from playwright.sync_api import sync_playwright
from note_stock.store import Store
from note_stock.discord_setup import PortalSetup
from note_stock.secrets import unprotect


def test_owner_authentication_pending_is_reported_without_collecting_password(tmp_path):
    import pytest
    store=Store(tmp_path)
    worker=PortalSetup(store)
    notices=[]
    worker.progress.connect(notices.append)
    class Visible:
        first=None
        def __init__(self): self.first=self
        def count(self): return 1
        def is_visible(self): return True
    class Page:
        def get_by_role(self,*a,**k): return Visible()
        def get_by_text(self,*a,**k): return Visible()
        def wait_for_timeout(self,milliseconds): worker.cancel.set()
    worker.page=Page()
    with pytest.raises(RuntimeError,match='中断'):
        worker.until(lambda:False,'接続を準備しています。')
    assert store.setting('discord_setup')['stage']=='owner_authentication'
    assert any('一致しません' in notice for notice in notices)


def test_portal_container_form_creation_token_encryption_and_resume(tmp_path,monkeypatch):
    store=Store(tmp_path)
    store.set_setting('discord_onboarding',{'enabled':True,'destination':'existing','developer_terms_accepted':True})
    fake_token='X'*24+'.'+'Y'*6+'.'+'Z'*30
    state={'created':0,'reset':0}
    application='123456789012345678'
    def route(route):
        url=route.request.url
        if url.endswith('/applications'):
            html='''<button onclick="document.querySelector('#form').hidden=false">新しいアプリケーション</button>
                <div id="form" hidden><input aria-label="名前 *"><label><input type="checkbox" checked>規約に同意済み（模擬）</label>
                <button onclick="fetch('/created',{method:'POST'});history.pushState({},'', '/developers/applications/'''+application+'''/information')">作成</button></div>'''
        elif url.endswith('/created'):
            state['created']+=1
            route.fulfill(body='ok')
            return
        elif url.endswith('/reset'):
            state['reset']+=1
            route.fulfill(body='ok')
            return
        elif url.endswith('/installation'):
            html='''<label><input type="checkbox" aria-label="ユーザーインストール" checked>ユーザーインストール</label>
                <label><input type="checkbox" aria-label="ギルドのインストール" checked>ギルドのインストール</label>
                <input role="combobox" aria-label="インストールリンク" onkeydown="if(event.key==='ArrowDown')document.querySelector('#none').hidden=false">
                <div id="none" role="option" hidden onclick="this.hidden=true;document.querySelector('#save').hidden=false">なし</div>
                <button id="save" hidden onclick="this.hidden=true">変更を保存</button>'''
        elif url.endswith('/bot'):
            html='''<span id="privacy-label">公開アプリは誰でもインストールできます。</span>
                <input type="checkbox" role="switch" aria-labelledby="privacy-label" checked onchange="document.querySelector('#save').hidden=false">
                <button id="save" hidden onclick="this.hidden=true">変更を保存</button>
                <button onclick="document.querySelector('#confirmation').hidden=false">トークンをリセット</button>
                <div id="confirmation" hidden><h1>Botのトークンをリセットしますか？</h1>
                <button onclick="fetch('/reset',{method:'POST'});document.querySelector('#token').value='''+"'"+fake_token+"'"+'''">実行します！</button></div>
                <input id="token" readonly>'''
        elif '/oauth2/authorize?' in url:
            html='''<script>fetch('/api/v10/oauth2/authorize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({guild_id:'234567890123456789'})})</script>'''
        elif '/api/v10/oauth2/authorize' in url:
            route.fulfill(json={'location':'https://discord.com/oauth2/authorized'})
            return
        else:
            html='<h1>Portal</h1>'
        route.fulfill(content_type='text/html;charset=utf-8',body=html)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        context=browser.new_context()
        context.route('**/*',route)
        monkeypatch.setattr('note_stock.managed_browser.connect',lambda *args:(browser,context))
        from contextlib import nullcontext
        monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext(pw))
        outputs=[]
        worker=PortalSetup(store)
        worker.outcome.connect(lambda ok,message:outputs.append(ok))
        worker.run()
        assert outputs==[True]
        assert store.setting('discord_setup')['application']==application
        encrypted=store.setting('discord_token')
        assert fake_token not in encrypted and unprotect(encrypted)==fake_token
        assert store.setting('discord_setup')['stage']=='installed'
        assert store.setting('discord')['guild']=='234567890123456789'
        assert state=={'created':1,'reset':1}
        again=PortalSetup(store)
        again.outcome.connect(lambda ok,message:outputs.append(ok))
        again.run()
        assert outputs==[True,True]
        assert state=={'created':1,'reset':1}
        browser.close()


