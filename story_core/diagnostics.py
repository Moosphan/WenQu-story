"""Credential-safe diagnosis from observed transport facts, never raw response bodies."""
import httpx
from .errors import StoryError


def http_failure(status, code='PROVIDER_ERROR'):
    cases = {
        401: ('authentication', '模型服务拒绝鉴权。', '打开 AI 配置检查 API Key 是否有效及是否属于该服务商。'),
        403: ('permission', '模型服务拒绝访问。', '检查账户、模型权限与服务商访问限制。'),
        404: ('endpoint_or_model', '请求地址或模型不可用。', '核对接口地址和模型名称；仅凭 404 无法区分二者。'),
        429: ('rate_limit', '模型服务限制了当前请求。', '检查服务商额度与速率限制，确认后再重试；不要连续点击。'),
    }
    category, message, action = cases.get(status, ('upstream' if status >= 500 else 'request',
        '模型服务未接受或未完成请求。', '检查服务商状态及模型参数，确认恢复后重试当前阶段。'))
    return StoryError(code, message, {'status': status, 'category': category,
        'phase': 'http_response', 'next_action': action})


def transport_failure(error, code='PROVIDER_ERROR'):
    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
        phase, message = 'connect', '尚未建立模型服务连接。'
        action = '检查接口地址、网络和代理；这不是正文审稿未通过。'
    elif isinstance(error, httpx.ReadTimeout):
        phase, message = 'waiting_response', '等待模型响应超时，未收到完整结果。'
        action = '检查服务商状态与请求规模后重试；无法据此确认服务端是否已生成或计费。'
    else:
        phase, message = 'transport', '传输模型请求或响应时失败。'
        action = '检查网络与服务商状态后重试当前阶段。'
    return StoryError(code, message, {'category': 'transport', 'phase': phase,
        'type': type(error).__name__, 'next_action': action})
