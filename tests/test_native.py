"""Native note is the editor. These tests prove payload preservation, not live note UI behavior."""
import copy
import html
import json
import time

import pytest
from playwright.sync_api import sync_playwright

from note_stock.native import NativeNoteBrowser, EDITOR_FEATURES, body_fingerprint
from note_stock.store import Store

KEY='n123456789abc'
API='https://note.com/api/v1/text_notes/12345'
RICH_HTML='''<h2>大見出し</h2><h3>小見出し</h3><p><strong>太字</strong><em>強調</em><s>装飾</s><a href="https://example.com">リンク</a></p>
<p style="text-align:center">中央配置</p><ul><li>箇条書き</li></ul><ol><li>番号付き</li></ol><blockquote>引用</blockquote><pre><code>print("code")</code></pre><hr>
<figure><img src="https://assets.example/image.png" alt="本文画像"><figcaption>説明</figcaption></figure>
<audio src="https://assets.example/audio.mp3" controls></audio><iframe src="https://example.com/embed"></iframe>
<div data-attachment-key="file-key">ファイル</div><figure data-comic="true">コミック</figure><table-of-contents>目次</table-of-contents>'''
PAY_HTML='<p><strong>有料部分も装飾を保持</strong></p><img src="https://assets.example/paid.png">'
DOC={'type':'doc','content':[{'type':'native-html','attrs':{'html':RICH_HTML+PAY_HTML}},
                           {'type':'future_note_feature','attrs':{'nested':[1,2,{'keep':'unchanged'}]}}]}
PAYLOAD={'author_ids':[],'body_length':100,'disable_comment':True,'exclude_from_creator_top':False,
         'free_body':RICH_HTML,'pay_body':PAY_HTML,'hashtags':['#日記','#エッセイ'],
         'image_keys':['image-key','paid-image-key'],'index':True,'is_refund':False,'limited':False,
         'magazine_ids':[12,34],'magazine_keys':['magazine-key'],'name':'装飾と素材を含む原稿',
         'price':399,'send_notifications_flag':False,'separator':'native-separator','slug':'',
         'status':'published','circle_permissions':[{'kind':'circle_plan','keys':['plan-key']}],
         'discount_campaigns':[{'kind':'time_sale','discounted_price':299}],
         'translation_setting':{'enabled':True},'line_add_friend_access_token':'secret-test-token',
         'future_setting':{'unknown_native_field':['must','survive']}}


@pytest.fixture(params=['fetch','xhr'])
def native_site(tmp_path,monkeypatch,request):
    transport_mode=request.param
    store=Store(tmp_path)
    store.set_setting('account','tester')
    store.set_setting('account_verified',True)
    state={'payload':copy.deepcopy(PAYLOAD),'sent':[],'auto':True,'published':False,'reject':False,'changed':False}
    monkeypatch.setattr('note_stock.native.load_public_html',lambda url:(200 if state['published'] else 404,url,
        '<h1>装飾と素材を含む原稿</h1>'+RICH_HTML+'<a href="/hashtag/日記">#日記</a><a href="/hashtag/エッセイ">#エッセイ</a>'))
    def route(route):
        request=route.request
        url=request.url
        if url==API and request.method=='PUT':
            state['sent'].append(request.post_data_json)
            state['published']=not state['reject']
            route.fulfill(status=200,content_type='application/json',body=json.dumps(
                {'error':{'message':'rejected'}} if state['reject'] else {'data':{'accepted':True,'status':request.post_data_json['status']}}))
            return
        if '/settings/account/note_id' in url:
            content='<input aria-label="note ID" value="tester">'
        elif '/notes/' in url or url.endswith('/new'):
            auto=state['auto']
            state['auto']=False
            doc=copy.deepcopy(DOC)
            if state['changed']:
                doc['content'].append({'type':'paragraph','content':[{'type':'text','text':'external change'}]})
            payload=json.dumps(state['payload'] if auto else {'status':'published','price':0,'free_body':'carrier, must not be used','pay_body':'','hashtags':[]},ensure_ascii=False)
            transport=("fetch("+json.dumps(API)+",{method:'PUT',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify(window.nativePayload)}).catch(()=>{})"
                       if transport_mode=='fetch' else
                       "(()=>{const xhr=new XMLHttpRequest();xhr.open('PUT',"+json.dumps(API)+");xhr.setRequestHeader('Content-Type','application/json');xhr.send(JSON.stringify(window.nativePayload));})()")
            content='''<html><meta charset="utf-8"><textarea placeholder="記事タイトル">装飾と素材を含む原稿</textarea>
                <div class="ProseMirror" contenteditable="true">'''+RICH_HTML+PAY_HTML+'''</div>
                <button onclick="openSettings()">公開に進む</button><script>
                history.replaceState({},'', '/notes/'''+KEY+'''/edit/');
                document.querySelector('.ProseMirror').pmViewDesc={node:{toJSON:()=>('''+json.dumps(doc,ensure_ascii=False)+''')}};
                function openSettings(){history.pushState({},'','/notes/'''+KEY+'''/publish/');
                  document.body.innerHTML='<label><input type="radio" name="is_paid" id="free" checked>無料</label><button id="finish">投稿する</button>';
                  document.querySelector('#finish').onclick=()=>{'''+transport+'''};
                }
                window.nativePayload='''+payload+''';
                window.autoSubmit='''+json.dumps(auto)+''';</script></html>'''
        elif '/tester/n/' in url and state['published']:
            content='<h1>装飾と素材を含む原稿</h1>'+RICH_HTML+'<a href="/hashtag/日記">#日記</a><a href="/hashtag/エッセイ">#エッセイ</a>'
        elif 'assets.example' in url or 'example.com' in url:
            route.fulfill(status=200,body='')
            return
        else:
            route.fulfill(status=404,body='not found')
            return
        route.fulfill(status=200,content_type='text/html; charset=utf-8',body=content)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        original=browser.new_context
        def context(*args,**kwargs):
            ctx=original(*args,**kwargs)
            ctx.route('**/*',route)
            return ctx
        monkeypatch.setattr(browser,'new_context',context)
        adapter=NativeNoteBrowser(store)
        def ready(message):
            if message.startswith('note本来の編集画面'):
                adapter.page.evaluate("if(window.autoSubmit){window.autoSubmit=false;openSettings();document.querySelector('#finish').click()}")
        adapter.progress=ready
        started=time.monotonic()
        adapter.cancelled=lambda:time.monotonic()-started>12
        adapter.context=browser.new_context()
        adapter.page=adapter.context.new_page()
        adapter.context.set_default_timeout(2000)
        yield adapter,store,state
        browser.close()


