"""Synthetic SQLite growth benchmark. No provider, credentials or manuscript input.

One cumulative trajectory: every chapter and seven task stages, with checkpoints
at 100/300/500. This measures context compilation and lexical recall, not generated
prose, actual service task transitions, embeddings, or true provider billing.
"""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from .context_compiler import ContextCompiler, ContextPolicy, SOFT_TARGETS
from .context_replay import _present_ids
from .memory import index_chapter
from .retrieval import RetrievalScope, SQLiteRetriever
from .schemas import SCHEMAS
from .storage import Store, dumps

STAGES = ('draft', 'revise', 'extract', 'continuity', 'reader', 'arc', 'ending')


def benchmark(chapters=500):
    if type(chapters) is not int or not 1 <= chapters <= 500:
        raise ValueError('chapters must be 1–500')
    points = []
    summary = {'real_model_calls': 0, 'provider_reported_tokens': None, 'semantic_recall': None,
               'hard_labels': 0, 'bounded_hard_missing': 0, 'recent_only_hard_missing': 0,
               'exact_recall': {'matched': 0, 'labels': 0}, 'alias_recall': {'matched': 0, 'labels': 0},
               'bounded_unexecutable': 0, 'indexed_memory_count': 0}
    with TemporaryDirectory(prefix='wenqu-synthetic-benchmark-') as directory:
        store = Store(directory)
        book = store.create_book('Synthetic fixture', 'Synthetic fixture', {})['book_id']
        retriever = SQLiteRetriever(store)
        debt = None
        for number in range(1, chapters + 1):
            with store.read() as conn:
                history = [{**json.loads(row['data']), 'id': row['id'], 'source': {'chapter_number': row['chapter_number'], 'version_id': row['version_id']}} for row in conn.execute('''SELECT m.* FROM memories m JOIN chapters c ON c.book_id=m.book_id AND c.number=m.chapter_number AND c.version_id=m.version_id WHERE m.book_id=? AND m.chapter_number<? AND c.status='committed' ORDER BY m.chapter_number,m.id''', (book, number))]
            start = perf_counter()
            found = retriever.search('铜牌旧约', RetrievalScope(book, number - 1))
            retrieval_ms = (perf_counter() - start) * 1000
            if debt:
                for name, query in (('exact_recall', '铜牌旧约'), ('alias_recall', '青蚨契')):
                    hits = found if name == 'exact_recall' else retriever.search(query, RetrievalScope(book, number - 1))
                    summary[name]['labels'] += 1
                    summary[name]['matched'] += any(hit['id'] == debt for hit in hits['hits'])
            for stage in STAGES:
                data = {'chapter_number': number, 'instruction': '依据本章计划与来源写作，不明事项保持未知。',
                        'chapter_plan': {'goal': '沿旧路重返古城', 'participants': ['主角'], 'required_fact_ids': [debt] if debt else []},
                        'brief': {'premise': '守约与失物', 'style': '具体克制'},
                        'required_memory': [dict(item, context_reason='lexical_supplement') for item in history]}
                if stage in ('revise', 'extract', 'continuity', 'reader'):
                    data['candidate'] = {'title': '故城', 'body': '他沿河岸向城门走去，夜色渐深。' * 30}
                schema = SCHEMAS[stage]
                full = ContextCompiler(ContextPolicy(mode='off', context_window=64000)).compile(data, schema, stage=stage)
                start = perf_counter()
                packed = ContextCompiler(ContextPolicy(mode='adaptive', context_window=64000,
                    soft_target=SOFT_TARGETS[stage])).compile(data, schema, stage=stage)
                compile_ms = (perf_counter() - start) * 1000
                # A deliberately simple source-summary baseline: five recent
                # original evidence excerpts, never an invented semantic summary.
                recent_data = {**data, 'required_memory': [], 'chapter_plan': {'goal': data['chapter_plan']['goal']},
                    'source_excerpts': [{'id': item['id'], 'evidence': item['evidence']} for item in history[-5:]]}
                recent = ContextCompiler(ContextPolicy(mode='off', context_window=64000)).compile(recent_data, schema, stage=stage)
                expected = {debt} if debt else set()
                missing = len(expected - _present_ids(packed.input))
                recent_missing = len(expected - _present_ids(recent.input))
                summary['hard_labels'] += len(expected)
                summary['bounded_hard_missing'] += missing
                summary['recent_only_hard_missing'] += recent_missing
                summary['bounded_unexecutable'] += not packed.executable
                points.append({'chapter': number, 'stage': stage, 'full_history_tokens': full.diagnostics['final_tokens'],
                    'bounded_tokens': packed.diagnostics['final_tokens'], 'recent_excerpt_tokens': recent.diagnostics['final_tokens'],
                    'hard_missing': missing, 'recent_only_hard_missing': recent_missing,
                    'bounded_executable': packed.executable, 'index_complete': found['index_complete'],
                    'candidates_examined': found['candidates_examined'],
                    'retrieval_ms': round(retrieval_ms, 3), 'compile_ms': round(compile_ms, 3)})
            version = f'fixture-version-{number}'
            quote = '铜牌借你，重逢归还。' if number == 12 else '主角见过商人与同门。'
            memory = {'kind': 'entity' if number == 12 else 'fact', 'key': '铜牌旧约' if number == 12 else f'故城旧事{number}',
                      'value': quote if number == 12 else quote * 30, 'evidence': quote, 'visibility': 'reader',
                      **({'aliases': ['青蚨契']} if number == 12 else {})}
            with store.write(book) as conn:
                conn.execute('INSERT INTO chapter_versions VALUES (?,?,?,?,?,?)', (version, book, number, 'Synthetic', quote * 40, 0))
                conn.execute("INSERT INTO chapters VALUES (?,?,?,'committed')", (book, number, version))
                index_chapter(conn, book, number, version, quote * 40, [memory])
                if number == 12:
                    debt = conn.execute('SELECT id FROM memories WHERE version_id=?', (version,)).fetchone()[0]
            with store.read() as conn:
                summary['indexed_memory_count'] = conn.execute('SELECT count(*) FROM memory_indexed WHERE book_id=?', (book,)).fetchone()[0]
    checkpoints = []
    for number in (100, 300, 500):
        subset = [point for point in points if point['chapter'] == number]
        if subset:
            checkpoints.append({'chapter': number, 'full_history_range': [min(p['full_history_tokens'] for p in subset), max(p['full_history_tokens'] for p in subset)],
                'bounded_range': [min(p['bounded_tokens'] for p in subset), max(p['bounded_tokens'] for p in subset)]})
    return {'version': 'synthetic-sqlite-growth-v1', 'summary': summary, 'checkpoints': checkpoints, 'points': points,
        'limitations': ['One cumulative synthetic trajectory; seven compiler stages per chapter, not a full model/workflow replay.',
            'Hard ID preservation and literal/registered-alias retrieval labels only; no semantic quality or 95% recall claim.',
            'Recent excerpts baseline is deterministic; vector and AI summaries are unavailable, not fabricated.',
            'Conservative uncalibrated token estimates; timings depend on this machine, no billed cost estimate.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chapters', type=int, default=500)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = benchmark(args.chapters)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(dumps({'summary': result['summary'], 'checkpoints': result['checkpoints']}))


if __name__ == '__main__':
    main()
