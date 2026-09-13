"""Conservative, reproducible review comparison; absence is not proof of repair."""
from copy import deepcopy
import hashlib
import json


def annotate_reviews(book_id, reviews):
    rows = deepcopy(reviews)
    groups = {}
    for row in rows:
        if row.get('source') != 'model':
            continue
        # Never compare different readers, chapters, runs or planning baselines.
        scope = (book_id, row.get('run_id'), row.get('chapter_number'),
                 row.get('stage'), row.get('reader_profile'), row.get('base_revision'))
        history = groups.setdefault(scope, {'seen': set(), 'previous': {}, 'task': None})
        current = {}
        for issue in row['result'].get('issues', []):
            signature = tuple(''.join(str(issue.get(key, '')).split()) for key in ('dimension', 'evidence'))
            # Empty evidence cannot establish issue identity across reviews.
            identity = (*scope, *signature, row.get('task_id') if not signature[1] else None)
            issue_id = 'I-' + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:12]
            state = 'repeated' if issue_id in history['previous'] else 'reappeared' if issue_id in history['seen'] else 'new'
            current[issue_id] = {'issue_id': issue_id, 'state': state, 'dimension': issue.get('dimension', ''),
                                 'evidence': issue.get('evidence', '')}
        row['issue_tracking'] = {
            'method': 'exact-dimension-and-evidence-v1', 'previous_task_id': history['task'],
            'current': list(current.values()),
            'not_repeated': [item for key, item in history['previous'].items() if key not in current],
        }
        history['seen'].update(current)
        history['previous'], history['task'] = current, row.get('task_id')
    return rows
