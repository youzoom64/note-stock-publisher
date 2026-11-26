from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from note_stock.store import Store
from note_stock.worker import BrowserWorker


@pytest.mark.parametrize('sync_error',[None,RuntimeError('同期に失敗しました。')])
@pytest.mark.parametrize('cleanup_error',[False,True])
def test_sync_closes_owned_note_browser_before_reporting_result(tmp_path,monkeypatch,sync_error,cleanup_error):
    store=Store(tmp_path)
    events=[]
    adapter=Mock()
    adapter.context=SimpleNamespace()
    adapter.sync_articles.side_effect=sync_error
    adapter.sync_articles.return_value={'catalog':{'items':[],'complete':True},'results':[]}
    adapter.disconnect.side_effect=lambda:events.append('disconnect')
    monkeypatch.setattr('note_stock.worker.NoteBrowser',lambda *args:adapter)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
    monkeypatch.setattr('note_stock.headless_sync.remember_session',Mock())
    def close(root):
        assert root==store.root
        events.append('close')
        if cleanup_error:raise RuntimeError('終了エラー')
        return {'closed':True}
    shutdown=Mock(side_effect=close)
    monkeypatch.setattr('note_stock.managed_browser.shutdown',shutdown)
    worker=BrowserWorker(store,'sync_articles')
    worker.headless_sync=False
    outcomes=[]
    worker.outcome.connect(lambda *result:(events.append('outcome'),outcomes.append(result)))
    worker.run()
    assert events==['disconnect','close','outcome']
    shutdown.assert_called_once_with(store.root)
    assert outcomes[0][0]==('failed' if sync_error else 'synced')
    assert ('専用ブラウザーを終了できませんでした' in outcomes[0][1])==cleanup_error


def test_sync_does_not_close_an_existing_browser_when_open_is_rejected(tmp_path,monkeypatch):
    adapter=Mock()
    adapter.open.side_effect=RuntimeError('ログイン中です。')
    monkeypatch.setattr('note_stock.worker.NoteBrowser',lambda *args:adapter)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
    shutdown=Mock()
    monkeypatch.setattr('note_stock.managed_browser.shutdown',shutdown)
    worker=BrowserWorker(Store(tmp_path),'sync_articles')
    worker.headless_sync=False
    worker.run()
    shutdown.assert_not_called()


@pytest.mark.parametrize('failure',[False,True])
def test_default_sync_is_headless_and_closes_session_before_result(tmp_path,monkeypatch,failure):
    from contextlib import contextmanager
    store=Store(tmp_path)
    adapter=Mock()
    adapter.sync_articles.return_value={'catalog':{'items':[],'complete':True},'results':[]}
    if failure:adapter.sync_articles.side_effect=RuntimeError('同期を中止しました。')
    events=[]
    @contextmanager
    def session(*args):
        events.append('headless_open')
        try:yield
        finally:events.append('headless_close')
    monkeypatch.setattr('note_stock.worker.NoteBrowser',lambda *args:adapter)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
    monkeypatch.setattr('note_stock.headless_sync.open_note_session',session)
    shutdown=Mock()
    monkeypatch.setattr('note_stock.managed_browser.shutdown',shutdown)
    worker=BrowserWorker(store,'sync_articles')
    assert worker.headless_sync
    outputs=[]
    worker.outcome.connect(lambda *result:(events.append('outcome'),outputs.append(result)))
    worker.run()
    assert events==['headless_open','headless_close','outcome']
    adapter.open.assert_not_called()
    shutdown.assert_not_called()
    assert outputs[0][0]==('failed' if failure else 'synced')
