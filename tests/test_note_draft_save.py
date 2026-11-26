import json
import pytest
from playwright.sync_api import sync_playwright
from note_stock.store import Store
from note_stock.native import NativeNoteBrowser,content_snapshot


def test_save_records_key_before_native_save_leaves_editor_and_reopens_to_verify(tmp_path,monkeypatch):
    store=Store(tmp_path)
    store.set_setting('account','tester')
    pid=store.create({'title':'保存テスト','body':'本文の保存確認','tags':['日記']})
    key='n123456789abc'
    state={'saved':False,'title':'','html':'','publications':0}
    def route(route):
        request=route.request
        if '/draft_save' in request.url:
            assert store.get(pid)['draft_key']==key
            value=request.post_data_json
            state.update(saved=True,title=value['title'],html=value['html'])
            route.fulfill(content_type='application/json',body='{"data":{"saved":true}}')
        elif request.method=='PUT':
            state['publications']+=1
            route.fulfill(content_type='application/json',body='{}')
        elif request.url.endswith('/notes'):
            route.fulfill(content_type='text/html',body='<h1>記事一覧</h1>')
        else:
            title=state['title'] if state['saved'] else ''
            html=state['html'] if state['saved'] else ''
            content='''<textarea placeholder="記事タイトル">'''+title+'''</textarea>
                <div class="ProseMirror" contenteditable="true">'''+html+'''</div>
                <button onclick="save()">下書き保存</button><script>
                history.replaceState({},'', '/notes/'''+key+'''/edit/');
                async function save(){await fetch('https://note.com/api/v1/text_notes/draft_save',{method:'POST',
                  headers:{'Content-Type':'application/json'},body:JSON.stringify({title:document.querySelector('textarea').value,
                  html:document.querySelector('.ProseMirror').innerHTML})});location.href='https://note.com/notes';}
                </script>'''
            route.fulfill(content_type='text/html;charset=utf-8',body=content)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        context=browser.new_context()
        context.route('**/*',route)
        adapter=NativeNoteBrowser(store)
        adapter.context=context
        adapter.page=context.new_page()
        adapter.check_account=lambda:'tester'
        def capture(post,automatic=False):
            pytest.fail('下書き保存で本文・公開条件を取得してはいけません')
        monkeypatch.setattr(adapter,'edit',capture)
        assert adapter.save_draft(store.get(pid))==key
        saved=store.get(pid)
        assert not saved['native_ready'] and saved['body']=='本文の保存確認'
        assert saved['tags']==['日記']
        assert state['saved']
        # Saving an existing scheduled/queued draft must not turn a missing
        # catalog timestamp into an external-change/review state.
        store.merge_catalog([{'key':key,'title':'保存テスト','status':'draft','owner':'tester','updated':'known-revision'}])
        store.save_fields(pid,{'status':'scheduled','due':9999999999})
        store.save_fields(pid,{'status':'queued','due':None})
        adapter.page=context.new_page()
        assert adapter.save_draft(store.get(pid))==key
        assert store.get(pid)['status']=='queued'
        assert store.get(pid)['tags']==['日記']
        assert store.catalog_state(pid)['remote_updated']=='known-revision'
        assert state['publications']==0
        browser.close()


def test_full_catalog_paginates_without_draft_filter_and_reports_completion(tmp_path):
    from types import SimpleNamespace
    from note_stock.drafts import read_catalog
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        context=browser.new_context()
        page=context.new_page()
        def route(route):
            if '/api/' in route.request.url:
                last=route.request.url.endswith('2')
                row={'key':'n123456789abd' if last else 'n123456789abc','name':'公開記事' if last else '下書き','status':'published' if last else 'draft'}
                route.fulfill(content_type='application/json',body=json.dumps({'data':{'notes':[row],'isLastPage':last}}))
            else:
                route.fulfill(content_type='text/html;charset=utf-8',body='''<button onclick="window.filterClicked=true">下書き</button>
                    <button id="more" onclick="fetch('/api/list?page=2');this.remove()">もっと見る</button>
                    <script>fetch('/api/list?page=1')</script>''')
        page.route('**/*',route)
        adapter=SimpleNamespace(page=page,CORE='https://note.com',check_account=lambda:None,cancelled=lambda:False,progress=lambda _:None)
        result=read_catalog(adapter)
        assert result['complete'],result
        assert {r['status'] for r in result['items']}=={'draft','published'}
        browser.close()
