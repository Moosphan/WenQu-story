def test_model_context_omits_only_duplicate_inspector_manifest():
    from story_core.model_context import model_input
    data = {'context_manifest': {'canonical_sources': ['large UI index']}, 'required_memory': [{'key': '人物', 'evidence': '原文'}], 'candidate': {'body': '正文'}, 'source_paragraphs': [{'id': 'p1', 'text': '正文'}]}
    sent = model_input(data)
    assert 'context_manifest' not in sent
    assert sent['required_memory'] == data['required_memory']
    assert sent['candidate'] == data['candidate']
    assert sent['source_paragraphs'] == data['source_paragraphs']
    assert 'context_manifest' in data


def test_memory_evidence_is_serialized_once_without_losing_provenance():
    from story_core.model_context import model_input
    memory = {'kind': 'fact', 'key': '承诺', 'value': '仍未兑现', 'evidence': '原文依据', 'source': {'quote': '原文依据', 'chapter_number': 3, 'version_id': 'v3'}}
    data = {'required_memory': [memory]}
    sent = model_input(data)['required_memory'][0]
    assert sent['evidence'] == '原文依据'
    assert sent['source'] == {'chapter_number': 3, 'version_id': 'v3'}
    assert data['required_memory'][0]['source']['quote'] == '原文依据'
    memory['source']['quote'] = '不同依据'
    assert model_input(data)['required_memory'][0]['source']['quote'] == '不同依据'


def test_retrieval_diagnostics_stay_local_without_removing_story_fields():
    from story_core.model_context import model_input
    memory = {'key': '约定', 'value': '归还铜牌', 'evidence': '铜牌明日还你',
              'context_reason': 'participant', 'source': {'chapter_number': 1}}
    data = {'required_memory': [memory], 'supplementary_memory': [memory],
            'candidate': {'body': 'context_reason 是正文中的文字'}}
    sent = model_input(data)
    for field in ('required_memory', 'supplementary_memory'):
        assert sent[field] == [{key: value for key, value in memory.items() if key != 'context_reason'}]
    assert data['required_memory'][0]['context_reason'] == 'participant'
    assert sent['candidate'] == data['candidate']


def test_context_blocker_identifies_pending_stage(tmp_path):
    from test_workflow import make
    service, book = make(tmp_path)
    with service.store.write(book) as conn:
        conn.execute("UPDATE runs SET status='needs_attention',reason='上下文超过 MVP 安全上限，需要缩小篇幅或人工整理记忆。' WHERE book_id=?", (book,))
    status = service.status(book)
    assert status['blocker']['code'] == 'CONTEXT_LIMIT'
    assert status['blocker']['stage'] == status['run']['stage']
    assert status['blocker']['chapter_number'] == status['run']['chapter_number']
    assert status['blocker']['input_bytes'] > 0
def test_retrieval_diagnostics_stay_local_without_removing_story_fields():
    from story_core.model_context import model_input
    memory = {'key': '约定', 'value': '归还铜牌', 'evidence': '铜牌明日还你',
              'context_reason': 'participant', 'source': {'chapter_number': 1}}
    data = {'required_memory': [memory], 'supplementary_memory': [memory],
            'candidate': {'body': 'context_reason 是正文中的文字'}}
    sent = model_input(data)
    for field in ('required_memory', 'supplementary_memory'):
        assert sent[field] == [{key: value for key, value in memory.items() if key != 'context_reason'}]
    assert data['required_memory'][0]['context_reason'] == 'participant'
    assert sent['candidate'] == data['candidate']
