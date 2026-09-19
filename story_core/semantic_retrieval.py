"""Explicit offline embeddings with book/model-partitioned, persistent HNSW.

No model identifier, network fallback, or provider is accepted. Maintenance is
explicit; queries never embed the archive. Old index generations are retained so
concurrent readers can finish safely (operators may garbage collect offline).
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import uuid
import fcntl
import shutil

from .errors import StoryError


def validate_policy(policy):
    if not isinstance(policy, dict) or policy.get('strategy') not in ('lexical', 'semantic', 'hybrid'):
        raise StoryError('INVALID_RETRIEVAL_POLICY', '检索策略必须是 lexical、semantic 或 hybrid。')
    if set(policy) - {'strategy', 'model_path', 'model_fingerprint'}:
        raise StoryError('INVALID_RETRIEVAL_POLICY', '检索配置含未知字段。')
    return policy


def load_policy(store):
    path = store.root / 'retrieval-policy.json'
    try:
        policy = json.loads(path.read_text()) if path.exists() else {'strategy': 'lexical'}
    except (OSError, ValueError) as exc:
        raise StoryError('INVALID_RETRIEVAL_POLICY', '检索配置无法读取。') from exc
    return validate_policy(policy)


def configure_retrieval(store, config):
    policy = dict(validate_policy(config))
    if policy.get('strategy') not in ('lexical', 'semantic', 'hybrid'):
        raise StoryError('INVALID_RETRIEVAL_POLICY', '无效检索策略。')
    if policy['strategy'] != 'lexical':
        encoder = LocalEncoder(policy)  # Validate weights and dependencies before activation.
        policy['model_fingerprint'] = encoder.fingerprint
    path = store.root / 'retrieval-policy.json'
    temp = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(policy, ensure_ascii=False, indent=2))
    temp.replace(path)
    return policy


class LocalEncoder:
    def __init__(self, policy):
        raw = policy.get('model_path')
        path = Path(raw).expanduser() if isinstance(raw, str) and raw else None
        if path is None or not path.is_absolute() or not path.is_dir() or not list(path.rglob('*.safetensors')):
            raise StoryError('SEMANTIC_UNAVAILABLE', '需要明确的绝对本地模型目录与 safetensors 权重。')
        try:
            import hnswlib  # noqa: F401
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise StoryError('SEMANTIC_UNAVAILABLE', '缺少本地 semantic 可选依赖。') from exc
        digest = hashlib.sha256()
        for file in sorted(p for p in path.rglob('*') if p.is_file()):
            digest.update(str(file.relative_to(path)).encode())
            with file.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
        self.fingerprint = digest.hexdigest()
        expected = policy.get('model_fingerprint')
        if expected and expected != self.fingerprint:
            raise StoryError('SEMANTIC_MODEL_CHANGED', '本地模型指纹已改变；请重新配置并维护独立索引。')
        try:
            self.model = SentenceTransformer(str(path), device='cpu', local_files_only=True,
                trust_remote_code=False, model_kwargs={'use_safetensors': True})
        except Exception as exc:
            raise StoryError('SEMANTIC_UNAVAILABLE', '本地模型加载失败；不会下载或回退远程服务。') from exc

    def encode(self, texts):
        return self.model.encode(texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)


@dataclass(frozen=True)
class PreparedSemanticQuery:
    text: str
    vector: object
    fingerprint: str


def _schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS semantic_partitions (
        book_id TEXT NOT NULL, fingerprint TEXT NOT NULL, dimension INTEGER NOT NULL,
        index_path TEXT NOT NULL, next_label INTEGER NOT NULL,
        PRIMARY KEY(book_id,fingerprint))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS semantic_vectors (
        book_id TEXT NOT NULL, fingerprint TEXT NOT NULL, memory_id TEXT NOT NULL,
        source_version TEXT NOT NULL, label INTEGER NOT NULL, dimension INTEGER NOT NULL,
        vector BLOB NOT NULL, indexed_status TEXT NOT NULL,
        PRIMARY KEY(book_id,fingerprint,memory_id), UNIQUE(book_id,fingerprint,label))''')


def delete_semantic_book(conn, book_id):
    """Remove optional semantic metadata in the caller's book-deletion transaction."""
    for table in ('semantic_vectors', 'semantic_partitions', 'semantic_pending'):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            conn.execute(f'DELETE FROM {table} WHERE book_id=?', (book_id,))


