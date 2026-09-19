"""Versioned lexical RAG, with authorization before retrieval and quoted sources."""
import json
import re

from .errors import StoryError
from .storage import dumps, uid

KINDS = {"fact", "entity", "relationship", "knowledge", "promise", "emotion", "timeline", "summary"}
ENTITY_TYPES = {"person", "item", "place", "organization", "creature", "other"}


_EVIDENCE_PUNCTUATION = str.maketrans({
    '“': '"', '”': '"', '‘': "'", '’': "'", '：': ':', '，': ',', '。': '.',
    '！': '!', '？': '?', '；': ';', '（': '(', '）': ')', '【': '[', '】': ']', '…': '...',
})


def _evidence_projection(text):
    """Return a format-insensitive comparison string and its source character spans."""
    characters, starts, ends = [], [], []
    for index, character in enumerate(text):
        if character.isspace():
            continue
        for projected in character.translate(_EVIDENCE_PUNCTUATION):
            characters.append(projected); starts.append(index); ends.append(index + 1)
    return ''.join(characters), starts, ends


def exact_evidence(body, evidence):
    """Keep provenance strict while tolerating harmless quote and punctuation normalization."""
    if evidence in body:
        return evidence
    expected, _, _ = _evidence_projection(evidence)
    actual, starts, ends = _evidence_projection(body)
    if not expected:
        return None
    position = actual.find(expected)
    if position < 0:
        return None
    return body[starts[position]:ends[position + len(expected) - 1]]


def validate_memories(body, memories):
    if not isinstance(memories, list) or len(memories) > 30:
        raise StoryError("INVALID_RESULT", "记忆必须为至多 30 项的数组。")
    for item in memories:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            raise StoryError("INVALID_RESULT", "记忆类型无效。")
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("key", "value", "evidence")):
            raise StoryError("INVALID_RESULT", "每条记忆需要 key、value 和正文原文 evidence。")
        matched = exact_evidence(body, item["evidence"])
        if matched is None:
            raise StoryError("INVALID_EVIDENCE", "记忆证据不在当前正文中。", {"key": item["key"]})
        item["evidence"] = matched
        if item.get("visibility", "reader") not in ("author", "reader"):
            raise StoryError("INVALID_SCOPE", "记忆可见性无效。")
        if item.get("entity_type") is not None and (item["kind"] != "entity" or item["entity_type"] not in ENTITY_TYPES):
            raise StoryError("INVALID_RESULT", "实体类型只能用于 entity，且必须为受支持的分类。", {"key": item["key"], "field": "entity_type", "kind": item["kind"]})
        aliases = item.get("aliases")
        if aliases is not None and (item["kind"] != "entity" or not isinstance(aliases, list) or not 1 <= len(aliases) <= 12
                                    or len(set(aliases)) != len(aliases) or not all(isinstance(alias, str) and 1 <= len(alias.strip()) <= 100 for alias in aliases)):
            raise StoryError("INVALID_RESULT", "别名只能用于 entity，且必须为 1–12 个唯一的短文本。")


def index_chapter(conn, book_id, number, version_id, body, memories):
    validate_memories(body, memories)
    conn.execute("DELETE FROM memories WHERE version_id=?", (version_id,))
    conn.execute("DELETE FROM chunks WHERE version_id=?", (version_id,))
    for item in memories:
        conn.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (uid("mem"), book_id, number, version_id, item["kind"], item["key"], item["value"],
                      item["evidence"], item.get("visibility", "reader"), dumps(item)))
    from .retrieval import index_version
    index_version(conn, book_id, version_id)
    # Overlapping character windows: no tokenizer dependency or English-only FTS.
    for start in range(0, len(body), 600):
        conn.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)",
                     (uid("chunk"), book_id, number, version_id, body[start:start + 750], start))