def test_no_choice_never_starts_browser(tmp_path):
    worker=PortalSetup(Store(tmp_path))
    outcomes=[]
    worker.outcome.connect(lambda ok,msg:outcomes.append((ok,msg)))
    worker.run()
    assert outcomes and not outcomes[0][0] and '先にDiscord連携' in outcomes[0][1]
    assert not hasattr(worker,'page')


def test_destination_switch_reuses_only_its_own_resources(tmp_path):
    from note_stock.discord_setup import select_destination
    store=Store(tmp_path)
    a,b='123456789012345678','234567890123456789'
    store.set_setting('discord',{'guild':a,'channel':'one','menu':'menu-one','owner':'owner'})
    select_destination(store,b)
    assert store.setting('discord')=={'guild':b,'owner':'owner'}
    store.set_setting('discord',dict(store.setting('discord'),channel='two'))
    select_destination(store,a)
    assert store.setting('discord')['channel']=='one'
    assert store.setting('discord')['menu']=='menu-one'
    select_destination(store,b)
    assert store.setting('discord')['channel']=='two'


def test_new_server_creation_and_interrupted_creation_never_duplicate(tmp_path):
    import pytest
    store=Store(tmp_path)
    store.set_setting('discord_onboarding',{'enabled':True,'destination':'new','server_name':'My note server','guidelines_accepted':True})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page()
        created=[]
        def route(r):
            if '/api/v10/guilds' in r.request.url:
                created.append(True)
                r.fulfill(json={'id':'345678901234567890'})
            else:
                r.fulfill(content_type='text/html;charset=utf-8',body='''<div role="treeitem" aria-label="サーバーを追加">+</div><button>オリジナルの作成</button>
                    <label>サーバー名<input></label>
                    <button id="approve" onclick="fetch('/api/v10/guilds',{method:'POST'})">作成</button>''')
        page.route('**/*',route)
        worker=PortalSetup(store); worker.page=page
        assert worker.create_server()=='345678901234567890'
        assert worker.create_server()=='345678901234567890'
        assert len(created)==1
        store.set_setting('discord_setup',{'server_creation_pending':True})
        with pytest.raises(RuntimeError,match='重複作成'):
            worker.create_server()
        assert len(created)==1
        browser.close()


def test_new_server_invite_is_pinned_and_only_approved_destination_is_saved(tmp_path):
    store=Store(tmp_path)
    guild='345678901234567890'
    store.set_setting('discord_onboarding',{'enabled':True,'destination':'new','server_name':'note'})
    store.set_setting('discord_setup',{'created_guild':guild})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page()
        def route(r):
            if '/api/' in r.request.url:
                r.fulfill(json={'location':'https://discord.com/oauth2/authorized'})
            else:
                r.fulfill(content_type='text/html',body='''<script>fetch('/api/v10/oauth2/authorize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({guild_id:"'''+guild+'''"})})</script>''')
        page.route('**/*',route)
        worker=PortalSetup(store); worker.page=page
        worker.install_server('123456789012345678')
        assert 'guild_id='+guild in page.url and 'disable_guild_select=true' in page.url
        assert Store(tmp_path).setting('discord')['guild']==guild
        assert store.setting('discord_setup')['stage']=='installed'
        browser.close()


def test_owner_finishes_verification_on_another_device(tmp_path):
    store=Store(tmp_path)
    guild='345678901234567890'
    store.set_setting('discord_onboarding',{'enabled':True,'destination':'new','server_name':'note'})
    store.set_setting('discord_setup',{'created_guild':guild})
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page()
        page.route('**/*',lambda r:r.fulfill(body='<h1>Verification completed on phone</h1>'))
        worker=PortalSetup(store); worker.page=page
        worker.guild_joined=lambda gid:gid==guild
        worker.install_server('123456789012345678')
        assert store.setting('discord')['guild']==guild
        assert store.setting('discord_setup')['stage']=='installed'
        browser.close()
