import httpx
import pytest
from story_core.providers import OpenAICompatible
from story_core.errors import StoryError


@pytest.mark.parametrize('status,category', [(401,'authentication'),(403,'permission'),(404,'endpoint_or_model'),(429,'rate_limit'),(503,'upstream')])
def test_http_failure_explains_action_without_body(status, category):
    client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(status,text='secret-body')))
    with pytest.raises(StoryError) as caught:
        OpenAICompatible('http://localhost/v1','fixture','secret-key',client=client).generate({'input':{},'output_schema':{}})
    error=caught.value.as_dict()
    assert error['details']['category']==category
    assert error['details']['phase']=='http_response'
    assert error['details']['next_action']
    assert 'secret' not in str(error)


@pytest.mark.parametrize('exception,phase', [(httpx.ConnectTimeout,'connect'),(httpx.ReadTimeout,'waiting_response'),(httpx.ConnectError,'connect')])
def test_transport_failure_distinguishes_connect_from_wait(exception,phase):
    def fail(request): raise exception('secret-url')
    client=httpx.Client(transport=httpx.MockTransport(fail))
    with pytest.raises(StoryError) as caught:
        OpenAICompatible('http://localhost/v1','fixture','key',client=client).generate({'input':{},'output_schema':{}})
    assert caught.value.details['phase']==phase
    assert caught.value.details['next_action']
    assert 'secret-url' not in str(caught.value.as_dict())
