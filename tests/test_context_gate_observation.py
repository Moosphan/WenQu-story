import json

import pytest
from test_workflow import make


@pytest.mark.parametrize('gate', ['financial_budget', 'transport_bytes'])
def test_shadow_observation_is_saved_at_existing_prelease_gates(tmp_path, monkeypatch, gate):
    service, book = make(tmp_path, budget_tokens=1 if gate == 'financial_budget' else 2000000)
    if gate == 'transport_bytes':
        monkeypatch.setattr(service, '_input', lambda *args: {'instruction': '大' * 70000})
    result = service.next_task(book)
    assert not result.get('task_id')
    with service.store.read() as conn:
        observed = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM events WHERE book_id=? AND kind='context_blocked'", (book,))]
    assert len(observed) == 1
    assert observed[0]['gate'] == gate
    assert observed[0]['context']['mode'] == 'shadow'
    assert observed[0]['context']['executable']
