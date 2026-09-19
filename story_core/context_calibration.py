"""Offline analysis of explicitly supplied provider usage JSONL; never calls models."""
import argparse
import json
from pathlib import Path


def _count(value):
    return value if type(value) is int and value >= 0 else None


def usage_breakdown(raw, dialect):
    raw = raw if isinstance(raw, dict) else {}
    if dialect == 'compatible':
        incoming = _count(raw.get('prompt_tokens'))
        outgoing = _count(raw.get('completion_tokens'))
        details = raw.get('prompt_tokens_details')
        cached = _count(details.get('cached_tokens')) if isinstance(details, dict) else None
        total = _count(raw.get('total_tokens'))
        if incoming is not None and ((cached is not None and cached > incoming) or
                (total is not None and (total < incoming or
                 (outgoing is not None and total != incoming + outgoing)))):
            incoming = None
    elif dialect == 'native':
        counts = [_count(raw.get(key)) for key in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')]
        incoming = sum(counts) if all(value is not None for value in counts) else None
        outgoing = _count(raw.get('output_tokens'))
        cached = _count(raw.get('cache_read_input_tokens'))
    else:
        raise ValueError('Unknown usage dialect')
    return {'input_tokens': incoming, 'output_tokens': outgoing, 'cached_input_tokens': cached}


def _label(value):
    return value if isinstance(value, str) and 0 < len(value) <= 200 else None


def calibration_report(records):
    calls, conflicts = {}, set()
    duplicates, invalid = 0, 0
    for row in records:
        if not isinstance(row, dict) or not _label(row.get('call_id')):
            invalid += 1
            continue
        context = row.get('context') if isinstance(row.get('context'), dict) else {}
        usage = row.get('usage_breakdown') if isinstance(row.get('usage_breakdown'), dict) else {}
        execution = row.get('execution') if isinstance(row.get('execution'), dict) else {}
        value = {'executor': _label(execution.get('executor') or row.get('executor')),
                 'model': _label(context.get('model')), 'counter': _label(context.get('counter')),
                 'estimate': _count(context.get('final_tokens')), 'actual': _count(usage.get('input_tokens')),
                 'fingerprint': _label(context.get('fingerprint')),
                 'stage': _label(context.get('stage')), 'output': _count(usage.get('output_tokens')),
                 'output_reserve': _count(context.get('output_reserve'))}
        if 'models' in execution and execution['models'] != [value['model']]:
            value['actual'] = None
        identity = row['call_id']
        if identity in calls:
            duplicates += 1
            if calls[identity] != value:
                conflicts.add(identity)
        else:
            calls[identity] = value
    groups, eligible = {}, 0
    for identity, value in calls.items():
        required = ('executor', 'model', 'counter', 'estimate', 'actual', 'fingerprint')
        if identity in conflicts or any(value[key] is None for key in required) or not value['estimate']:
            continue
        eligible += 1
        key = (value['executor'], value['model'], value['counter'])
        group = groups.setdefault(key, {'executor': key[0], 'model': key[1], 'counter': key[2],
            'calls': 0, 'underestimated_calls': 0, 'estimated_input_tokens': 0, 'reported_input_tokens': 0,
            'max_actual_to_estimate': 0, 'max_underestimate_tokens': 0,
            'observed_extra_input_tokens': 0, 'stages': [], 'output_samples': 0,
            'max_output_tokens': None, 'outputs_at_reserve': 0, 'capacity_validated': False})
        group['calls'] += 1
        group['underestimated_calls'] += value['actual'] > value['estimate']
        group['estimated_input_tokens'] += value['estimate']
        group['reported_input_tokens'] += value['actual']
        group['max_actual_to_estimate'] = max(group['max_actual_to_estimate'], value['actual'] / value['estimate'])
        group['max_underestimate_tokens'] = max(group['max_underestimate_tokens'], value['actual'] - value['estimate'])
        group['observed_extra_input_tokens'] = group['max_underestimate_tokens']
        if value['stage'] is not None:
            group['stages'] = sorted(set(group['stages']) | {value['stage']})
        if value['output'] is not None:
            group['output_samples'] += 1
            group['max_output_tokens'] = max(group['max_output_tokens'] or 0, value['output'])
            group['outputs_at_reserve'] += (value['output_reserve'] is not None
                                           and value['output'] >= value['output_reserve'])
    return {'version': 'offline-calibration-v1', 'unique_calls': len(calls), 'eligible_calls': eligible,
        'ineligible_calls': len(calls) - eligible, 'invalid_records': invalid, 'duplicate_calls': duplicates,
        'conflicting_calls': len(conflicts), 'groups': [groups[key] for key in sorted(groups)],
        'policy_changed': False, 'real_model_calls': 0,
        'limitations': ['Observed input usage only; total tokens never substitute for prompt tokens.',
            'Native cache fields must all be reported; missing fields remain unknown.',
            'Reported host models must match the estimated model exactly; multi-model aggregates are excluded.',
            'Supplied call identity and usage provenance are trusted, not independently authenticated.',
            'Observed extra input includes tokenizer/template differences, not an isolated measurement of hidden instructions.',
            'Ratios describe supplied samples only, not validated capacity, pricing, or a safe reduction of reserves.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Authorized JSONL provider_usage payloads')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        parser.error('Input and output must be different files')
    with args.input.open(encoding='utf-8') as source:
        report = calibration_report(json.loads(line) for line in source if line.strip())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('unique_calls', 'eligible_calls', 'conflicting_calls', 'policy_changed')}))


if __name__ == '__main__':
    main()
