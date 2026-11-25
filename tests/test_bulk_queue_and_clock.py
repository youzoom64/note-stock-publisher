from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QTimer, QTime, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from note_stock.clock_picker import PostingTimeDialog
from note_stock.remote import submit
from note_stock.store import Store
from note_stock.ui import MainWindow, SettingsDialog
from note_stock.worker import BrowserWorker


@pytest.fixture
def screen(tmp_path, qt_application):
    store = Store(tmp_path)
    store.set_setting("account", "tester")
    store.set_setting("account_verified", True)
    ids = [store.create({"title": f"下書き {i}", "body": "本文"}) for i in range(3)]
    published = store.create({"title": "公開済み"})
    store.finish(published, "published", url="https://note.com/tester/n/n123456789abc")
    window = MainWindow(store, offline=True)
    window.show()
    qt_application.processEvents()
    yield store, window, ids, published
    window.quitting = True
    window.close()


def test_checkbox_click_all_clear_refresh_and_batch_enqueue(screen, qt_application):
    store, window, ids, published = screen
    item = next(window.list.item(i) for i in range(window.list.count())
                if window.list.item(i).data(Qt.ItemDataRole.UserRole) == ids[0])
    option = QStyleOptionViewItem()
    window.list.initViewItemOption(option)
    option.rect = window.list.visualItemRect(item)
    window.list.itemDelegate().initStyleOption(option, window.list.indexFromItem(item))
    rect = window.list.style().subElementRect(
        QStyle.SubElement.SE_ItemViewItemCheckIndicator, option, window.list
    )
    QTest.mouseClick(window.list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert ids[0] in window.checked_ids
    window.refresh_list()
    assert ids[0] in window.checked_ids
    window.check_all_button.click()
    assert window.checked_ids == set(ids)
    window.clear_checks_button.click()
    assert not window.checked_ids and not window.queue_checked_button.isEnabled()
    window.check_all_button.click()
    window.queue_checked_button.click()
    assert not window.checked_ids
    jobs = store.operation_history()["items"]
    assert {job["post_id"] for job in jobs} == set(ids)
    assert all(job["data"]["queue"] and job["data"]["draft_queue"] for job in jobs)
    assert any("キュー追加中" in window.list.item(i).text() for i in range(window.list.count()))
    window.check_all_button.click()
    window.queue_checked_button.click()
    assert store.operation_history()["total"] == 3
    assert store.get(published)["status"] == "published"


def test_bulk_failure_keeps_failed_checkbox_and_attempts_remaining_items(screen, monkeypatch):
    store, window, ids, _ = screen
    import note_stock.remote as remote
    original = remote.submit
    def save(store, action, pid, data):
        if pid == ids[1]:
            raise ValueError("この1件だけ失敗")
        return original(store, action, pid, data)
    monkeypatch.setattr(remote, "submit", save)
    window.check_all_drafts()
    window.queue_checked_articles()
    assert window.checked_ids == {ids[1]}
    assert store.operation_history()["total"] == 2
    assert "この1件だけ失敗" in window.statusBar().currentMessage()


def test_bulk_queue_rechecks_changed_article_before_browser_work(screen, monkeypatch):
    store, window, ids, _ = screen
    submit(store, "save_note_draft", ids[0], {"queue": True, "draft_queue": True})
    store.finish(ids[0], "published", url="https://note.com/tester/n/n123456789abc")
    worker = Mock()
    monkeypatch.setattr("note_stock.remote.BrowserWorker", worker)
    window.remote_controller.tick()
    worker.assert_not_called()
    job = store.operation_history()["items"][0]
    assert job["state"] == "failed"
    assert "下書きの状態が変わりました" in job["result"]["message"]


def test_queue_worker_finishes_in_queue_with_existing_save_path(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.set_setting("account", "tester")
    cover = tmp_path / "cover.png"
    image = QImage(1, 1, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    assert image.save(str(cover))
    stored_cover = store.import_image(cover)
    pid = store.create({"editor_kind": "legacy", "title": "タイトル", "body": "本文", "image": stored_cover})
    class Browser:
        def __init__(self, *args):
            self.background = False
            self.context = Mock()
        def save_draft(self, post):
            assert post["body"] == "本文"
    @contextmanager
    def session(adapter, playwright):
        yield
    monkeypatch.setattr("note_stock.worker.NoteBrowser", Browser)
    monkeypatch.setattr("note_stock.headless_sync.open_note_session", session)
    worker = BrowserWorker(store, "save_note_draft", store.get(pid))
    worker.items = ["queue"]
    worker.run()
    assert store.get(pid)["status"] == "queued"
    assert store.get(pid)["image"] == stored_cover


@pytest.mark.parametrize("accepted", [True, False])
def test_settings_time_click_opens_clock_and_saves_only_accepted_time(tmp_path, qt_application, accepted):
    store = Store(tmp_path)
    settings = SettingsDialog(store)
    settings.show()
    qt_application.processEvents()
    previous = settings.time.time()
    opened = []
    def choose():
        dialog = QApplication.activeModalWidget()
        opened.append(isinstance(dialog, PostingTimeDialog))
        if isinstance(dialog, PostingTimeDialog):
            dialog.time_edit.setTime(QTime(14, 35))
            dialog.accept() if accepted else dialog.reject()
    QTimer.singleShot(50, choose)
    try:
        QTest.mouseClick(settings.time.lineEdit(), Qt.MouseButton.LeftButton)
        QTest.qWait(80)
        assert opened == [True]
        expected = "14:35" if accepted else previous.toString("HH:mm")
        assert settings.time.time().toString("HH:mm") == expected
        assert store.setting("recurrence", {"time": "09:00"})["time"] == expected
    finally:
        settings.close()
