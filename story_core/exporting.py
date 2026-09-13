import json
import time
import hashlib
import shutil
import zipfile
from html import escape
from pathlib import Path

from .errors import StoryError
from .storage import uid


def _xhtml_document(title, body):
    paragraphs = [part.strip() for part in body.replace('\r\n', '\n').split('\n\n') if part.strip()]
    rendered = ''.join(
        f"    <p>{escape(part, quote=False).replace(chr(10), '<br />')}</p>\n"
        for part in paragraphs
    ) or '    <p></p>\n'
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">
  <head><title>{escape(title)}</title><meta charset="utf-8" /></head>
  <body>
    <h1>{escape(title)}</h1>
{rendered}  </body>
</html>
'''


def _write_epub(path, book, chapters, export_id):
    chapter_items = []
    spine_items = []
    navigation = []
    chapter_documents = []
    for chapter in chapters:
        number = chapter['chapter_number']
        item_id = f'chapter-{number:03d}'
        filename = f'chapters/{item_id}.xhtml'
        chapter_title = f"第{number}章 {chapter['title']}"
        chapter_items.append(f'    <item id="{item_id}" href="{filename}" media-type="application/xhtml+xml" />')
        spine_items.append(f'    <itemref idref="{item_id}" />')
        navigation.append(f'        <li><a href="{filename}">{escape(chapter_title)}</a></li>')
        chapter_documents.append((filename, _xhtml_document(chapter_title, chapter['body'])))

    title = escape(book['title'])
    modified = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" xml:lang="zh-CN">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:hulk-story:{escape(export_id)}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:language>zh-CN</dc:language>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
{chr(10).join(chapter_items)}
  </manifest>
  <spine>
{chr(10).join(spine_items)}
  </spine>
</package>
'''
    nav = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN" lang="zh-CN">
  <head><title>{title}</title><meta charset="utf-8" /></head>
  <body>
    <nav epub:type="toc" id="toc" role="doc-toc">
      <h1>{title}</h1>
      <ol>
{chr(10).join(navigation)}
      </ol>
    </nav>
  </body>
</html>
'''
    container = '''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" /></rootfiles>
</container>
'''
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        mimetype = zipfile.ZipInfo('mimetype')
        mimetype.compress_type = zipfile.ZIP_STORED
        package.writestr(mimetype, 'application/epub+zip')
        package.writestr('META-INF/container.xml', container)
        package.writestr('OEBPS/content.opf', opf)
        package.writestr('OEBPS/nav.xhtml', nav)
        for filename, document in chapter_documents:
            package.writestr(f'OEBPS/{filename}', document)


def export_book(service, book_id, allow_partial=False):
    book = service.get_book(book_id)
    complete = book['status'] == 'complete' and len(book['chapters']) == book['settings']['chapter_count'] and all(c['status'] == 'committed' for c in book['chapters'])
    if not complete and not allow_partial:
        raise StoryError('INCOMPLETE_EXPORT', '全书尚未通过完结审计，不能导出成品。')
    export_id = uid('export')
    folder = service.store.root / 'exports' / book_id / export_id
    staging = folder.with_name('.' + export_id)
    staging.mkdir(parents=True, exist_ok=False)
    chapters = sorted(book['chapters'], key=lambda x: x['chapter_number'])
    manuscript = '\n\n'.join(f"第{c['chapter_number']}章 {c['title']}\n\n{c['body']}" for c in chapters)
    markdown = '\n\n'.join(f"## 第{c['chapter_number']}章 {c['title']}\n\n{c['body']}" for c in chapters)
    if not complete:
        manuscript = '【未完稿 · 不用于发布】\n\n' + manuscript
        markdown = '> 未完稿 · 不用于发布\n\n' + markdown
    payload = {'book_id': book_id, 'title': book['title'], 'revision': book['revision'], 'complete': complete,
               'chapters': [{'number': c['chapter_number'], 'title': c['title'], 'version_id': c['version_id']} for c in chapters]}
    (staging / 'manuscript.txt').write_text(f"{book['title']}\n\n{manuscript}", encoding='utf-8')
    (staging / 'manuscript.md').write_text(f"# {book['title']}\n\n{markdown}", encoding='utf-8')
    if complete:
        _write_epub(staging / 'book.epub', book, chapters, export_id)
    files = [{'name': p.name, 'path': str(folder/p.name), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(staging.iterdir())]
    manifest = {'export_id': export_id, 'book_id': book_id, 'path': str(folder), 'revision': book['revision'], 'complete': complete, 'files': files}
    (staging / 'manifest.json').write_text(json.dumps({**payload,**manifest}, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        with service.store.write(book_id,expected_revision=book['revision']) as conn:
            conn.execute('INSERT INTO exports VALUES (?,?,?,?,?)', (export_id, book_id, book['revision'], json.dumps(manifest, ensure_ascii=False), time.time()))
            staging.rename(folder)
    except BaseException:
        shutil.rmtree(staging,ignore_errors=True)
        shutil.rmtree(folder,ignore_errors=True)
        raise
    return manifest
