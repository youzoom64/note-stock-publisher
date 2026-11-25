"""Run authenticated note operations without opening a desktop window."""
from contextlib import contextmanager
from urllib.parse import urlsplit

from .managed_browser import BrowserNotRunning, connect, select_browser
from .publisher import LoginRequired
from .secrets import protect, unprotect


def note_storage(context):
    state = context.storage_state()
    def note_host(host):
        host = (host or '').lstrip('.').casefold()
        return host == 'note.com' or host.endswith('.note.com')
    return {
        'cookies': [cookie for cookie in state['cookies'] if note_host(cookie['domain'])],
        'origins': [origin for origin in state['origins'] if note_host(urlsplit(origin['origin']).hostname)],
    }


def remember_session(store, context):
    account = store.setting('account', '')
    state = note_storage(context)
    if account and state['cookies']:
        store.set_setting('headless_note_session', protect({'account': account, 'state': state}))


@contextmanager
def open_note_session(adapter, playwright):
    store = adapter.store
    preference = store.setting('browser', 'auto')
    account = store.setting('account', '')
    encrypted = store.setting('headless_note_session','')
    try:
        saved = unprotect(encrypted) if encrypted else {}
    except (OSError, ValueError):
        saved = {}
    cached_state = saved.get('state') if saved.get('account') == account else None

    def states():
        if cached_state and cached_state['cookies']:
            yield cached_state
        # Migrate an existing login or recover an expired copy passively.
        # This connection never launches or navigates a desktop browser.
        try:
            _, visible = connect(playwright, store.root, preference, activate=False, allow_launch=False)
        except BrowserNotRunning:
            return
        current = note_storage(visible)
        if current['cookies'] and current != cached_state:
            yield current

    _, executable = select_browser(playwright, preference)
    login_error = None
    with playwright.chromium.launch(executable_path=executable, headless=True) as browser:
        version_session=browser.new_browser_cdp_session()
        try:
            user_agent=version_session.send('Browser.getVersion')['userAgent'].replace('HeadlessChrome/','Chrome/')
        finally:
            version_session.detach()
        for state in states():
            with browser.new_context(storage_state=state, locale='ja-JP', timezone_id='Asia/Tokyo',user_agent=user_agent) as context:
                context.set_default_timeout(20000)
                adapter.browser = browser
                adapter.context = context
                adapter.background = False
                adapter.page = adapter.new_page()
                try:
                    adapter.check_account()
                except LoginRequired as exc:
                    login_error = exc
                else:
                    remember_session(store, context)
                    # Session fallback ends before the operation starts. In particular,
                    # a publish error must never cause the operation to be replayed.
                    yield
                    return
    raise login_error or LoginRequired('バックグラウンド処理用のログイン情報がありません。設定からnoteへの接続を確認してください。')


def sync_catalog(adapter, playwright):
    with open_note_session(adapter, playwright):
        return adapter.sync_articles()
