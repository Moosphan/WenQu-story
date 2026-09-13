"""Optional Microsoft Edge TTS synthesis and immutable chapter audio cache."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import threading
from pathlib import Path

from .errors import StoryError


VOICE_CATALOG = (
    {'id': 'zh-CN-YunxiNeural', 'label': '云希 · 男声', 'locale': '中文（普通话）', 'style': '自然叙述'},
    {'id': 'zh-CN-XiaoxiaoNeural', 'label': '晓晓 · 女声', 'locale': '中文（普通话）', 'style': '自然叙述'},
    {'id': 'zh-CN-YunjianNeural', 'label': '云健 · 男声', 'locale': '中文（普通话）', 'style': '沉稳有力'},
    {'id': 'zh-CN-XiaoyiNeural', 'label': '晓伊 · 女声', 'locale': '中文（普通话）', 'style': '温柔清晰'},
    {'id': 'zh-CN-YunyangNeural', 'label': '云扬 · 男声', 'locale': '中文（普通话）', 'style': '新闻播报'},
    {'id': 'zh-CN-YunxiaNeural', 'label': '云夏 · 男声', 'locale': '中文（普通话）', 'style': '普通话'},
    {'id': 'zh-CN-liaoning-XiaobeiNeural', 'label': '晓北 · 女声 · 东北话', 'locale': '中文（辽宁）', 'style': '方言'},
    {'id': 'zh-CN-shaanxi-XiaoniNeural', 'label': '晓妮 · 女声 · 陕西话', 'locale': '中文（陕西）', 'style': '方言'},
    {'id': 'zh-HK-HiuGaaiNeural', 'label': '晓佳 · 女声 · 粤语', 'locale': '中文（香港）', 'style': '粤语'},
    {'id': 'zh-HK-HiuMaanNeural', 'label': '晓曼 · 女声 · 粤语', 'locale': '中文（香港）', 'style': '粤语'},
    {'id': 'zh-HK-WanLungNeural', 'label': '云龙 · 男声 · 粤语', 'locale': '中文（香港）', 'style': '粤语'},
    {'id': 'zh-TW-HsiaoChenNeural', 'label': '晓臻 · 女声 · 台湾国语', 'locale': '中文（台湾）', 'style': '台湾国语'},
    {'id': 'zh-TW-HsiaoYuNeural', 'label': '晓雨 · 女声 · 台湾国语', 'locale': '中文（台湾）', 'style': '台湾国语'},
    {'id': 'zh-TW-YunJheNeural', 'label': '云哲 · 男声 · 台湾国语', 'locale': '中文（台湾）', 'style': '台湾国语'},
)
VOICE_IDS = {item['id'] for item in VOICE_CATALOG}


def _rate_string(rate: float) -> str:
    return f"{(float(rate) - 1.0) * 100:+.0f}%"


class EdgeTTSService:
    def __init__(self, root, synthesizer=None):
        self.cache_root = Path(root).expanduser().resolve() / 'tts-cache'
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.synthesizer = synthesizer
        self._lock = threading.Lock()

    @staticmethod
    def voices():
        return [dict(item) for item in VOICE_CATALOG]

    def _validate(self, voice: str, rate: float):
        if voice not in VOICE_IDS:
            raise StoryError('TTS_INVALID_VOICE', '不支持的 Edge TTS 音色。', {'voice': voice, 'voices': sorted(VOICE_IDS)})
        if type(rate) not in (int, float) or not 0.5 <= float(rate) <= 2.0:
            raise StoryError('TTS_INVALID_RATE', '语速需要在 0.5–2.0 倍之间。', {'rate': rate, 'min': 0.5, 'max': 2.0})

    async def _default_synthesizer(self, text: str, voice: str, rate: str, output: Path):
        try:
            edge_tts = importlib.import_module('edge_tts')
        except ImportError:
            raise StoryError('TTS_UNAVAILABLE', '未安装 Edge TTS。请安装 hulk-story-agent[tts] 后重试。') from None
        communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
        await communicate.save(str(output))

    async def audio_path(self, book_id: str, chapter_number: int, version_id: str, text: str,
                         voice: str = 'zh-CN-YunxiNeural', rate: float = 1.0) -> Path:
        if not isinstance(text, str) or not text.strip():
            raise StoryError('TTS_EMPTY_TEXT', '本章没有可播放的正文。')
        if len(text.encode('utf-8')) > 2_000_000:
            raise StoryError('TTS_TEXT_TOO_LARGE', '单章正文超过 2 MB，暂不生成听书音频。', {'max_bytes': 2_000_000})
        self._validate(voice, rate)
        normalized_rate = round(float(rate), 3)
        digest = hashlib.sha256('|'.join((book_id, str(chapter_number), version_id, voice,
                                          str(normalized_rate), text)).encode('utf-8')).hexdigest()
        path = self.cache_root / book_id / f'{digest}.mp3'
        if path.is_file() and path.stat().st_size:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        # The lock prevents two browser tabs from corrupting the same MP3. The
        # network await is intentionally outside SQLite and does not block story state.
        with self._lock:
            if path.is_file() and path.stat().st_size:
                return path
            temporary = path.with_suffix('.part')
            try:
                synthesizer = self.synthesizer or self._default_synthesizer
                await synthesizer(text, voice, _rate_string(normalized_rate), temporary)
                if not temporary.is_file() or not temporary.stat().st_size:
                    raise StoryError('TTS_EMPTY_AUDIO', 'Edge TTS 未返回有效音频。')
                temporary.replace(path)
            except StoryError:
                temporary.unlink(missing_ok=True)
                raise
            except Exception as error:
                temporary.unlink(missing_ok=True)
                error_type = type(error).__name__
                details = {'type': error_type, 'voice': voice}
                if error_type == 'NoAudioReceived':
                    raise StoryError('TTS_EMPTY_AUDIO', '微软未返回音频；当前音色可能暂不可用，请切换云希或晓晓后重试。', details) from None
                if isinstance(error, TimeoutError):
                    raise StoryError('TTS_FAILED', '微软语音服务响应超时，请稍后重试。', details) from None
                raise StoryError('TTS_FAILED', f'语音服务连接失败（{error_type}），请稍后重试。', details) from None
        return path
