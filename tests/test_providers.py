import json
import httpx
import pytest
from test_workflow import make, result_for


def test_provider_uses_task_only_json_and_bounded_output():
    from story_core.providers import OpenAICompatible
    captured=[]
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}], 'usage':{'total_tokens':42}})
    provider=OpenAICompatible('http://localhost/v1','test-model','secret',client=httpx.Client(transport=httpx.MockTransport(respond)))
    answer=provider.generate({'input':{'instruction':'只评读者已读部分','candidate':'文本'},'output_schema':{'type':'object'}})
    assert answer=={'ok':True}
    assert captured[0]['max_tokens']<=12000
    assert 'tools' not in captured[0]
    assert provider.last_usage==42


def test_truncated_output_is_never_submitted():
    from story_core.providers import OpenAICompatible
    from story_core.errors import StoryError
    client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})))
    with pytest.raises(StoryError,match='截断'):
        OpenAICompatible('http://localhost/v1','test','key',client=client).generate({'input':{},'output_schema':{}})


def test_worker_finishes_persisted_workflow(tmp_path):
    from story_core.providers import run_worker
    service,book=make(tmp_path)
    class FixtureProvider:
        def generate(self,task): return result_for(task)
    result=run_worker(service,book,FixtureProvider())
    assert result['status']=='complete'
    assert service.export(book)['complete']


def test_provider_failure_pauses_without_losing_task(tmp_path):
    from story_core.providers import run_worker
    from story_core.errors import StoryError
    service,book=make(tmp_path)
    class Broken:
        def generate(self,task): raise StoryError('PROVIDER_ERROR','不可用')
    with pytest.raises(StoryError): run_worker(service,book,Broken())
    assert service.status(book)['run']['status']=='paused'
    assert service.get_book(book)['brief'] is None
