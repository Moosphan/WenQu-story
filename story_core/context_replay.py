"""Offline prompt replay. No provider imports, network or paid generation.

JSONL accepts task-shaped rows with optional expected_hard_ids, reported_tokens,
truncated_output and chapters. Reports contain sizes/IDs, never input prose.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path

from .context_compiler import ContextCompiler, ContextPolicy, SOFT_TARGETS
from .schemas import SCHEMAS
from .storage import dumps


def synthetic_samples(sizes=(100, 300, 500)):
    for size in sizes:
        history = [{'kind': 'fact', 'key': f'旧事{i}', 'fact_id': f'history-{i}',
                    'value': '主角在古城修炼，见过商人与同门。' * 12, 'evidence': '主角走过古城。',
                    'context_reason': 'participant', 'source': {'chapter_number': i, 'version_id': f'v{i}'}}
                   for i in range(1, size)]
        # A labeled long-distance dependency: an exact identity, not just a name.
        debt = {'kind': 'fact', 'key': '铜牌债务', 'fact_id': 'debt-12', 'value': '铜牌仍借给商人，尚未归还。',
                'evidence': '铜牌借你，重逢归还。', 'source': {'chapter_number': 12, 'version_id': 'v12'}}
        promises = [{'key': f'伏笔{i}', 'due_chapter': size + i, 'setup_chapter': i, 'resolution': '解释原委'} for i in range(1, size)]
        promises.append({'key': '铜牌债务', 'promise_id': 'promise-12', 'due_chapter': size, 'setup_chapter': 12, 'resolution': '归还铜牌'})
        data = {'instruction': '遵守本章计划与硬约束，根据来源证据写作；不明事项不得编造。', 'chapter_number': size,
                'brief': {'premise': '主角沿旧路重返古城追索失物。', 'pov': '限知', 'style': '具体克制',
                          'characters': [{'name': '主角', 'boundary': '守约'}, *({'name': f'路人{i}', 'boundary': '谨慎'} for i in range(size))]},
                'chapter_plan': {'goal': '重返古城向商人索还铜牌', 'participants': ['主角'], 'pov': '主角',
                                 'required_fact_ids': ['debt-12'], 'promise_ids': ['promise-12']},
                'required_memory': [*history, debt], 'planned_promises': promises,
                'promise_obligations': [{'key': '铜牌债务', 'promise_id': 'promise-12', 'urgency': 'due'}],
                'recent_chapters': [{'chapter_number': size-1, 'version_id': f'v{size-1}', 'body': '他走向古城城门。' * 180}]}
        for stage in ('draft', 'revise', 'extract', 'continuity', 'reader', 'arc', 'ending'):
            context = dict(data)
            if stage in ('revise', 'extract', 'continuity', 'reader'):
                context['candidate'] = {'title': '故城', 'body': '他向商人伸出手，索回了寄放多年的铜牌。' * 50}
            if stage == 'extract':
                context['existing_memory_keys'] = [{'key': m['key'], 'kind': m['kind']} for m in history]
            if stage == 'reader':
                context['reader_memory'] = context.pop('required_memory')
            yield {'sample_id': f'synthetic-{size}-{stage}', 'chapters': size, 'stage': stage,
                   'input': context, 'output_schema': SCHEMAS[stage], 'expected_hard_ids': ['debt-12', 'promise-12']}


def _present_ids(data):
    ids = set()
    if isinstance(data, dict):
        ids.update(str(data[key]) for key in ('fact_id', 'promise_id', 'id') if data.get(key))
        for value in data.values(): ids.update(_present_ids(value))
    elif isinstance(data, list):
        for value in data: ids.update(_present_ids(value))
    return ids


def replay(samples, context_window=64000):
    rows = []
    billed, bill_count, truncated, expected_count, missing_count = 0, 0, 0, 0, 0
    for index, sample in enumerate(samples):
        stage = sample.get('stage', 'draft')
        policy = ContextPolicy(context_window=context_window, soft_target=SOFT_TARGETS.get(stage, 24000))
        shadow = ContextCompiler(policy).compile(sample['input'], sample.get('output_schema', {}), stage=stage)
        adaptive = ContextCompiler(replace(policy, mode='adaptive')).compile(sample['input'], sample.get('output_schema', {}), stage=stage)
        expected = set(sample.get('expected_hard_ids', []))
        missing = sorted(expected - _present_ids(adaptive.input))
        expected_count += len(expected); missing_count += len(missing)
        amount = sample.get('reported_tokens')
        if type(amount) is int and amount >= 0:
            billed += amount; bill_count += 1
        truncated += sample.get('truncated_output') is True
        rows.append({'sample_id': sample.get('sample_id', str(index)), 'chapters': sample.get('chapters'), 'stage': stage,
                     'full_history_tokens': shadow.diagnostics['final_tokens'], 'shadow': shadow.diagnostics,
                     'adaptive': adaptive.diagnostics, 'missing_expected_hard_ids': missing})
    return {'version': 'offline-context-replay-v1', 'samples': rows, 'summary': {
        'sample_count': len(rows), 'real_model_calls': 0,
        'organization_trigger_rate': sum(r['shadow']['organization_triggered'] for r in rows)/len(rows) if rows else None,
        'adaptive_unexecutable_count': sum(not r['adaptive']['executable'] for r in rows),
        'adaptive_missing_hard_count': missing_count,
        'key_fact_omission_rate': missing_count/expected_count if expected_count else None,
        'observed_truncated_outputs': truncated, 'provider_reported_tokens': billed if bill_count else None,
        'provider_usage_samples': bill_count,
        'limitations': ['Synthetic end-of-history snapshots, not 500 generated chapters or prose quality validation.',
                       'Fallback token counts are conservative uncalibrated estimates.',
                       'Recall covers only explicitly labeled dependencies; semantic/vector recall not measured.']}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--synthetic', action='store_true')
    source.add_argument('--input', type=Path, help='Authorized JSONL task snapshots (read only)')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--context-window', type=int, default=64000, help='Test fixture capacity, not a model claim')
    args = parser.parse_args(argv)
    samples = synthetic_samples() if args.synthetic else (json.loads(line) for line in args.input.read_text().splitlines() if line.strip())
    report = replay(samples, args.context_window)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(dumps(report['summary']))


if __name__ == '__main__':
    main()
