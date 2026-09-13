"""Revision explanations are author-facing claims, never automatic review passes."""
import hashlib
import json
import re
from .errors import StoryError


def feedback_items(reviews):
    items = {}
    for review in reviews or []:
        sources = review.get('issues', [])
        if review.get('stage') == 'author' and review.get('notes'):
            sources = [*sources, {'dimension': '作者要求', 'evidence': '', 'suggestion': review['notes']}]
        for issue in sources:
            item = {'source': review.get('stage'), 'dimension': issue.get('dimension', ''),
                    'evidence': issue.get('evidence', ''), 'request': issue.get('suggestion', '')}
            key = 'F-' + hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
            items[key] = {'feedback_id': key, **item, 'severity': issue.get('severity'),
                          'repair_target': review.get('repair_target', 'manuscript')}
    return list(items.values())


def validate_response(result, items):
    if 'revision_response' not in result:  # Older hosts may still return title/body only.
        return
    responses = result['revision_response']
    ids = [row['feedback_id'] for row in responses]
    if len(ids) != len(set(ids)) or set(ids) != {item['feedback_id'] for item in items}:
        raise StoryError('INVALID_REVISION_RESPONSE', '返修说明必须逐项对应本次修改要求，不得重复或遗漏。')
    for row in responses:
        if row['status'] == 'changed' and (not row['evidence'].strip() or not quote_present(row['evidence'], result['body'])):
            raise StoryError('INVALID_REVISION_RESPONSE', '返修说明的修改后引文必须出现在候选正文中。')


def validate_verification(result, context, candidate):
    """Check coverage and quotations, not the truth of a model's judgment."""
    if 'revision_verification' not in result:
        return
    rows = result['revision_verification']
    expected = {item['feedback_id'] for item in (context or {}).get('feedback_items', [])}
    ids = [row['feedback_id'] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise StoryError('INVALID_REVISION_VERIFICATION', '核验记录必须逐项对应当前返修要求，不得遗漏、重复或引用其他版本。')
    body = (candidate or {}).get('body', '')
    for row in rows:
        if row['status'] == 'verified' and (not row['evidence'].strip() or row['evidence'] not in body):
            raise StoryError('INVALID_REVISION_VERIFICATION', '已核验项必须引用当前候选正文中的原句。')
        if row['evidence'] and row['evidence'] not in body:
            raise StoryError('INVALID_REVISION_VERIFICATION', '核验引文不在当前候选正文中。')


def unresolved_hard_requirements(result, task_input):
    items = {item['feedback_id']: item for item in (task_input.get('revision_check') or {}).get('feedback_items', [])}
    unresolved = []
    for row in result.get('revision_verification', []):
        item = items.get(row['feedback_id'], {})
        hard = item.get('severity') == 'blocker' or (item.get('severity') == 'major' and item.get('source') != 'reader')
        if hard and row['status'] == 'unresolved':
            unresolved.append(row['feedback_id'])
    return unresolved


def quote_present(evidence, body):
    if evidence in body:
        return True
    parts = [part.strip() for part in re.split(r'[；;/\n]+', evidence) if part.strip()]
    return len(parts) > 1 and all(part in body for part in parts)