def _terms(query):
    words = re.findall(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+", query.lower())
    return set(t for w in words for t in ([w] if len(w) < 3 or w.isascii() else [w] + [w[i:i+2] for i in range(len(w)-1)]))


def _memory_search_text(memory):
    return ' '.join([memory.get('key', ''), memory.get('value', ''), *memory.get('aliases', [])]).lower()


def promise_obligations(memories, plan, chapter_number, upcoming_window=2):
    """Return unresolved mandatory plan promises relevant to this chapter.

    The caller supplies active canonical memory, so a paid/waived status from a
    prior committed chapter removes the plan obligation without mutating it.
    """
    if not isinstance(memories, list) or not isinstance(plan, dict) or type(chapter_number) is not int or chapter_number < 1:
        raise StoryError('INVALID_REQUEST', '承诺检查参数无效。')
    if type(upcoming_window) is not int or not 0 <= upcoming_window <= 20:
        raise StoryError('INVALID_REQUEST', '承诺预警窗口无效。')
    settled = {memory.get('key') for memory in memories if memory.get('kind') == 'promise'
               and memory.get('status') in ('paid', 'waived')}
    obligations = []
    for promise in plan.get('promises', []):
        if not isinstance(promise, dict) or not promise.get('mandatory') or promise.get('key') in settled:
            continue
        due = promise.get('due_chapter')
        if type(due) is not int or due < 1:
            raise StoryError('INVALID_REQUEST', '承诺回收章节无效。')
        if due > chapter_number + upcoming_window:
            continue
        obligations.append({'key': promise['key'], 'due_chapter': due, 'resolution': promise['resolution'],
                            'urgency': 'due' if due <= chapter_number else 'upcoming'})
    return sorted(obligations, key=lambda item: (item['due_chapter'], item['key']))


def context_layers(memories, chapter_plan, chapter_number, supplement_limit=8):
    """Separate non-evictable story state from bounded lexical context.

    The plan's participant list is optional for legacy outlines; plan text still
    provides a deterministic fallback. This function only projects canonical
    memory and never writes or promotes a record.
    """
    if not isinstance(memories, list) or not isinstance(chapter_plan, dict) or type(chapter_number) is not int or chapter_number < 1:
        raise StoryError("INVALID_REQUEST", "上下文分层参数无效。")
    if type(supplement_limit) is not int or not 0 <= supplement_limit <= 50:
        raise StoryError("INVALID_REQUEST", "补充记忆数量无效。")
    pov = chapter_plan.get('pov')
    if pov is not None and (not isinstance(pov, str) or not pov.strip()):
        raise StoryError("INVALID_REQUEST", "章节 POV 无效。")
    pov = pov.strip() if pov else None
    participants = [name.strip() for name in chapter_plan.get('participants', []) if isinstance(name, str) and name.strip()]
    plan_text = ' '.join(value for key, value in chapter_plan.items() if key != 'participants' and isinstance(value, str))
    all_plan_terms = _terms(plan_text)
    plan_terms = sorted(all_plan_terms, key=lambda term: (-len(term), term))[:12]
    required, remaining, withheld_knowledge_count = [], [], 0
    for index, memory in enumerate(memories):
        # A limited-POV task must not receive another character's knowledge.
        # Keep a count only, so a key or value cannot leak via diagnostics.
        if pov and memory.get('kind') == 'knowledge' and memory.get('owner') and memory.get('owner') != pov:
            withheld_knowledge_count += 1
            continue
        text = _memory_search_text(memory)
        reason = None
        if memory.get('kind') == 'promise' and memory.get('status') == 'open':
            reason = 'unresolved_promise'
        elif any(name.lower() in text for name in participants):
            reason = 'participant'
        elif sum(len(term) for term in all_plan_terms if term in text) >= 4:
            reason = 'plan_term'
        item = dict(memory)
        if reason:
            item['context_reason'] = reason
            required.append(item)
        else:
            score = sum(len(term) for term in all_plan_terms if term in text)
            remaining.append((-score, -int((memory.get('source') or {}).get('chapter_number') or 0), index, item))
    remaining.sort(key=lambda row: row[:3])
    supplementary = []
    for _, _, _, memory in remaining[:supplement_limit]:
        memory['context_reason'] = 'lexical_supplement'
        supplementary.append(memory)
    return {'required': required, 'supplementary': supplementary,
            'queries': {'participants': participants, 'plan_terms': plan_terms},
            'pov_guard': {'pov': pov, 'withheld_knowledge_count': withheld_knowledge_count}}


def retrieve(store, book_id, query, role="author", through_chapter=None, limit=10):
    store.book(book_id)
    if role not in ("author", "reader") or (role == "reader" and (type(through_chapter) is not int or through_chapter < 0)):
        raise StoryError("INVALID_SCOPE", "读者检索必须给出非负整数的已读章节边界。")
    if through_chapter is not None and (type(through_chapter) is not int or through_chapter < 0):
        raise StoryError("INVALID_SCOPE", "章节边界无效。")
    if not isinstance(query, str) or len(query) > 10000 or type(limit) is not int or not 1 <= limit <= 50:
        raise StoryError("INVALID_REQUEST", "检索词或 limit 无效。")
    boundary = through_chapter if through_chapter is not None else 2**31
    with store.read() as conn:
        # Join active versions and committed status before scoring. No global index post-filter.
        rows = conn.execute("""SELECT m.* FROM memories m JOIN chapters c ON
          c.book_id=m.book_id AND c.number=m.chapter_number AND c.version_id=m.version_id
          WHERE m.book_id=? AND m.chapter_number<=? AND c.status='committed'
          AND (?='author' OR m.visibility='reader')""", (book_id, boundary, role)).fetchall()
        chunks = conn.execute("""SELECT m.* FROM chunks m JOIN chapters c ON
          c.book_id=m.book_id AND c.number=m.chapter_number AND c.version_id=m.version_id
          WHERE m.book_id=? AND m.chapter_number<=? AND c.status='committed'""", (book_id, boundary)).fetchall()
    hits = []
    terms = _terms(query)
    for row in list(rows) + list(chunks):
        item = dict(row)
        is_memory = "kind" in item
        txt = f'{item["key"]}：{item["value"]}' if is_memory else item["text"]
        aliases = json.loads(item["data"]).get("aliases", []) if is_memory else []
        search_text = _memory_search_text({'key': item['key'], 'value': item['value'], 'aliases': aliases}) if is_memory else txt.lower()
        score = sum(len(t) for t in terms if t in search_text)
        if score <= 0 and query.strip():
            continue
        source = {"version_id": item["version_id"], "chapter_number": item["chapter_number"],
                  "quote": item["evidence"] if is_memory else item["text"]}
        hits.append({"id": item["id"], "text": txt, "kind": item.get("kind", "passage"),
                     "chapter_number": item["chapter_number"], "visibility": item.get("visibility", "reader"),
                     "source": source, "score": score})
    hits.sort(key=lambda x: (x["score"], x["chapter_number"]), reverse=True)
    return {"hits": hits[:limit], "strategy": "chinese-lexical-v1", "role": role,
            "through_chapter": through_chapter, "vector_enabled": False}


def canonical_memory(conn, book_id, through_chapter):
    """Mandatory facts plus open promises: never rely on nearest-neighbor recall alone."""
    rows = conn.execute("""SELECT m.* FROM memories m JOIN chapters c ON
      c.book_id=m.book_id AND c.number=m.chapter_number AND c.version_id=m.version_id
      WHERE m.book_id=? AND m.chapter_number<=? AND c.status='committed'
      ORDER BY m.chapter_number,m.rowid""", (book_id, through_chapter)).fetchall()
    latest = {}
    for row in rows:
        item = json.loads(row["data"])
        item["source"] = {"chapter_number": row["chapter_number"], "version_id": row["version_id"], "quote": row["evidence"]}
        # Knowledge is indexed per owner; state updates supersede only the same key.
        key = (item["kind"], item["key"], item.get("owner", ""))
        latest[key] = item
    return list(latest.values())


def selected_canonical_memory(conn, book_id, through_chapter, *, kinds, limit=None):
    """Project selected legacy kinds in SQL before materializing JSON.

    Mandatory promise rows have no limit; latest status is selected before any
    status filtering so a settled obligation cannot resurrect an older event.
    The SQL still visits matching kind history; this is not an O(1) claim.
    """
    query = '''WITH ranked AS (
        SELECT m.*, ROW_NUMBER() OVER (
            PARTITION BY m.kind,m.key,COALESCE(json_extract(m.data,'$.owner'),'')
            ORDER BY m.chapter_number DESC,m.rowid DESC) AS position
        FROM memories m JOIN chapters c ON c.book_id=m.book_id
            AND c.number=m.chapter_number AND c.version_id=m.version_id
        WHERE m.book_id=? AND m.chapter_number<=? AND c.status='committed'
            AND m.kind IN (SELECT value FROM json_each(?)))
        SELECT * FROM ranked WHERE position=1 ORDER BY chapter_number DESC,id'''
    params = [book_id, through_chapter, dumps(kinds)]
    if limit is not None:
        if type(limit) is not int or not 1 <= limit <= 64:
            raise StoryError('INVALID_REQUEST', '可选记忆数量应为 1–64。')
        query += ' LIMIT ?'
        params.append(limit)
    result = []
    for row in conn.execute(query, params):
        item = json.loads(row['data'])
        item['id'] = row['id']
        item['source'] = {'chapter_number': row['chapter_number'], 'version_id': row['version_id'], 'quote': row['evidence']}
        result.append(item)
    return result


def bounded_context_layers(memories, chapter_plan, chapter_number, supplement_limit=8, *,
                           current_facts=(), scheduled_promises=()):
    """Opt-in candidate selection; ContextCompiler applies final token budgets.

    Explicit requirements and scheduled obligations are never silently capped.
    Name/outline matches rank historical evidence only, not hard constraints.
    Legacy context_layers remains the default shadow baseline.
    """
    if not isinstance(memories, list) or not isinstance(chapter_plan, dict) or type(chapter_number) is not int or chapter_number < 1:
        raise StoryError('INVALID_REQUEST', '上下文分层参数无效。')
    if type(supplement_limit) is not int or not 0 <= supplement_limit <= 50:
        raise StoryError('INVALID_REQUEST', '补充记忆数量无效。')
    pov = chapter_plan.get('pov')
    explicit = set(chapter_plan.get('required_fact_ids') or []) | set(chapter_plan.get('required_memory_ids') or [])
    terms = _terms(' '.join(v for v in chapter_plan.values() if isinstance(v, str)))
    required = [dict(item, context_reason='current_state') for item in current_facts]
    required.extend(dict(item, context_reason=item.get('context_reason', 'due_promise'), hard_constraint=True)
                    for item in scheduled_promises)
    candidates, withheld = [], 0
    for index, memory in enumerate(memories):
        if pov and memory.get('kind') == 'knowledge' and memory.get('owner') and memory['owner'] != pov:
            withheld += 1
            continue
        identity = memory.get('fact_id') or memory.get('id')
        if identity and identity in explicit:
            required.append(dict(memory, context_reason='explicit_dependency'))
            continue
        # Scheduling is based on identifiers and dates, never a participant name.
        if memory.get('kind') == 'promise':
            due = memory.get('due_chapter')
            if memory.get('status') not in ('paid', 'waived', 'resolved', 'cancelled') and type(due) is int and due <= chapter_number:
                required.append(dict(memory, context_reason='due_promise'))
            continue
        score = sum(len(t) for t in terms if t in _memory_search_text(memory))
        candidates.append((-score, -int((memory.get('source') or {}).get('chapter_number') or 0), index, memory))
    candidates.sort(key=lambda item: item[:3])
    included = {item.get('fact_id') or item.get('id') for item in required}
    return {'required': required, 'missing_required_ids': sorted(explicit - included),
            'supplementary': [dict(item[3], context_reason='lexical_supplement') for item in candidates[:supplement_limit]],
            'queries': {'participants': chapter_plan.get('participants', []), 'plan_terms': sorted(terms)[:12]},
            'pov_guard': {'pov': pov, 'withheld_knowledge_count': withheld}}
