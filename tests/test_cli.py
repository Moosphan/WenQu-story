import json
import os
import subprocess
import sys
from test_workflow import result_for


def cli(root, *args):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    return subprocess.run(
        [sys.executable, "-m", "story_core.cli", "--root", str(root), *args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def output(process):
    return json.loads(process.stdout)


def test_cli_persists_book_across_processes_and_exports(tmp_path):
    created = cli(tmp_path, "init", "--request", "写一本都市悬疑", "--title", "旧收音机", "--chapters", "2", "--words", "800")
    assert created.returncode == 0, created.stderr
    book_id = output(created)["book_id"]

    listed = cli(tmp_path, "books")
    assert listed.returncode == 0, listed.stderr
    assert [book["book_id"] for book in output(listed)] == [book_id]

    exported = cli(tmp_path, "export", book_id, "--allow-partial")
    assert exported.returncode == 0, exported.stderr
    assert output(exported)["book_id"] == book_id


def test_cli_reads_request_and_submit_result_files(tmp_path):
    request = tmp_path / "request.txt"
    request.write_text("写一本带旧收音机的都市悬疑", encoding="utf-8")
    created = cli(tmp_path, "init", "--request-file", str(request), "--chapters", "1")
    assert created.returncode == 0, created.stderr
    book_id = output(created)["book_id"]
    assert cli(tmp_path, "start", book_id, "--chapters", "1").returncode == 0
    task = output(cli(tmp_path, "next", book_id, "--worker", "cli-test"))
    result = tmp_path / "result.json"
    result.write_text(json.dumps(result_for(task)), encoding="utf-8")
    submitted = cli(tmp_path, "submit", task["task_id"], "--lease", task["lease_id"], "--result-file", str(result), "--worker", "cli-test")
    assert submitted.returncode == 0, submitted.stderr
    assert output(submitted)["accepted"] is True


def test_cli_emits_structured_story_error_on_stderr(tmp_path):
    failed = cli(tmp_path, "show", "missing")
    assert failed.returncode != 0
    error = json.loads(failed.stderr)
    assert error["error"]["code"] == "NOT_FOUND"
    assert not failed.stdout


def test_cli_rejects_unknown_flags(tmp_path):
    failed = cli(tmp_path, "books", "--surprise")
    assert failed.returncode != 0
    assert "unrecognized arguments" in failed.stderr


def test_cli_import_json_file(tmp_path):
    book = output(cli(tmp_path, 'init', '--request', '开书'))
    source = tmp_path / 'chapters.json'
    source.write_text(json.dumps([{'chapter_number': 1, 'title': '山门', 'body': '未审正文。'}]))
    imported = cli(tmp_path, 'import', book['book_id'], '--chapters-file', str(source))
    assert imported.returncode == 0, imported.stderr
    assert output(imported)['imported'] == 1
    shown = output(cli(tmp_path, 'show', book['book_id']))
    assert shown['chapters'][0]['status'] == 'imported'


def test_cli_saves_version_bound_human_feedback(tmp_path):
    from test_workflow import finish, make

    service, book = make(tmp_path, chapters=1)
    finish(service, book)
    response = cli(tmp_path, 'feedback', book, '--chapter', '1', '--type', 'editor', '--rating', '4',
                   '--would-continue', '--notes', '冲突清楚，建议让代价更早落地。')
    assert response.returncode == 0, response.stderr
    assert output(response)['reviewer_type'] == 'editor'
    history = output(cli(tmp_path, 'reviews', book))
    assert any(item['source'] == 'human' and item['rating'] == 4 for item in history)


def test_cli_reads_and_updates_project_metadata(tmp_path):
    book = output(cli(tmp_path, 'init', '--request', '开书'))
    metadata = tmp_path / 'project.json'
    metadata.write_text(json.dumps({'platform': 'fanqie', 'genre_tags': ['修仙', '种田']}), encoding='utf-8')
    updated = cli(tmp_path, 'project', book['book_id'], '--metadata-file', str(metadata), '--expected-revision', '0')
    assert updated.returncode == 0, updated.stderr
    assert output(updated)['project']['platform'] == 'fanqie'
    assert output(cli(tmp_path, 'project', book['book_id']))['project']['genre_tags'] == ['修仙', '种田']
