import asyncio

import pytest


def test_catalog_excludes_unsupported_edge_voices():
    from story_core.tts import VOICE_IDS
    assert not VOICE_IDS.intersection({'zh-CN-XiaochenNeural', 'zh-CN-XiaohanNeural',
        'zh-CN-XiaomengNeural', 'zh-CN-XiaoruiNeural', 'zh-CN-YunfengNeural'})


def test_no_audio_error_identifies_voice_without_claiming_network_failure(tmp_path):
    class NoAudioReceived(Exception):
        pass
    from story_core.errors import StoryError
    from story_core.tts import EdgeTTSService

    async def fake(*args):
        raise NoAudioReceived('No audio was received')

    with pytest.raises(StoryError) as failure:
        asyncio.run(EdgeTTSService(tmp_path, fake).audio_path('b', 1, 'v', '正文'))
    assert failure.value.code == 'TTS_EMPTY_AUDIO'
    assert '音色' in failure.value.message


def test_tts_cache_key_separates_versions_and_voice(tmp_path):
    from story_core.tts import EdgeTTSService

    calls = []

    async def fake(text, voice, rate, output):
        calls.append((text, voice, rate))
        output.write_bytes(f'{voice}|{rate}|{text}'.encode())

    service = EdgeTTSService(tmp_path, synthesizer=fake)
    first = asyncio.run(service.audio_path('book-1', 1, 'v1', '正文甲', 'zh-CN-YunxiNeural', 1.0))
    again = asyncio.run(service.audio_path('book-1', 1, 'v1', '正文甲', 'zh-CN-YunxiNeural', 1.0))
    second_version = asyncio.run(service.audio_path('book-1', 1, 'v2', '正文乙', 'zh-CN-YunxiNeural', 1.0))
    second_voice = asyncio.run(service.audio_path('book-1', 1, 'v1', '正文甲', 'zh-CN-XiaoxiaoNeural', 1.0))
    assert first == again
    assert first.read_bytes() == 'zh-CN-YunxiNeural|+0%|正文甲'.encode()
    assert second_version != first and second_voice != first
    assert len(calls) == 3


def test_tts_rejects_invalid_voice_and_rate(tmp_path):
    from story_core.errors import StoryError
    from story_core.tts import EdgeTTSService

    service = EdgeTTSService(tmp_path, synthesizer=lambda *args: None)
    with pytest.raises(StoryError) as voice:
        asyncio.run(service.audio_path('b', 1, 'v', '正文', 'unknown', 1.0))
    assert voice.value.code == 'TTS_INVALID_VOICE'
    with pytest.raises(StoryError) as rate:
        asyncio.run(service.audio_path('b', 1, 'v', '正文', 'zh-CN-YunxiNeural', 3.0))
    assert rate.value.code == 'TTS_INVALID_RATE'


def test_audio_endpoint_serves_selected_committed_version(tmp_path):
    from fastapi.testclient import TestClient
    from story_core.http import create_app
    from story_core.service import StoryService

    service = StoryService(tmp_path)
    book = service.open_book('写一本悬疑小说', title='旧收音机', chapter_count=1, target_words=50)
    imported = service.import_chapters(book['book_id'], [{'chapter_number': 1, 'title': '门外', 'body': '门外有人。'}])

    class FakeTTS:
        def voices(self):
            return [{'id': 'zh-CN-YunxiNeural', 'label': '云希'}]

        async def audio_path(self, *args):
            path = tmp_path / 'audio.mp3'
            path.write_bytes(b'ID3 fake')
            return path

    client = TestClient(create_app(tmp_path, tts_service=FakeTTS()))
    response = client.get(f"/api/books/{book['book_id']}/chapters/1/audio?version_id={imported['chapters'][0]['version_id']}")
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('audio/mpeg')
    assert response.content == b'ID3 fake'
    assert "media-src 'self' blob:" in response.headers['content-security-policy']
    voices = client.get('/api/tts/voices')
    assert voices.status_code == 200
    assert voices.json()['voices'][0]['id'] == 'zh-CN-YunxiNeural'
