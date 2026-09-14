import json
import os
import time
from urllib.parse import urlparse

import httpx

from .context_compiler import compile_task, request_envelope
from .errors import StoryError
from .model_json import parse_object
from .diagnostics import http_failure, transport_failure
from .service import MAX_TASK_OUTPUT_TOKENS
from .storage import dumps, uid


class OpenAICompatible:
    name = 'api'
    def __init__(self, base_url, model, api_key, client=None):
        parsed=urlparse(base_url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            raise StoryError('INVALID_PROVIDER','模型 API 地址无效。')
        if parsed.scheme=='http' and parsed.hostname not in ('localhost','127.0.0.1','::1'):
            raise StoryError('INVALID_PROVIDER','远程模型 API 必须使用 HTTPS。')
        if not model or not api_key:
            raise StoryError('PROVIDER_NOT_CONFIGURED','请配置 HULK_MODEL 和 HULK_API_KEY。')
        self.url=base_url.rstrip('/')+'/chat/completions'
        self.model=model
        self.api_key=api_key
        self.client=client
        self.last_usage=None

    @classmethod
    def from_env(cls):
        return cls(os.environ.get('HULK_BASE_URL','https://api.openai.com/v1'),
                   os.environ.get('HULK_MODEL',''),os.environ.get('HULK_API_KEY',''))

    def generate(self, task):
        self.last_usage = None
        self.last_context = None
        compiled = compile_task(task, output_reserve=MAX_TASK_OUTPUT_TOKENS, model=self.model)
        self.last_context = compiled.diagnostics
        compiled.require_executable()
        payload={'model':self.model, **request_envelope(compiled.input, task['output_schema']),
                 'response_format':{'type':'json_object'},'max_tokens':MAX_TASK_OUTPUT_TOKENS}
        headers={'Authorization':'Bearer '+self.api_key,'Content-Type':'application/json'}
        client=self.client or httpx.Client(timeout=httpx.Timeout(240,connect=15),follow_redirects=False)
        try:
            response=client.post(self.url,json=payload,headers=headers)
            if response.status_code!=200:
                raise http_failure(response.status_code)
            data=response.json()
            usage = data.get('usage', {}).get('total_tokens')
            self.last_usage = usage if type(usage) is int and usage >= 0 else None
            choice=data['choices'][0]
            if choice.get('finish_reason')=='length':
                raise StoryError('TRUNCATED_OUTPUT','模型输出被截断；请缩小任务篇幅。')
            if choice.get('finish_reason') not in ('stop',None):
                raise StoryError('PROVIDER_ERROR','模型未正常完成当前任务。')
            result=parse_object(choice['message']['content'], 'PROVIDER_ERROR')
            if not isinstance(result,dict):
                raise StoryError('INVALID_RESULT','模型没有返回 JSON 对象。')
            return result
        except httpx.HTTPError as error:
            raise transport_failure(error) from None
        except (KeyError,IndexError,TypeError,ValueError) as error:
            raise StoryError('PROVIDER_ERROR','模型响应无法解析或连接失败。',{'type':type(error).__name__, 'phase': 'decode_result', 'category': 'format', 'next_action': '模型返回内容不符合 JSON 结构；检查模型是否支持结构化输出，再重试当前阶段。'}) from None
        finally:
            if self.client is None: client.close()


def configured_provider(executor=None, settings=None):
    # A UI-saved connection wins over defaults, but callers can still explicitly
    # request a transport (CLI/MCP/tests) and environment-only deployments retain
    # their existing behavior.
    if executor is None:
        from .ai_config import AISettings
        saved = (settings or AISettings()).runtime()
        if saved:
            if saved['mode'] == 'claude':
                from .host import ClaudeCode
                return ClaudeCode(model=saved.get('model') or None, host_options=saved.get('host_options'))
            return OpenAICompatible(saved['base_url'], saved['model'], saved['api_key'])
    name = executor or os.environ.get('HULK_EXECUTOR', 'api')
    if name == 'claude':
        from .host import ClaudeCode
        return ClaudeCode()
    if name == 'api':
        return OpenAICompatible.from_env()
    raise StoryError('INVALID_PROVIDER', 'HULK_EXECUTOR 目前支持 api 或 claude。')


def run_worker(service, book_id, provider, worker_id=None):
    worker_id=worker_id or uid('api')
    while True:
        task=service.next_task(book_id,worker_id)
        if not task.get('task_id'):
            return task
        call = {'call_id': uid('call'), 'task_id': task['task_id'], 'worker_id': worker_id,
                'executor': getattr(provider, 'name', 'unknown'), 'started_at': time.time()}
        with service.store.write(book_id) as conn:
            service.store.event(conn, book_id, 'provider_call_started', call, task['run_id'])
        provider.last_usage = None
        provider.last_metadata = None
        provider.last_context = None
        outcome, error_code = 'failed', None
        diagnosis = None
        try:
            if getattr(provider, 'cancellable', False):
                last_heartbeat = 0.0

                def cancelled():
                    nonlocal last_heartbeat
                    now = time.monotonic()
                    if now - last_heartbeat >= 60:
                        if not service.renew_task_lease(task['task_id'], task['lease_id'], worker_id):
                            return True
                        last_heartbeat = now
                    return not service.task_active(task['task_id'], task['lease_id'])

                result = provider.generate(task, cancelled=cancelled)
            else:
                result=provider.generate(task)
            try:
                service.submit_task(task['task_id'],task['lease_id'],result,worker_id)
            except StoryError as error:
                # Pause/cancel can arrive while a remote request is finishing. Its
                # response must never be submitted into a stopped run, but it also
                # is not a provider failure to surface back to the author.
                if error.code == 'INVALID_LEASE' and not service.task_active(task['task_id'], task['lease_id']):
                    outcome = 'cancelled'
                    return service.status(book_id)['run']
                raise
            outcome = 'succeeded'
        except (StoryError,KeyboardInterrupt) as error:
            error_code = getattr(error, 'code', 'INTERRUPTED')
            diagnosis = {key: value for key, value in getattr(error, 'details', {}).items() if key in ('phase', 'category', 'next_action', 'status', 'type', 'errors')}
            service.fail_task(task['task_id'], task['lease_id'], error)
            raise
        except Exception:
            error = StoryError('WORKER_ERROR', '执行器异常，进度已保留，可检查宿主连接后恢复。')
            error_code = error.code
            service.fail_task(task['task_id'], task['lease_id'], error)
            raise error from None
        finally:
            usage = getattr(provider, 'last_usage', None)
            usage = usage if type(usage) is int and usage >= 0 else None
            with service.store.write(book_id) as conn:
                service.store.event(conn, book_id, 'provider_usage', {
                    **call, 'status': outcome, 'error_code': error_code, 'diagnosis': diagnosis,
                    'finished_at': time.time(), 'reported_tokens': usage,
                    'execution': getattr(provider, 'last_metadata', None),
                    'context': getattr(provider, 'last_context', None)}, task['run_id'])
