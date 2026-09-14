import asyncio
import json
import os
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .ai_config import AISettings
from .errors import StoryError
from .providers import OpenAICompatible, configured_provider, run_worker
from .service import StoryService
from .runtime import WorkbenchRuntime
from .tts import EdgeTTSService


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class OpenRequest(StrictModel):
    request: str
    title: str = ''
    genre: str = ''
    chapter_count: int = 40
    target_words: int = 2500
    mode: str = 'host'
    project: dict = Field(default_factory=dict)


class ControlRequest(StrictModel):
    action: str
    options: dict = Field(default_factory=dict)


class NextRequest(StrictModel):
    worker_id: str = 'gui'


class SubmitRequest(StrictModel):
    task_id: str
    lease_id: str
    result: dict
    worker_id: str = 'gui'


class ExportRequest(StrictModel):
    allow_partial: bool = False


class ImportChapter(StrictModel):
    chapter_number: int
    title: str
    body: str


class ImportRequest(StrictModel):
    chapters: list[ImportChapter] = Field(min_length=1, max_length=200)
    expected_revision: int | None = None


class QueryRequest(StrictModel):
    query: str
    role: str = 'author'
    through_chapter: int | None = None
    limit: int = 10


class BookKindRequest(StrictModel):
    kind: str


class HumanFeedbackRequest(StrictModel):
    chapter_number: int = Field(ge=1)
    reviewer_type: str
    reviewer_name: str = ''
    verdict: str = 'revise'
    rating: int | None = Field(default=None, ge=1, le=5)
    would_continue: bool | None = None
    notes: str = Field(min_length=1, max_length=10000)
    version_id: str | None = None


class ProjectMetadataRequest(StrictModel):
    metadata: dict = Field(default_factory=dict)
    expected_revision: int | None = None


class StoryBibleRequest(StrictModel):
    brief: dict
    plan: dict
    expected_revision: int | None = None


class MarketIdeasRequest(StrictModel):
    source_ids: list[str] = Field(min_length=1, max_length=8)
    preferences: dict = Field(default_factory=dict)
    limit: int = Field(default=3, ge=1, le=3)


class ProposalRequest(StrictModel):
    request: str = Field(min_length=1, max_length=20000)
    project: dict = Field(default_factory=dict)
    limit: int = Field(default=3, ge=1, le=3)


class AIConfigRequest(StrictModel):
    provider: str
    host_options: dict | None = None
    base_url: str = Field(default='', max_length=500)
    model: str = Field(default='', max_length=160)
    api_key: str = Field(default='', max_length=4096)


class IdeaRequest(StrictModel):
    request: str = Field(min_length=1, max_length=20000)
    genre_hint: str = Field(default='', max_length=100)
    project: dict = Field(default_factory=dict)


def _frame_ancestors(value):
    raw = os.environ.get('HULK_FRAME_ANCESTORS', '') if value is None else value
    if not isinstance(raw, str):
        raise ValueError('frame_ancestors must be a comma-separated origin list')
    sources = ["'self'"]
    if not raw.strip():
        return ' '.join(sources)
    for origin in raw.split(','):
        origin = origin.strip()
        if not re.fullmatch(r'https?://[A-Za-z0-9.-]+(?::\d{1,5})?', origin):
            raise ValueError('frame_ancestors accepts origins only, without paths or wildcards')
        if origin not in sources:
            sources.append(origin)
    return ' '.join(sources)


