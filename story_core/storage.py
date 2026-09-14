"""SQLite is the authority. Model calls never run inside a write transaction."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .errors import StoryError


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS book_trash (book_id TEXT PRIMARY KEY, deleted_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS books (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, request TEXT NOT NULL, config TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'new', revision INTEGER NOT NULL DEFAULT 0,
 brief TEXT, plan TEXT, ending TEXT, kind TEXT NOT NULL DEFAULT 'user', project TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chapter_versions (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id), number INTEGER NOT NULL,
 title TEXT NOT NULL, body TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chapters (
 book_id TEXT NOT NULL REFERENCES books(id), number INTEGER NOT NULL,
 version_id TEXT NOT NULL REFERENCES chapter_versions(id), status TEXT NOT NULL,
 PRIMARY KEY(book_id,number));
CREATE TABLE IF NOT EXISTS memories (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL, chapter_number INTEGER NOT NULL,
 version_id TEXT NOT NULL REFERENCES chapter_versions(id), kind TEXT NOT NULL,
 key TEXT NOT NULL, value TEXT NOT NULL, evidence TEXT NOT NULL,
 visibility TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS memory_scope ON memories(book_id,chapter_number,version_id);
CREATE TABLE IF NOT EXISTS chunks (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL, chapter_number INTEGER NOT NULL,
 version_id TEXT NOT NULL REFERENCES chapter_versions(id), text TEXT NOT NULL, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id), status TEXT NOT NULL,
 stage TEXT NOT NULL, chapter_number INTEGER NOT NULL, end_chapter INTEGER NOT NULL,
 steps INTEGER NOT NULL DEFAULT 0, max_steps INTEGER NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, max_revisions INTEGER NOT NULL,
 tokens INTEGER NOT NULL DEFAULT 0, budget_tokens INTEGER NOT NULL,
 candidate TEXT, extraction TEXT, reviews TEXT NOT NULL DEFAULT '[]', reason TEXT,
 repair INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(book_id) WHERE status IN ('running','paused','needs_attention');
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), book_id TEXT NOT NULL,
 stage TEXT NOT NULL, chapter_number INTEGER NOT NULL, input TEXT NOT NULL, output_schema TEXT NOT NULL,
 base_revision INTEGER NOT NULL, status TEXT NOT NULL, worker_id TEXT NOT NULL,
 lease_id TEXT NOT NULL, lease_until REAL NOT NULL, result TEXT, response TEXT,
 created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS task_run ON tasks(run_id,status);
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, book_id TEXT NOT NULL, run_id TEXT,
 kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS exports (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL, revision INTEGER NOT NULL,
 manifest TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS human_reviews (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id), chapter_number INTEGER NOT NULL,
 version_id TEXT NOT NULL REFERENCES chapter_versions(id), reviewer_type TEXT NOT NULL,
 reviewer_name TEXT NOT NULL, verdict TEXT NOT NULL, rating INTEGER, would_continue INTEGER,
 notes TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS human_review_scope ON human_reviews(book_id, chapter_number, version_id, created_at);
CREATE TABLE IF NOT EXISTS market_snapshots (
 id TEXT PRIMARY KEY, source_id TEXT NOT NULL, snapshot TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS market_snapshot_source ON market_snapshots(source_id, created_at DESC);
CREATE TABLE IF NOT EXISTS idea_jobs (
 id TEXT PRIMARY KEY, input TEXT NOT NULL, status TEXT NOT NULL, result TEXT, error TEXT,
 created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS workbench_executions (
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(id),
 run_id TEXT NOT NULL REFERENCES runs(id), executor TEXT NOT NULL,
 status TEXT NOT NULL, error TEXT, started_at REAL NOT NULL, finished_at REAL);
CREATE INDEX IF NOT EXISTS workbench_execution_history ON workbench_executions(book_id,started_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS one_workbench_execution ON workbench_executions(book_id) WHERE status='running';
PRAGMA user_version = 9;
"""


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "project.sqlite"
        with self.connect() as conn:
            if conn.execute("PRAGMA user_version").fetchone()[0] > 9:
                raise StoryError("NEWER_DATABASE", "数据库版本高于当前程序，请升级程序。")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            columns = {row['name'] for row in conn.execute("PRAGMA table_info(books)")}
            if 'kind' not in columns:
                conn.execute("ALTER TABLE books ADD COLUMN kind TEXT NOT NULL DEFAULT 'user'")
            if 'project' not in columns:
                conn.execute("ALTER TABLE books ADD COLUMN project TEXT NOT NULL DEFAULT '{}'")
            conn.execute("UPDATE books SET kind='user' WHERE kind IS NULL")
            conn.execute("UPDATE books SET project='{}' WHERE project IS NULL")
            from .long_memory import SCHEMA as LONG_MEMORY_SCHEMA
            conn.executescript(LONG_MEMORY_SCHEMA)
            from .memory_workflow import SCHEMA as MEMORY_WORKFLOW_SCHEMA
            conn.executescript(MEMORY_WORKFLOW_SCHEMA)
            conn.execute("PRAGMA user_version = 9")

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    @contextmanager
    def read(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
        finally:
            conn.rollback()
            conn.close()

    @contextmanager
    def write(self, book_id=None, expected_revision=None, allow_trash=False):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if book_id is not None:
                book = conn.execute("SELECT revision FROM books WHERE id=?", (book_id,)).fetchone()
                if book is None:
                    raise StoryError("NOT_FOUND", "找不到这本书。")
                if not allow_trash and conn.execute('SELECT 1 FROM book_trash WHERE book_id=?', (book_id,)).fetchone():
                    raise StoryError('BOOK_TRASHED', '作品已在回收站，请先还原再操作。')
                if expected_revision is not None and book["revision"] != expected_revision:
                    raise StoryError("STALE_REVISION", "作品已发生变化，请重新领取任务。")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_book(self, request, title, config, project=None):
        book_id = uid("book")
        with self.write() as conn:
            conn.execute("INSERT INTO books(id,title,request,config,kind,project,created_at) VALUES (?,?,?,?,?,?,?)",
                         (book_id, title, request, dumps(config), 'user', dumps(project or {}), time.time()))
        return self.book(book_id)

    @staticmethod
    def decode_book(row):
        if row is None:
            raise StoryError("NOT_FOUND", "找不到这本书。")
        book = dict(row)
        book["book_id"] = book.pop("id")
        book["book_kind"] = book.pop("kind", "user")
        book["settings"] = json.loads(book.pop("config"))
        book["project"] = json.loads(book.pop("project", "{}") or "{}")
        for field in ("brief", "plan", "ending"):
            book[field] = json.loads(book[field]) if book[field] else None
        return book

    def book(self, book_id):
        with self.read() as conn:
            return self.decode_book(conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())

    @staticmethod
    def event(conn, book_id, kind, payload, run_id=None):
        conn.execute("INSERT INTO events(book_id,run_id,kind,payload,created_at) VALUES (?,?,?,?,?)",
                     (book_id, run_id, kind, dumps(payload), time.time()))
