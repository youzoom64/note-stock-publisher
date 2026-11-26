from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt

from note_stock.store import Store, JST
from note_stock.ui import MainWindow


def test_queue_drag_preserves_fixed_time_and_only_queue_rows_are_draggable(tmp_path,qt_application,monkeypatch):
    stamp=datetime(2026,12,1,8,0,tzinfo=JST).timestamp()
    monkeypatch.setattr("note_stock.store.now_ts",lambda:stamp)
    store=Store(tmp_path)
    store.set_setting("account","tester")
    store.set_setting("recurrence",{"days":list(range(7)),"time":"09:00"})
    def article(title,status,due=None):
        pid=store.create({"editor_kind":"legacy","title":title,"body":"本文"})
        store.save(pid,title,"本文",[],"",status,due)
        return pid
    first=article("キューA","queued")
    fixed_due=datetime(2026,12,1,12,0,tzinfo=JST).timestamp()
    fixed=article("日時指定B","scheduled",fixed_due)
    last=article("キューC","queued")
    window=MainWindow(store,offline=True)
    try:
        window.show()
        assert not window.order_buttons['planned'].isEnabled()
        window.filter_buttons["queued"].click()
        qt_application.processEvents()
        assert [window.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(3)]==[first,fixed,last]
        assert not window.list.item(1).flags() & Qt.ItemFlag.ItemIsDragEnabled
        assert window.list.item(1).background().color().name()=="#dcc3aa"
        assert window.count_labels["queued"].text()=="キュー 2"
        assert window.count_labels["scheduled"].text()=="日時指定 1"
        window.list.dragged_id=last
        point=window.list.visualItemRect(window.list.item(0)).topLeft()+QPoint(10,2)
        event=SimpleNamespace(source=lambda:window.list,position=lambda:QPointF(point),
                              acceptProposedAction=Mock(),ignore=Mock())
        window.list.dropEvent(event)
        event.acceptProposedAction.assert_called_once()
        assert [window.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(3)]==[last,fixed,first]
        assert store.get(fixed)["due"]==fixed_due
        plan=store.queue_schedule(stamp)
        assert datetime.fromtimestamp(plan[last],JST).strftime("%Y/%m/%d %H:%M")=="2026/12/01 09:00"
        assert datetime.fromtimestamp(plan[first],JST).strftime("%Y/%m/%d %H:%M")=="2026/12/02 09:00"
        window.list.dragged_id=fixed
        event.acceptProposedAction.reset_mock()
        window.list.dropEvent(event)
        event.ignore.assert_called_once()
        assert store.get(fixed)["due"]==fixed_due
        before={pid:store.get(pid) for pid in (first,fixed,last)}
        with pytest.raises(ValueError,match="キューの状態"):
            store.set_queue_order([first,fixed],stamp)
        assert {pid:store.get(pid) for pid in before}==before
        window.order_buttons['oldest'].click()
        window.order_buttons['planned'].click()
        for key in window.filter_buttons:
            if key=='queued':
                continue
            window.filter_buttons[key].click()
            assert not window.order_buttons['planned'].isEnabled()
            assert window.order_buttons['oldest'].isChecked()
            window.order_buttons['planned'].setChecked(True)
            assert not window.order_buttons['planned'].isChecked()
            window.filter_buttons['queued'].click()
            assert window.order_buttons['planned'].isEnabled()
            assert window.order_buttons['planned'].isChecked()
    finally:
        window.quitting=True
        window.close()
