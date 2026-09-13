import pytest
from story_core.errors import StoryError


def test_exact_patches_are_atomic_and_preserve_other_text():
    from story_core.targeted_revision import materialize
    base={'title':'章','body':'甲段。\n乙段。\n丙段。'}
    assert materialize({'patches':[{'before':'乙段。','after':'新乙段。'}]},base)['body']=='甲段。\n新乙段。\n丙段。'
    assert base['body']=='甲段。\n乙段。\n丙段。'
    for patches in ([{'before':'不存在','after':'新'}],[{'before':'段','after':'新'}],[{'before':'甲段。','after':'新'},{'before':'甲段','after':'新'}]):
        with pytest.raises(StoryError): materialize({'patches':patches},base)


def test_service_accepts_patch_replay_and_retains_validation(tmp_path):
    from test_workflow import make,finish
    service,book=make(tmp_path,chapters=1);finish(service,book)
    c=service.get_book(book)['chapters'][0]
    service.control(book,'revise',chapter_number=1,title=c['title'],body=c['body'],feedback='修改开头')
    task=service.next_task(book)
    result={'patches':[{'before':c['body'][:20],'after':'修改后的开头。妹妹仍旧'}]}
    response=service.submit_task(task['task_id'],task['lease_id'],result)
    assert service.submit_task(task['task_id'],task['lease_id'],result)==response
    assert service.status(book)['run']['candidate']['body'].startswith('修改后的开头。')
