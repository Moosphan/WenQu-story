import zipfile
from xml.etree import ElementTree

from test_workflow import make, finish


def test_repeated_exports_unique_and_hashed(tmp_path):
    service,book=make(tmp_path)
    finish(service,book)
    first=service.export(book)
    second=service.export(book)
    assert first['export_id']!=second['export_id']
    assert all(item['sha256'] for item in first['files'])


def test_complete_export_contains_valid_epub3_package(tmp_path):
    from pathlib import Path

    service, book = make(tmp_path)
    finish(service, book)
    manifest = service.export(book)
    epub = Path(manifest['path']) / 'book.epub'
    assert any(item['name'] == 'book.epub' for item in manifest['files'])
    with zipfile.ZipFile(epub) as package:
        names = package.namelist()
        assert names[0] == 'mimetype'
        assert package.getinfo('mimetype').compress_type == zipfile.ZIP_STORED
        assert package.read('mimetype') == b'application/epub+zip'
        assert 'META-INF/container.xml' in names
        assert 'OEBPS/content.opf' in names
        assert 'OEBPS/nav.xhtml' in names
        assert 'OEBPS/chapters/chapter-001.xhtml' in names
        assert '第1章 回音' in package.read('OEBPS/chapters/chapter-001.xhtml').decode('utf-8')
        for filename in ('META-INF/container.xml', 'OEBPS/content.opf', 'OEBPS/nav.xhtml', 'OEBPS/chapters/chapter-001.xhtml'):
            ElementTree.fromstring(package.read(filename))


def test_partial_export_never_contains_epub(tmp_path):
    from pathlib import Path

    service, book = make(tmp_path)
    manifest = service.export(book, allow_partial=True)
    assert not manifest['complete']
    assert not any(item['name'] == 'book.epub' for item in manifest['files'])
    assert not (Path(manifest['path']) / 'book.epub').exists()


def test_partial_export_is_labelled_in_manuscript(tmp_path):
    from pathlib import Path
    service,book=make(tmp_path)
    manifest=service.export(book,allow_partial=True)
    assert not manifest['complete']
    assert '未完稿' in (Path(manifest['path'])/'manuscript.txt').read_text()


def test_invalid_brief_never_promoted_to_canon(tmp_path):
    import pytest
    from story_core.errors import StoryError
    service,book=make(tmp_path)
    task=service.next_task(book)
    with pytest.raises(StoryError):
        service.submit_task(task['task_id'],task['lease_id'],{'title':'标题','body':'这不是设定'})
    assert service.get_book(book)['brief'] is None
