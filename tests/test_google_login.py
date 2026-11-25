from types import SimpleNamespace
from note_stock.store import Store


def test_google_login_opens_regular_owned_browser_without_debugging(tmp_path,monkeypatch):
    import note_stock.managed_browser as managed
    monkeypatch.setattr(managed,'shutdown',lambda root:{'closed':True})
    monkeypatch.setattr(managed,'select_browser',lambda pw,preference:('chrome','test-chrome'))
    launched=[]
    def launch(command,**kwargs):
        launched.append(command)
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(managed.subprocess,'Popen',launch)
    monkeypatch.setattr(managed.psutil,'Process',lambda pid:SimpleNamespace(pid=pid,create_time=lambda:100))
    managed.interactive_login(None,tmp_path)
    assert len(launched)==1
    assert 'https://note.com/login' in launched[0]
    assert '--user-data-dir='+str((tmp_path/'browser-profiles'/'chrome').resolve()) in launched[0]
    assert not any('remote-debugging' in item or 'automation' in item for item in launched[0])
    assert (tmp_path/'browser-owner-chrome.json').exists()


def test_automation_cannot_attach_while_manual_google_login_is_open(tmp_path,monkeypatch):
    import json
    import pytest
    import note_stock.managed_browser as managed
    profile=tmp_path/'browser-profiles'/'chrome'; profile.mkdir(parents=True)
    (tmp_path/'browser-owner-chrome.json').write_text(json.dumps({'mode':'interactive_login'}))
    monkeypatch.setattr(managed,'select_browser',lambda *a:('chrome','test-chrome'))
    monkeypatch.setattr(managed,'owned_process',lambda *a:object())
    with pytest.raises(RuntimeError,match='Googleログイン用'):
        managed.connect(None,tmp_path)


def test_google_login_verifies_only_after_regular_browser_is_closed(tmp_path,monkeypatch):
    from note_stock.worker import BrowserWorker
    store=Store(tmp_path); store.set_setting('note_login_method','google')
    stages=[]
    class Browser:
        def __init__(self,*args): pass
        def open(self,pw): stages.append('verify')
        def identity(self): return 'tester'
        def disconnect(self): pass
    class Context:
        def __enter__(self): return None
        def __exit__(self,*args): pass
    def launch(*args):
        stages.append('interactive')
        def closed():
            stages.append('closed'); return 0
        return SimpleNamespace(poll=closed)
    monkeypatch.setattr('note_stock.worker.NoteBrowser',Browser)
    monkeypatch.setattr('note_stock.managed_browser.interactive_login',launch)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',Context)
    worker=BrowserWorker(store,'login',expected='tester')
    results=[]; worker.outcome.connect(lambda status,*args:results.append(status))
    worker.run()
    assert stages==['interactive','closed','verify']
    assert results==['connected'] and store.setting('account_verified')


def test_failed_google_identity_is_never_marked_connected(tmp_path,monkeypatch):
    from note_stock.worker import BrowserWorker
    store=Store(tmp_path); store.set_setting('note_login_method','google')
    class Browser:
        def __init__(self,*args): pass
        def open(self,pw): pass
        def identity(self): return 'different-account'
        def disconnect(self): pass
    class Context:
        def __enter__(self): return None
        def __exit__(self,*args): pass
    monkeypatch.setattr('note_stock.worker.NoteBrowser',Browser)
    monkeypatch.setattr('note_stock.managed_browser.interactive_login',lambda *a:SimpleNamespace(poll=lambda:0))
    monkeypatch.setattr('playwright.sync_api.sync_playwright',Context)
    worker=BrowserWorker(store,'login',expected='tester')
    results=[]; worker.outcome.connect(lambda status,*args:results.append(status))
    worker.run()
    assert results==['failed'] and not store.setting('account_verified',False)
