from pathlib import Path
import subprocess


def test_tts_url_encodes_selected_version_and_auto_next_uses_next_chapter_only():
    source = Path('story_core/web/app.js').read_text()
    start = source.index('function ttsAudioUrl(')
    end = source.index('function formatAudioTime(', start)
    helpers = source[start:end]
    script = helpers + r'''
const assert = require('node:assert/strict');
const url = ttsAudioUrl('book/1', {chapter_number: 2, candidate: true}, 'zh-CN-XiaoxiaoNeural', 1.25);
assert.ok(url.includes('/api/books/book%2F1/chapters/2/audio'));
assert.ok(url.includes('version_id=candidate'));
assert.ok(url.includes('voice=zh-CN-XiaoxiaoNeural'));
assert.ok(url.includes('rate=1.25'));
const chapters = [{chapter_number: 3, title:'三'}, {chapter_number: 1, title:'一'}, {chapter_number: 5, title:'五'}];
assert.equal(nextPlayableChapter(chapters, 1).chapter_number, 3);
assert.equal(nextPlayableChapter(chapters, 5), null);
'''
    subprocess.run(['node', '-e', script], check=True)


def test_tts_controls_are_present_and_auto_play_is_not_page_load_behavior():
    html = Path('story_core/web/index.html').read_text()
    js = Path('story_core/web/app.js').read_text()
    for item in ('chapter-audio', 'tts-voice', 'tts-rate', 'tts-volume', 'tts-auto-next', 'tts-play'):
        assert f'id="{item}"' in html
    assert 'await loadTTSVoices();' in js
    assert 'audio.addEventListener' not in js  # uses the actual chapter-audio element, not a global autoplay hook
    assert "action('tts-play', toggleTTS)" in js


def test_preload_threshold_deduplication_and_cancel():
    source = Path('story_core/web/app.js').read_text()
    helpers = source[source.index('function ttsAudioUrl('):source.index('function resetTTSForChapter(')]
    script = helpers + r'''
const assert = require('node:assert/strict');
const audio = {duration:100,currentTime:69};
const controls = {'chapter-audio':audio,'tts-auto-next':{checked:true},'tts-voice':{value:'v'},'tts-rate':{value:'1'},'tts-status':{}};
const element = id => controls[id];
const state = {tts:{playing:true},dirty:false,chapter:{chapter_number:1},book:{book_id:'b',chapters:[{chapter_number:2,version_id:'v2'}]}};
let calls=0; let signal;
const fetch = (url, options) => { calls++; signal=options.signal; return Promise.resolve({ok:true,blob:async()=>new Blob(['audio'])}); };
(async()=>{
maybePreloadTTS(); assert.equal(calls,0);
audio.currentTime=70; maybePreloadTTS(); maybePreloadTTS(); assert.equal(calls,1);
assert.ok(await state.tts.preload.promise);
maybePreloadTTS(); assert.equal(calls,1);
clearTTSPreload(); assert.equal(signal.aborted,true);
controls['tts-auto-next'].checked=false; maybePreloadTTS(); assert.equal(calls,1);
controls['tts-auto-next'].checked=true; state.dirty=true; maybePreloadTTS(); assert.equal(calls,1);
})();
'''
    subprocess.run(['node', '-e', script], check=True)
