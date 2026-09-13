from copy import deepcopy


def review(task, issues, **kw):
    return dict(task_id=task, run_id='run', chapter_number=1, stage='continuity',
                base_revision=2, source='model', result={'issues': issues}, **kw)


def issue(evidence='他已知道秘密', dimension='角色知情'):
    return dict(evidence=evidence, dimension=dimension, severity='major', explanation='原因', suggestion='修改')


def test_tracks_repeat_absence_and_reappearance_without_claiming_resolved():
    from story_core.review_tracking import annotate_reviews
    rows=[review('a',[issue()]),review('b',[issue()]),review('c',[]),review('d',[issue()])]
    original=deepcopy(rows)
    result=annotate_reviews('book',rows)
    first=result[0]['issue_tracking']['current'][0]
    assert first['state']=='new'
    assert result[1]['issue_tracking']['current'][0]['state']=='repeated'
    assert result[2]['issue_tracking']['not_repeated'][0]['issue_id']==first['issue_id']
    assert result[3]['issue_tracking']['current'][0]['state']=='reappeared'
    assert result[3]['issue_tracking']['current'][0]['issue_id']==first['issue_id']
    assert rows==original


def test_different_reviewers_and_changed_evidence_are_not_falsely_merged():
    from story_core.review_tracking import annotate_reviews
    a=review('a',[issue()]);b=review('b',[issue('他尚不知道秘密')]);c=review('c',[issue()],reader_profile='casual')
    result=annotate_reviews('book',[a,b,c])
    assert all(row['issue_tracking']['current'][0]['state']=='new' for row in result)
    assert result[2]['issue_tracking']['previous_task_id'] is None
    assert annotate_reviews('other',[a])[0]['issue_tracking']['current'][0]['issue_id']!=result[0]['issue_tracking']['current'][0]['issue_id']


def test_service_returns_tracking_with_stable_ids_across_limit(tmp_path):
    from test_workflow import make, finish
    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    full = service.review_history(book, include_history=True)
    assert full
    assert all('issue_tracking' in row for row in full if row['source']=='model')
    assert service.review_history(book, include_history=True, limit=1)==full[-1:]
