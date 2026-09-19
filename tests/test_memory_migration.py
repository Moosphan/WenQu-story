"""Upgrade a database containing only the pre-normalization tables."""
import sqlite3
import pytest

from story_core.storage import SCHEMA, Store


@pytest.mark.parametrize('old_version', [7, 8, 9])
def test_upgrade_from_real_legacy_table_set_keeps_manuscript_and_memory(tmp_path, old_version):
    path = tmp_path / 'project.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute(f'PRAGMA user_version={old_version}')
        conn.execute("INSERT INTO books(id,title,request,config,created_at) VALUES ('b','fixture','synthetic','{}',0)")
        conn.execute("INSERT INTO chapter_versions VALUES ('v','b',1,'fixture','铜牌还未归还。',0)")
        conn.execute("INSERT INTO chapters VALUES ('b',1,'v','committed')")
        conn.execute("INSERT INTO memories VALUES ('m','b',1,'v','fact','铜牌','未归还','铜牌','reader','{}')")
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'lm_%'").fetchone()
    store = Store(tmp_path)
    for _ in range(2):
        store = Store(tmp_path)
        with store.read() as conn:
            assert conn.execute('PRAGMA user_version').fetchone()[0] == 10
            assert conn.execute('SELECT body FROM chapter_versions').fetchone()[0] == '铜牌还未归还。'
            assert conn.execute('SELECT id FROM memories').fetchone()[0] == 'm'
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='lm_promise_status_events'").fetchone()
            assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
