import pytest
from test_bounded_retrieval import setup
from test_memory import seed
from story_core.errors import StoryError


def test_default_policy_is_lexical_and_missing_model_is_error(tmp_path):
    from story_core.semantic_retrieval import load_policy, configure_retrieval
    from story_core.retrieval import retriever_for
    store, _ = setup(tmp_path)
    assert load_policy(store)['strategy'] == 'lexical'
    assert retriever_for(store).semantic is None
    with pytest.raises(StoryError, match='本地'):
        configure_retrieval(store, {'strategy': 'semantic', 'model_path': str(tmp_path / 'missing')})
    assert not (tmp_path / 'retrieval-policy.json').exists()


def test_real_ann_persistence_prepared_query_and_core_authorization(tmp_path, monkeypatch):
    pytest.importorskip('hnswlib')
    import numpy as np
    from story_core import semantic_retrieval as sr
    from story_core.retrieval import retriever_for, RetrievalScope
    store, book = setup(tmp_path)
    seed(store, book, 1, '铜牌暂存商人处。', [{'kind':'fact','key':'铜牌','value':'商人持有','evidence':'铜牌'}])
    seed(store, book, 2, '铜牌被销毁。', [{'kind':'fact','key':'秘密','value':'销毁','visibility':'author','evidence':'铜牌'}])
    from contextlib import contextmanager
    original_write = store.write
    active_writes = []
    @contextmanager
    def tracked_write(*args, **kwargs):
        with original_write(*args, **kwargs) as conn:
            active_writes.append(conn)
            try:
                yield conn
            finally:
                active_writes.pop()
    monkeypatch.setattr(store, 'write', tracked_write)
    class SyntheticEncoder:
        fingerprint = 'synthetic-mathematics-only'
        def encode(self, texts):
            assert not active_writes
            # Deliberately synthetic: validates real HNSW math, NOT semantic quality.
            return np.array([[1., 0., 0.] for text in texts], dtype='float32')
    monkeypatch.setattr(sr, 'LocalEncoder', lambda policy: SyntheticEncoder())
    policy = {'strategy':'semantic', 'model_path':str(tmp_path)}
    sr.configure_retrieval(store, policy)
    assert sr.maintain_semantic_index(store, book, batch_size=1)['indexed'] == 2
    retriever = retriever_for(store)
    prepared = retriever.prepare_query('信物现在谁拿着')
    with store.read() as conn:
        result = retriever.search(prepared, RetrievalScope(book, 1, role='reader'), conn=conn)
        with pytest.raises(StoryError) as error:
            retriever.search('not prepared', RetrievalScope(book, 1), conn=conn)
        assert error.value.code == 'SEMANTIC_QUERY_NOT_PREPARED'
    assert [item['key'] for item in result['hits']] == ['铜牌']
    assert result['semantic_enabled']
    assert result['retrieval_mode'] == 'semantic'
    assert sr.maintain_semantic_index(store, book)['indexed'] == 0


def test_model_fingerprint_partition_and_stale_maintenance(tmp_path, monkeypatch):
    pytest.importorskip('hnswlib')
    import numpy as np
    from story_core import semantic_retrieval as sr
    from story_core.retrieval import retriever_for, RetrievalScope
    store, book = setup(tmp_path)
    old = seed(store, book, 1, '旧物。', [{'kind':'fact','key':'旧','value':'旧物','evidence':'旧物'}])
    class RacingEncoder:
        fingerprint = 'first'
        changed = False
        def encode(self, texts):
            if not self.changed:
                self.changed = True
                seed(store, book, 1, '新物。', [{'kind':'fact','key':'新','value':'新物','evidence':'新物'}])
            return np.array([[0.,1.] for _ in texts])
    encoder = RacingEncoder()
    monkeypatch.setattr(sr, 'LocalEncoder', lambda policy: encoder)
    sr.configure_retrieval(store, {'strategy':'semantic','model_path':str(tmp_path)})
    outcome = sr.maintain_semantic_index(store, book, batch_size=1)
    assert outcome['stale_skipped'] == 1
    assert outcome['indexed'] == 1
    result = retriever_for(store).search('不共享词项', RetrievalScope(book,1))
    assert [hit['key'] for hit in result['hits']] == ['新']
    assert result['hits'][0]['source']['version_id'] != old
    encoder.fingerprint = 'second'
    sr.configure_retrieval(store, {'strategy':'semantic','model_path':str(tmp_path)})
    with pytest.raises(StoryError) as error:
        retriever_for(store).search('任意', RetrievalScope(book,1))
    assert error.value.code == 'SEMANTIC_INDEX_UNAVAILABLE'
    assert sr.maintain_semantic_index(store, book)['indexed'] == 1
    with store.read() as conn:
        assert conn.execute('SELECT COUNT(*) FROM semantic_partitions').fetchone()[0] == 2


def test_commit_only_enqueues_without_loading_model(tmp_path):
    store, book = setup(tmp_path)
    version = seed(store, book, 1, '铜牌。', [{'kind':'fact','key':'铜牌','value':'物品','evidence':'铜牌'}])
    with store.read() as conn:
        row = conn.execute('SELECT * FROM semantic_pending WHERE book_id=?', (book,)).fetchone()
    assert row['source_version'] == version


def test_actual_sentence_transformer_local_safetensors_without_network(tmp_path, monkeypatch):
    """Random tiny BERT is a loader/math fixture, not pretrained semantic evidence."""
    pytest.importorskip('hnswlib')
    st = pytest.importorskip('sentence_transformers')
    import socket
    from sentence_transformers.models import Transformer, Pooling
    from transformers import BertConfig, BertModel, BertTokenizer
    from story_core.semantic_retrieval import configure_retrieval, maintain_semantic_index
    from story_core.retrieval import retriever_for, RetrievalScope
    raw = tmp_path / 'raw'
    raw.mkdir()
    (raw / 'vocab.txt').write_text('[PAD]\n[UNK]\n[CLS]\n[SEP]\n[MASK]\ncopper\ntoken\nmerchant\n')
    BertTokenizer(vocab_file=str(raw / 'vocab.txt')).save_pretrained(str(raw))
    BertModel(BertConfig(vocab_size=8, hidden_size=8, num_hidden_layers=1, num_attention_heads=2, intermediate_size=16)).save_pretrained(str(raw), safe_serialization=True)
    model = st.SentenceTransformer(modules=[Transformer(str(raw), model_args={'local_files_only':True}), Pooling(8)])
    path = tmp_path / 'tiny-model'
    model.save(str(path), safe_serialization=True)
    def no_network(*args, **kwargs):
        raise AssertionError('offline encoder attempted network')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    store, book = setup(tmp_path / 'store')
    seed(store, book, 1, 'copper token', [{'kind':'fact','key':'copper','value':'token merchant','evidence':'copper'}])
    configure_retrieval(store, {'strategy':'semantic','model_path':str(path)})
    assert maintain_semantic_index(store, book)['indexed'] == 1
    result = retriever_for(store).search('merchant token', RetrievalScope(book,1))
    assert result['semantic_enabled'] and len(result['hits']) == 1