def purge_semantic_files(store, book_id):
    """After book deletion commits, remove its generations without following links."""
    parent = store.root / 'semantic-indexes'
    folder = parent / hashlib.sha256(book_id.encode()).hexdigest()
    if (parent.is_symlink() or folder.is_symlink() or not folder.is_dir()
            or parent.resolve().parent != store.root.resolve()
            or folder.resolve().parent != parent.resolve()):
        return
    shutil.rmtree(folder)


def _vectors(values):
    import numpy as np
    array = np.asarray(values, dtype='float32')
    if array.ndim != 2 or not array.shape[1] or not np.isfinite(array).all():
        raise StoryError('SEMANTIC_VECTOR_INVALID', '本地编码器返回无效向量。')
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if (norms == 0).any():
        raise StoryError('SEMANTIC_VECTOR_INVALID', '本地编码器返回零向量。')
    return array / norms


class LocalSemanticAdapter:
    def __init__(self, store, policy):
        self.store = store
        self.policy = dict(policy)
        self.encoder = None
        self._cache = {}

    def prepare_query(self, text):
        if not isinstance(text, str) or len(text) > 10000:
            raise StoryError('INVALID_REQUEST', '检索词无效。')
        if self.encoder is None:
            self.encoder = LocalEncoder(self.policy)
            self.policy['model_fingerprint'] = self.encoder.fingerprint
        vector = _vectors(self.encoder.encode([text]))[0]
        return PreparedSemanticQuery(text, vector, self.encoder.fingerprint)

    def candidates(self, query, scope, limit):
        import hnswlib
        if not isinstance(query, PreparedSemanticQuery) or query.fingerprint != self.policy.get('model_fingerprint'):
            raise StoryError('SEMANTIC_QUERY_NOT_PREPARED', '请先在事务外编码检索词。')
        with self.store.read() as conn:
            present = conn.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_partitions'").fetchone()
            row = conn.execute('SELECT * FROM semantic_partitions WHERE book_id=? AND fingerprint=?', (scope.book_id, query.fingerprint)).fetchone() if present else None
            if row is None:
                # A first chapter (or a fully filtered scope) has no evidence to
                # index. Missing partitions are actionable only for eligible rows.
                eligible = conn.execute('''SELECT 1 FROM memories m JOIN chapters c
                    ON c.book_id=m.book_id AND c.number=m.chapter_number AND c.version_id=m.version_id
                    WHERE m.book_id=:book AND m.chapter_number<=:boundary AND c.status='committed'
                    AND (:role='author' OR m.visibility='reader')
                    AND (:pov='' OR (m.visibility='reader' AND
                         (m.kind!='knowledge' OR json_extract(m.data,'$.owner')=:pov)))
                    LIMIT 1''', {'book': scope.book_id, 'boundary': scope.through_chapter,
                                 'role': scope.role, 'pov': scope.pov or ''}).fetchone()
                if eligible is None:
                    return []
                raise StoryError('SEMANTIC_INDEX_UNAVAILABLE', '此作品和模型尚未维护本地语义索引。')
            if len(query.vector) != row['dimension']:
                raise StoryError('SEMANTIC_DIMENSION_CHANGED', '模型向量维度与索引不一致。')
            path = row['index_path']
            if path not in self._cache:
                index = hnswlib.Index(space='cosine', dim=row['dimension'])
                index.load_index(path)
                index.set_ef(128)
                self._cache = {path: index}
            index = self._cache[path]
            count = min(limit, index.get_current_count())
            if not count:
                return []
            labels, _ = index.knn_query(query.vector, k=count, num_threads=1)
            labels = [int(value) for value in labels[0]]
            rows = conn.execute('''SELECT label,memory_id FROM semantic_vectors WHERE book_id=? AND fingerprint=?
                AND indexed_status='ready' AND label IN (SELECT value FROM json_each(?))''',
                (scope.book_id, query.fingerprint, json.dumps(labels)))
            mapping = {row['label']: row['memory_id'] for row in rows}
            return [mapping[label] for label in labels if label in mapping]


