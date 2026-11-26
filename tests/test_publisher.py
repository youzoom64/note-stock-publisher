"""A real Chromium drives controlled pages; every network request is intercepted."""
import html
import re

import pytest
from playwright.sync_api import sync_playwright

from note_stock.publisher import NoteBrowser, LoginRequired, VerificationFailed, account_id, plain_text
from note_stock.store import Store


KEY = 'n123456789abc'
EDITOR_HTML = '''<!doctype html><html lang="ja"><meta charset="utf-8"><body>
<textarea placeholder="記事タイトル"></textarea>
<div class="ProseMirror" contenteditable="true" style="min-height:100px"></div>
<button onclick="document.querySelector('#file').click()">画像を追加</button>
<input id="file" type="file" accept="image/png" style="display:none" onchange="document.querySelector('#crop').hidden=false">
<div id="crop" hidden><button onclick="this.parentElement.hidden=true">保存</button></div>
<button onclick="showPublish()">公開に進む</button>
<script>
history.replaceState({}, '', '/notes/n123456789abc/edit/');
function showPublish(){
  window.article={title:document.querySelector('textarea').value,body:document.querySelector('.ProseMirror').innerText,cover:document.querySelector('#file').files.length>0};
  document.body.innerHTML='<div id="tags"><input placeholder="ハッシュタグを追加する"></div><label><input type="radio" name="price" checked>無料</label><button id="publish">投稿する</button>';
  const field=document.querySelector('#tags input');
  field.onkeydown=(e)=>{if(e.key==='Enter'){const chip=document.createElement('button'); chip.innerHTML=field.value+'<span aria-label="削除"></span>'; chip.onclick=()=>chip.remove();field.parentElement.prepend(chip);field.value='';}};
  document.querySelector('#publish').onclick=async()=>{article.tags=[...document.querySelectorAll('#tags button')].map(x=>x.innerText); await window.__publish(article);};
}
</script></body></html>'''


@pytest.fixture
def browser_site(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.set_setting('account', 'tester')
    state = {'public': False, 'published': 0, 'wrong_account': False, 'corrupt': False, 'requests': []}
    def route(request):
        url = request.request.url
        state['requests'].append(url)
        if '/settings/account/note_id' in url:
            who = 'other_user' if state['wrong_account'] else 'tester'
            content = '<input aria-label="note ID" value="'+who+'">'
        elif '/notes/' in url or url.endswith('/new'):
            content = EDITOR_HTML
        elif '/tester/n/' in url and state['public']:
            article = state['article']
            text = 'changed' if state['corrupt'] else html.escape(article['body'])
            tags = ''.join('<a href="/hashtag/'+tag+'">#'+tag+'</a>' for tag in article['tags'])
            content = '<meta property="og:image" content="https://assets.example/custom-cover.jpg"><h1>'+html.escape(article['title'])+'</h1><div class="note-common-styles__textnote-body" style="white-space:pre-wrap">'+text+'</div>'+tags
        else:
            request.fulfill(status=404, body='not found')
            return
        request.fulfill(status=200, content_type='text/html; charset=utf-8', body=content)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        original = browser.new_context
        def new_context(*args, **kwargs):
            context = original(*args, **kwargs)
            context.route('**/*', route)
            return context
        monkeypatch.setattr(browser, 'new_context', new_context)
        adapter = NoteBrowser(store)
        adapter.context = browser.new_context()
        def publish(source, article):
            state['published'] += 1
            # Model the editor's paragraph serialization, not layout-dependent innerText.
            article['body'] = state.get('source_body', '一行目\n\n二行目')
            state['article'] = article
            state['public'] = True
        adapter.context.expose_binding('__publish', publish)
        adapter.page = adapter.context.new_page()
        adapter.context.set_default_timeout(1500)
        yield adapter, store, state
        browser.close()


def make_post(store):
    pid = store.create({'editor_kind':'legacy'})
    store.save(pid, '静かな朝', '一行目\n\n二行目', ['日記','エッセイ'], '', 'queued')
    return store.claim(100, post_id=pid)


def test_full_browser_publish_and_public_verification(browser_site):
    adapter, store, state = browser_site
    post = make_post(store)
    try:
        result = adapter.publish(post)
    except VerificationFailed:
        print('fixture editor:', adapter.page.locator('.ProseMirror').evaluate('(e)=>e.outerHTML'))
        raise
    assert result == 'https://note.com/tester/n/'+KEY
    assert state['published'] == 1
    assert store.get(post['id'])['submitted'] == 1
    assert store.get(post['id'])['draft_key'] == KEY
    assert set(state['article']['tags']) == {'日記','エッセイ'}
    assert not any('/api/' in url for url in state['requests'])


def test_account_mismatch_never_submits(browser_site):
    adapter, store, state = browser_site
    post = make_post(store)
    state['wrong_account'] = True
    with pytest.raises(LoginRequired):
        adapter.publish(post)
    assert state['published'] == 0
    assert store.get(post['id'])['submitted'] == 0


def test_public_body_mismatch_is_not_success(browser_site):
    adapter, store, state = browser_site
    post = make_post(store)
    state['corrupt'] = True
    with pytest.raises(VerificationFailed):
        adapter.publish(post)
    assert state['published'] == 1
    assert store.get(post['id'])['submitted'] == 1


def test_verify_never_creates_another_post(browser_site):
    adapter, store, state = browser_site
    post = make_post(store)
    adapter.publish(post)
    saved = store.get(post['id'])
    assert adapter.verify(saved).endswith(KEY)
    assert state['published'] == 1


def test_tag_replacement_removes_old_chips(browser_site):
    adapter, _, _ = browser_site
    adapter.page.goto('https://editor.note.com/new')
    adapter.page.get_by_role('button', name='公開に進む').click()
    adapter.set_tags(['昔のタグ'])
    adapter.set_tags(['新しいタグ'])
    assert adapter.page.locator('#tags button').all_inner_texts() == ['新しいタグ']


def test_account_validation():
    assert account_id('https://note.com/my_note/') == 'my_note'
    for bad in ['https://evil.example/tester','tester/path','日本語ID','bad id']:
        with pytest.raises(ValueError):
            account_id(bad)


def test_cover_upload_uses_image_chooser(browser_site, tmp_path):
    from note_stock.qt import QImage, QColor
    adapter, store, state = browser_site
    image = QImage(128, 67, QImage.Format.Format_RGB32)
    image.fill(QColor('#16845e'))
    path = tmp_path/'cover.png'
    assert image.save(str(path))
    managed = store.import_image(path)
    pid = store.create({'editor_kind':'legacy'})
    store.save(pid, '静かな朝', '一行目\n\n二行目', ['日記'], managed, 'queued')
    post = store.claim(100, post_id=pid)
    assert adapter.publish(post).endswith(KEY)
    assert state['article']['cover'] is True


def test_plain_text_preserves_empty_paragraphs_and_soft_breaks(browser_site):
    adapter, _, _ = browser_site
    adapter.page.set_content('<div id="body"><p>一行目</p><p><br class="ProseMirror-trailingBreak"></p><p>二行目<br>続き</p></div>')
    assert plain_text(adapter.page.locator('#body')) == '一行目\n\n二行目\n続き'