def create_app(root='./books', frame_ancestors=None, ai_settings=None, tts_service=None):
    service = StoryService(root)
    ai_settings = ai_settings or AISettings()
    tts_service = tts_service or EdgeTTSService(root)
    allowed_frame_ancestors = _frame_ancestors(frame_ancestors)
    runtime = WorkbenchRuntime(service)

    @asynccontextmanager
    async def lifespan(app):
        with runtime.server_lock():
            runtime.recover()
            try:
                yield
            finally:
                runtime.recover()

    app = FastAPI(title='WenQu', version='0.1.0', lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])
    active = {}
    idea_active = {}
    lock = threading.Lock()

    @app.middleware('http')
    async def same_origin(request: Request, call_next):
        origin=request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'跨站请求被拒绝。'},status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']=("default-src 'self'; style-src 'self'; script-src 'self'; media-src 'self' blob:; "
                                                    f"frame-ancestors {allowed_frame_ancestors}")
        return response

    @app.exception_handler(StoryError)
    async def story_error(request, error):
        return JSONResponse({'detail':error.as_dict()},status_code=404 if error.code=='NOT_FOUND' else 409 if error.code in ('STALE_REVISION','INVALID_LEASE','RUN_ACTIVE','TASK_BUSY','WORKER_RUNNING') else 503 if error.code in ('TTS_UNAVAILABLE','TTS_FAILED','TTS_EMPTY_AUDIO') else 400)

    @app.get('/api/ai-config')
    def ai_config():
        return ai_settings.public()

    @app.post('/api/ai-config')
    def save_ai_config(body: AIConfigRequest):
        return ai_settings.save(**body.model_dump())

    @app.get('/api/capabilities')
    def capabilities():
        result=service.capabilities()
        try:
            provider = configured_provider(settings=ai_settings)
            result.update(api_configured=True, worker_configured=True, executor=provider.name)
        except StoryError as error:
            result.update(api_configured=False, worker_configured=False, executor='manual', configuration_error=error.as_dict())
        return result

    @app.get('/api/market/sources')
    def market_sources():
        return {'sources': service.market_sources()}

    @app.get('/api/market/{source_id}')
    def market_snapshot(source_id: str):
        return service.market_snapshot(source_id)

    @app.post('/api/market/{source_id}/refresh')
    def refresh_market_snapshot(source_id: str):
        return service.market_snapshot(source_id, refresh=True)

    @app.post('/api/market/ideas')
    def market_ideas(body: MarketIdeasRequest):
        return service.market_ideas(body.source_ids, body.preferences, body.limit)

    @app.post('/api/proposals')
    def proposals(body: ProposalRequest):
        return service.project_proposals(body.request, body.project, body.limit)

    @app.post('/api/ideas')
    def create_idea(body: IdeaRequest):
        job = service.create_idea_job(body.request, body.genre_hint, body.project)
        provider = configured_provider(settings=ai_settings)
        with lock:
            def execute():
                try:
                    service.run_idea_job(job['job_id'], provider)
                finally:
                    with lock:
                        idea_active.pop(job['job_id'], None)
            thread = threading.Thread(target=execute, daemon=True)
            idea_active[job['job_id']] = thread
            thread.start()
        return job

    @app.get('/api/ideas/{job_id}')
    def idea(job_id: str):
        return service.idea_job(job_id)

    @app.get('/api/books')
    def books(kind: str = 'all'):
        return service.list_books(kind)

    @app.post('/api/books')
    def open_book(body: OpenRequest):
        return service.open_book(**body.model_dump())

    @app.get('/api/books/{book_id}')
    def book(book_id: str):
        return service.get_book(book_id)

    @app.get('/api/books/{book_id}/project')
    def project(book_id: str):
        return {'project': service.project_metadata(book_id)}

    @app.post('/api/books/{book_id}/project')
    def update_project(book_id: str, body: ProjectMetadataRequest):
        return service.update_project_metadata(book_id, body.metadata, body.expected_revision)

    @app.post('/api/books/{book_id}/story-bible')
    def update_story_bible(book_id: str, body: StoryBibleRequest):
        return service.update_story_bible(book_id, body.brief, body.plan, body.expected_revision)

    @app.get('/api/books/{book_id}/status')
    def status(book_id: str):
        result=service.status(book_id)
        session = runtime.latest(book_id)
        if session and session['run_id'] != (result['run'] or {}).get('run_id'):
            session = None
        with lock:
            result['worker_running'] = book_id in active
        if session and session.get('finished_at') and (result.get('execution') or {}).get('started_at', 0) > session['finished_at']:
            session = None  # A later external/recovery task supersedes this completed worker session.
        result['worker_session'] = session
        result['worker_error'] = session['error'] if session else None
        return result

    @app.get('/api/books/{book_id}/reviews')
    def reviews(book_id: str, chapter_number: int | None = None, limit: int = 100, include_history: bool = False):
        return {'reviews': service.review_history(book_id, chapter_number, limit, include_history)}

    @app.get('/api/books/{book_id}/candidates')
    def candidate_versions(book_id: str, chapter_number: int | None = None, limit: int = 100):
        return {'candidates': service.candidate_versions(book_id, chapter_number, limit)}

    @app.post('/api/books/{book_id}/feedback')
    def human_feedback(book_id: str, body: HumanFeedbackRequest):
        return service.add_human_feedback(book_id, **body.model_dump())

    @app.get('/api/books/{book_id}/chapters/{chapter_number}/versions')
    def chapter_versions(book_id: str, chapter_number: int):
        return {'versions': service.chapter_versions(book_id, chapter_number)}

    @app.get('/api/trash')
    def trash_list():
        return service.list_trash()

    @app.post('/api/books/{book_id}/trash')
    def trash_book(book_id: str):
        return service.trash_book(book_id)

    @app.post('/api/books/{book_id}/restore')
    def restore_book(book_id: str):
        return service.restore_book(book_id)

    @app.delete('/api/trash/{book_id}')
    def purge_book(book_id: str):
        return service.purge_book(book_id)

    @app.get('/api/tts/voices')
    def tts_voices():
        return {'voices': tts_service.voices()}

    @app.get('/api/books/{book_id}/chapters/{chapter_number}/audio')
    def chapter_audio(book_id: str, chapter_number: int, version_id: str | None = None,
                      voice: str = 'zh-CN-YunxiNeural', rate: float = 1.0):
        book = service.get_book(book_id)
        chapter = next((item for item in book['chapters'] if item['chapter_number'] == chapter_number), None)
        selected_version = version_id or (chapter or {}).get('version_id')
        body = None
        title = None
        if chapter and (version_id is None or version_id == chapter['version_id']):
            body, title = chapter['body'], chapter['title']
        elif version_id and version_id != 'candidate':
            with service.store.read() as conn:
                row = conn.execute('SELECT title,body FROM chapter_versions WHERE id=? AND book_id=? AND number=?',
                                   (version_id, book_id, chapter_number)).fetchone()
            if row:
                body, title = row['body'], row['title']
        if version_id == 'candidate' or body is None:
            status = service.status(book_id)
            run = status.get('run') or {}
            candidate = run.get('candidate')
            if run.get('chapter_number') == chapter_number and candidate:
                body, title = candidate.get('body'), candidate.get('title')
                selected_version = f"candidate:{run['run_id']}"
        if not body:
            raise HTTPException(status_code=404, detail='该章节没有可播放正文。')
        path = asyncio.run(tts_service.audio_path(book_id, chapter_number, selected_version or 'current', body, voice, rate))
        return FileResponse(path, media_type='audio/mpeg', filename=f'{chapter_number:03d}-{title or "chapter"}.mp3',
                            headers={'Cache-Control': 'public, max-age=31536000, immutable'})

    @app.get('/api/books/{book_id}/memory-facets')
    def memory_facets(book_id: str):
        return {'facets': service.memory_facets(book_id)}

    @app.post('/api/books/{book_id}/kind')
    def book_kind(book_id: str, body: BookKindRequest):
        return service.set_book_kind(book_id, body.kind)

    @app.delete('/api/books/{book_id}')
    def delete_sample(book_id: str):
        return service.delete_sample(book_id)

    @app.get('/api/books/{book_id}/report')
    def report(book_id: str):
        return service.execution_report(book_id)

    @app.post('/api/books/{book_id}/control')
    def control(book_id: str, body: ControlRequest):
        return service.control(book_id,body.action,**body.options)

    @app.post('/api/books/{book_id}/next')
    def next_task(book_id: str, body: NextRequest):
        return service.next_task(book_id,body.worker_id)

    @app.post('/api/tasks/submit')
    def submit(body: SubmitRequest):
        return service.submit_task(**body.model_dump())

    @app.post('/api/books/{book_id}/query')
    def query(book_id: str, body: QueryRequest):
        return service.query(book_id,**body.model_dump())

    @app.post('/api/books/{book_id}/export')
    def export(book_id: str, body: ExportRequest):
        return service.export(book_id,body.allow_partial)

    @app.post('/api/books/{book_id}/import')
    def import_chapters(book_id: str, body: ImportRequest):
        return service.import_chapters(book_id, [chapter.model_dump() for chapter in body.chapters], body.expected_revision)

    @app.get('/api/exports/{export_id}/{filename}')
    def download(export_id: str, filename: str):
        with service.store.read() as conn:
            row=conn.execute('SELECT manifest FROM exports WHERE id=?',(export_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        manifest=json.loads(row['manifest'])
        if filename not in ('manuscript.txt','manuscript.md','book.epub','manifest.json'):
            raise HTTPException(404)
        path=Path(manifest['path'])/filename
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path,filename=filename)

    @app.post('/api/books/{book_id}/worker')
    def worker(book_id: str):
        provider=configured_provider(settings=ai_settings)
        service.get_book(book_id)
        with lock:
            if book_id in active:
                return {'running':True}
            session = runtime.reserve(book_id, provider.name)
            def execute():
                try:
                    runtime.execute(session, provider)
                finally:
                    with lock:
                        active.pop(book_id,None)
            thread=threading.Thread(target=execute,daemon=True)
            active[book_id]=thread
            thread.start()
        return {'running':True}

    web=Path(__file__).parent/'web'
    app.mount('/assets',StaticFiles(directory=web),name='assets')

    @app.get('/style.css')
    def stylesheet():
        return FileResponse(web/'style.css', media_type='text/css', headers={'Cache-Control': 'no-store'})

    @app.get('/app.js')
    def javascript():
        return FileResponse(web/'app.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})

    @app.get('/')
    def index():
        return FileResponse(web/'index.html', headers={'Cache-Control': 'no-store'})

    return app