def maintain_semantic_index(store, book_id, *, batch_size=32):
    """Incremental batches. Encode/build outside DB writes; recheck source before publish."""
    try:
        import hnswlib
    except ImportError as exc:
        raise StoryError('SEMANTIC_UNAVAILABLE', '缺少本地 semantic 可选依赖。') from exc
    if type(batch_size) is not int or not 1 <= batch_size <= 256:
        raise StoryError('INVALID_REQUEST', 'batch_size 必须在 1–256。')
    policy = load_policy(store)
    if policy['strategy'] == 'lexical':
        raise StoryError('SEMANTIC_UNAVAILABLE', '请先显式配置本地语义模型。')
    encoder = LocalEncoder(policy)
    fingerprint = encoder.fingerprint
    directory = store.root / 'semantic-indexes' / hashlib.sha256(book_id.encode()).hexdigest() / fingerprint
    directory.mkdir(parents=True, exist_ok=True)
    total, skipped = 0, 0
    with (directory / 'maintenance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with store.write(book_id) as conn:
            from .retrieval import initialize_index
            initialize_index(conn)
            _schema(conn)
        # A maintenance request performs one bounded batch; callers explicitly resume.
        for _ in range(1):
            with store.read() as conn:
                rows = conn.execute('''SELECT m.id,m.version_id,m.key,m.value FROM memories m
                    JOIN chapters c ON c.book_id=m.book_id AND c.number=m.chapter_number AND c.version_id=m.version_id
                    WHERE m.book_id=? AND c.status='committed' AND NOT EXISTS
                    (SELECT 1 FROM semantic_vectors v WHERE v.book_id=m.book_id AND v.fingerprint=?
                     AND v.memory_id=m.id AND v.source_version=m.version_id AND v.indexed_status='ready')
                    ORDER BY m.id LIMIT ?''', (book_id, fingerprint, batch_size)).fetchall()
                partition = conn.execute('SELECT * FROM semantic_partitions WHERE book_id=? AND fingerprint=?', (book_id, fingerprint)).fetchone()
            if not rows:
                break
            vectors = _vectors(encoder.encode([row['key'] + ' ' + row['value'] for row in rows]))
            if len(vectors) != len(rows):
                raise StoryError('SEMANTIC_VECTOR_INVALID', '编码器返回数量不匹配。')
            dimension = vectors.shape[1]
            if partition and partition['dimension'] != dimension:
                raise StoryError('SEMANTIC_DIMENSION_CHANGED', '同一模型指纹的向量维度发生变化。')
            index = hnswlib.Index(space='cosine', dim=dimension)
            start = partition['next_label'] if partition else 0
            if partition:
                index.load_index(partition['index_path'], max_elements=start + len(rows))
            else:
                index.init_index(max_elements=len(rows), ef_construction=128, M=16, random_seed=17)
            labels = list(range(start, start + len(rows)))
            index.add_items(vectors, labels, num_threads=1)
            path = directory / (uuid.uuid4().hex + '.hnsw')
            index.save_index(str(path))
            with store.write(book_id) as conn:
                for row, vector, label in zip(rows, vectors, labels):
                    current = conn.execute('''SELECT 1 FROM memories m JOIN chapters c ON c.book_id=m.book_id
                        AND c.number=m.chapter_number AND c.version_id=m.version_id WHERE m.id=? AND m.version_id=?
                        AND m.book_id=? AND c.status='committed' ''', (row['id'], row['version_id'], book_id)).fetchone()
                    if not current:
                        skipped += 1
                        continue
                    conn.execute('INSERT OR REPLACE INTO semantic_vectors VALUES (?,?,?,?,?,?,?,?)',
                        (book_id, fingerprint, row['id'], row['version_id'], label, dimension, vector.tobytes(), 'ready'))
                    conn.execute('DELETE FROM semantic_pending WHERE memory_id=?', (row['id'],))
                    total += 1
                conn.execute('INSERT OR REPLACE INTO semantic_partitions VALUES (?,?,?,?,?)',
                    (book_id, fingerprint, dimension, str(path), start + len(rows)))
    with store.read() as conn:
        remaining = conn.execute('''SELECT COUNT(*) FROM memories m JOIN chapters c
            ON c.book_id=m.book_id AND c.number=m.chapter_number AND c.version_id=m.version_id
            WHERE m.book_id=? AND c.status='committed' AND NOT EXISTS
            (SELECT 1 FROM semantic_vectors v WHERE v.book_id=m.book_id AND v.fingerprint=?
             AND v.memory_id=m.id AND v.source_version=m.version_id AND v.indexed_status='ready')''',
            (book_id, fingerprint)).fetchone()[0]
    return {'indexed': total, 'stale_skipped': skipped, 'remaining': remaining,
            'model_fingerprint': fingerprint}