def prepare(adapter,store):
    pid=store.create()
    post=store.begin_native_edit(pid)
    adapter.edit(post)
    return store.get(pid)


def test_native_capture_blocks_publication_and_keeps_all_html_and_unknown_settings(native_site):
    adapter,store,state=native_site
    post=prepare(adapter,store)
    assert state['sent']==[]
    assert post['native_ready']==1 and post['status']=='draft'
    assert post['native_html']==RICH_HTML+PAY_HTML
    assert store.native_template(post['id'])['payload']==PAYLOAD
    assert post['native_fingerprint']==body_fingerprint({'kind':'prosemirror','value':DOC})
    assert 'native_payload' not in post
    with store.connect() as c:
        encrypted=c.execute('SELECT native_payload FROM posts WHERE id=?',(post['id'],)).fetchone()[0]
    assert 'secret-test-token' not in encrypted and RICH_HTML not in encrypted
    assert len(EDITOR_FEATURES)>=20


def test_native_publish_does_not_strip_formatting_media_paid_or_future_fields(native_site):
    adapter,store,state=native_site
    post=prepare(adapter,store)
    adapter.page=adapter.context.new_page()
    url=adapter.publish(post)
    assert url.endswith(KEY)
    assert state['sent']==[PAYLOAD]
    assert state['sent'][0]['price']==399
    assert state['sent'][0]['free_body']==RICH_HTML
    assert state['sent'][0]['pay_body']==PAY_HTML
    assert store.get(post['id'])['submitted']==1


def test_application_tags_change_only_native_hashtags(native_site):
    adapter,store,state=native_site
    post=prepare(adapter,store)
    store.save(post['id'],post['title'],post['body'],['新しいタグ'],'','draft')
    payload=store.native_template(post['id'])['payload']
    expected=copy.deepcopy(PAYLOAD)
    expected['hashtags']=['#新しいタグ']
    assert payload==expected


def test_native_metadata_save_preserves_trailing_newlines_and_title_spacing(native_site):
    adapter,store,state=native_site
    post=prepare(adapter,store)
    with store.connect() as connection:
        connection.execute('UPDATE posts SET title=?,body=? WHERE id=?',(' '+post['title']+' ',post['body']+'\n\n',post['id']))
    post=store.get(post['id'])
    store.save(post['id'],post['title'],post['body'],['予約確認'],'','scheduled',time.time()+60)
    saved=store.get(post['id'])
    assert saved['body']==post['body'] and saved['title']==post['title']
    assert saved['status']=='scheduled'
    expected=copy.deepcopy(PAYLOAD)
    expected['hashtags']=['#予約確認']
    assert store.native_template(post['id'])['payload']==expected


def test_changed_native_draft_is_never_overwritten_or_published(native_site):
    adapter,store,state=native_site
    post=prepare(adapter,store)
    adapter.page=adapter.context.new_page()
    state['changed']=True
    with pytest.raises(Exception,match='変更されています'):
        adapter.publish(post)
    assert state['sent']==[]


def test_native_server_rejection_is_not_a_success(native_site):
    adapter,store,state=native_site
    post=prepare(adapter,store)
    adapter.page=adapter.context.new_page()
    state['reject']=True
    with pytest.raises(Exception,match='受け付けません'):
        adapter.publish(post)
    assert len(state['sent'])==1


def test_native_unprepared_document_cannot_be_queued_or_posted(tmp_path):
    store=Store(tmp_path)
    pid=store.create()
    with pytest.raises(ValueError,match='取り込'):
        store.save(pid,'原稿','本文',[],'','queued')
    with pytest.raises(ValueError,match='取り込'):
        store.claim(100,post_id=pid)


def test_duplicate_preserves_native_document_and_requires_new_capture(tmp_path):
    store=Store(tmp_path)
    document={'kind':'prosemirror','value':DOC}
    pid=store.create({'title':'コピー元','native_html':RICH_HTML+PAY_HTML,'native_document':document})
    copy_id=store.create(store.get(pid))
    copied=store.get(copy_id)
    assert json.loads(copied['native_document'])==document
    assert copied['native_html']==RICH_HTML+PAY_HTML
    assert copied['native_ready']==0 and copied['draft_key']==''


def test_stock_publication_is_blocked_even_if_document_restore_fails(native_site):
    adapter,store,state=native_site
    pid=store.create({'native_html':RICH_HTML,'native_document':{'kind':'prosemirror','value':DOC}})
    with pytest.raises(Exception,match='複製'):
        adapter.edit(store.begin_native_edit(pid))
    adapter.page.evaluate("openSettings();document.querySelector('#finish').click()")
    adapter.page.wait_for_timeout(1300)
    assert state['sent']==[]
    assert store.get(pid)['native_ready']==0
