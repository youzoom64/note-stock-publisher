from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from note_stock.managed_browser import background_page
from note_stock.headless_sync import open_note_session
from note_stock.native import NativeNoteBrowser
from note_stock.publisher import LoginRequired
from note_stock.store import Store
from note_stock.worker import BrowserWorker


def test_background_page_uses_visible_unselected_target_and_detaches_even_on_error():
    page=object();event=SimpleNamespace(value=page)
    class Wait:
        def __enter__(self):return event
        def __exit__(self,*args):pass
    session=Mock();browser=SimpleNamespace(new_browser_cdp_session=lambda:session)
    context=SimpleNamespace(expect_page=lambda:Wait(),new_page=Mock())
    assert background_page(browser,context) is page
    session.send.assert_called_once_with('Target.createTarget',{'url':'about:blank','background':True})
    context.new_page.assert_not_called();session.detach.assert_called_once()
    session.reset_mock();session.send.side_effect=RuntimeError('cannot create target')
    with pytest.raises(RuntimeError):background_page(browser,context)
    session.detach.assert_called_once()


@pytest.mark.parametrize('automatic',[False,True])
def test_only_automatic_browser_pages_avoid_activation(tmp_path,monkeypatch,automatic):
    adapter=NativeNoteBrowser(Store(tmp_path));adapter.background=automatic
    context=SimpleNamespace(pages=[],new_page=Mock(return_value='interactive'),set_default_timeout=Mock())
    connect=Mock(return_value=('browser',context));background=Mock(return_value='background')
    monkeypatch.setattr('note_stock.managed_browser.connect',connect)
    monkeypatch.setattr('note_stock.managed_browser.background_page',background)
    adapter.open(None)
    assert connect.call_args.kwargs['activate']==(not automatic)
    assert adapter.page==('background' if automatic else 'interactive')
    # Subsequent inspection/publish tabs must follow the same policy.
    assert adapter.new_page()==adapter.page
    if automatic:context.new_page.assert_not_called()
    else:background.assert_not_called()


def test_open_reuses_blank_without_overwriting_existing_editor(tmp_path,monkeypatch):
    editor=SimpleNamespace(url='https://editor.note.com/notes/n123456789abc/edit/',is_closed=lambda:False)
    blank=SimpleNamespace(url='about:blank',is_closed=lambda:False)
    context=SimpleNamespace(pages=[editor,blank],new_page=Mock(),set_default_timeout=Mock())
    monkeypatch.setattr('note_stock.managed_browser.connect',Mock(return_value=('browser',context)))
    adapter=NativeNoteBrowser(Store(tmp_path))
    adapter.open(None)
    assert adapter.page is blank
    context.new_page.assert_not_called()
    assert adapter.new_page() is blank
    context.new_page.assert_not_called()


def test_headless_operation_login_failure_does_not_replay_write(monkeypatch):
    state = {'cookies': [{'domain': 'note.com'}], 'origins': []}
    store = SimpleNamespace(root='profile', setting=lambda key, default='': {
        'account': 'tester', 'headless_note_session': 'protected',
    }.get(key, default))
    context = SimpleNamespace(new_page=Mock(return_value='page'), set_default_timeout=Mock())
    version_session=Mock()
    version_session.send.return_value={'userAgent':'Mozilla/5.0 HeadlessChrome/144.0.0.0 Safari/537.36'}
    browser = SimpleNamespace(new_context=Mock(return_value=nullcontext(context)),
                              new_browser_cdp_session=Mock(return_value=version_session))
    launch = Mock(return_value=nullcontext(browser))
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    adapter = SimpleNamespace(store=store, check_account=Mock())
    adapter.new_page = lambda: adapter.context.new_page()
    visible = Mock()
    monkeypatch.setattr('note_stock.headless_sync.unprotect', lambda _: {'account': 'tester', 'state': state})
    monkeypatch.setattr('note_stock.headless_sync.select_browser', lambda *_: ('chrome', 'browser'))
    monkeypatch.setattr('note_stock.headless_sync.remember_session', Mock())
    monkeypatch.setattr('note_stock.headless_sync.connect', visible)
    with pytest.raises(LoginRequired, match='送信時にログイン切れ'):
        with open_note_session(adapter, playwright):
            assert adapter.page == 'page'
            raise LoginRequired('送信時にログイン切れ')
    launch.assert_called_once_with(executable_path='browser', headless=True)
    browser.new_context.assert_called_once()
    assert browser.new_context.call_args.kwargs['user_agent']=='Mozilla/5.0 Chrome/144.0.0.0 Safari/537.36'
    version_session.detach.assert_called_once()
    adapter.check_account.assert_called_once()
    visible.assert_not_called()


def test_delete_uses_headless_session_without_opening_a_browser_window(tmp_path,monkeypatch):
    store=Store(tmp_path)
    pid=store.create({'title':'削除対象'})
    store.checkpoint(pid,key='n123456789abc')
    adapter=Mock()
    adapter.delete_article.side_effect=lambda post:store.delete(post['id'])
    session=Mock(return_value=nullcontext())
    monkeypatch.setattr('note_stock.worker.NoteBrowser',lambda *args:adapter)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
    monkeypatch.setattr('note_stock.headless_sync.open_note_session',session)
    results=[]
    worker=BrowserWorker(store,'delete_note',store.get(pid))
    worker.outcome.connect(lambda *result:results.append(result))
    worker.run()
    session.assert_called_once()
    adapter.open.assert_not_called()
    adapter.delete_article.assert_called_once()
    assert results[0][0]=='deleted'
