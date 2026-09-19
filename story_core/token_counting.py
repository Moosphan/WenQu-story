"""Opt-in local tokenizer artifacts; never download models or trust remote code.

Tokenizing a serialized transport envelope is still an estimate: the provider's
chat template and hidden instructions are not implied by its vocabulary file.
"""
import hashlib
from pathlib import Path
import re

from .errors import StoryError


def validate_profiles(profiles):
    if profiles is None:
        return
    if not isinstance(profiles, dict):
        raise StoryError('INVALID_TOKENIZER_PROFILE', '分词器配置必须按准确模型名称映射。')
    for model, profile in profiles.items():
        if (not isinstance(model, str) or not model.strip() or not isinstance(profile, dict)
                or set(profile) != {'path', 'sha256'}
                or not isinstance(profile['path'], str) or not Path(profile['path']).is_absolute()
                or not isinstance(profile['sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', profile['sha256'])):
            raise StoryError('INVALID_TOKENIZER_PROFILE', '分词器须提供绝对路径及 SHA-256，按准确模型名称配置。')


class LocalTokenizerCounter:
    def __init__(self, profile):
        try:
            payload = Path(profile['path']).read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if digest != profile['sha256']:
                raise ValueError('checksum mismatch')
            from tokenizers import Tokenizer
            self.tokenizer = Tokenizer.from_buffer(payload)
            self.tokenizer.no_truncation()
            self.tokenizer.no_padding()
        except Exception:
            # Native tokenizer loading errors vary by version. Do not include
            # local file contents or paths in the provider-facing error.
            raise StoryError('INVALID_TOKENIZER_PROFILE',
                             '本地分词器无法载入或校验失败，请检查文件和 tokenizers 依赖。') from None
        self.method = 'local-tokenizer-envelope-estimate:' + digest

    def count(self, text):
        from .context_compiler import TokenCount
        return TokenCount(len(self.tokenizer.encode(text, add_special_tokens=False).ids), True, self.method)


def counter_for_model(profiles, model):
    from .context_compiler import ConservativeCounter
    validate_profiles(profiles)
    profile = (profiles or {}).get(model)
    return LocalTokenizerCounter(profile) if profile else ConservativeCounter()
