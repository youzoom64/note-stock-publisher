from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from note_stock.store import Store
from note_stock.ui import MainWindow,STYLE


@pytest.mark.parametrize('ok',[True,False])
def test_sync_button_shows_queued_running_and_persistent_outcome(tmp_path,qt_application,ok):
    store=Store(tmp_path)
    store.set_setting('account_verified',True)
    store.set_setting('cursor_help_enabled',False)
    window=MainWindow(store,offline=True)
    window.timer.stop()
    window.remote_controller.timer.stop()
    window.setStyleSheet(STYLE)
    window.show()
    qt_application.processEvents()
    try:
        assert window.import_button.isEnabled()
        assert not window.sync_progress.isVisible()
        QTest.mouseClick(window.import_button,Qt.MouseButton.LeftButton)
        assert window.import_button.text()=='同期開始待ち…'
        assert not window.import_button.isEnabled()
        assert window.sync_progress.isVisible()
        assert window.sync_status.isVisible()
        window.import_drafts()
        with store.connect() as c:
            assert c.execute("SELECT COUNT(*) FROM jobs WHERE action='sync_articles'").fetchone()[0]==1
        job=store.next_job()
        window.worker=SimpleNamespace(action='sync_articles',headless_sync=True,isRunning=lambda:True)
        window.refresh_controls()
        assert window.import_button.text()=='同期中…'
        assert '記事一覧を取得' in window.sync_status.text()
        window.note_progress('記事一覧を取得中：15件')
        window.refresh()
        assert window.sync_status.text()=='記事一覧を取得中：15件'
        assert not window.import_button.isEnabled()
        message='記事を同期しました。全件取得済み。' if ok else 'ログイン状態を確認してください。'
        store.complete_job(job['id'],ok,message,notify=False)
        window.worker=None
        window.refresh()
        assert window.import_button.text()=='noteの全記事を同期'
        assert window.import_button.isEnabled()
        assert not window.sync_progress.isVisible()
        assert window.sync_status.text()==('同期完了：' if ok else '同期に失敗しました：')+message
        window.notify('別の操作を保存しました。')
        window.refresh()
        assert message in window.sync_status.text()
        QTest.mouseClick(window.import_button,Qt.MouseButton.LeftButton)
        assert window.import_button.text()=='同期開始待ち…'
        assert message not in window.sync_status.text()
        assert not window.sync_progress_message
    finally:
        window.worker=None
        window.quitting=True
        window.close()
