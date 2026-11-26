from pathlib import Path
from unittest.mock import Mock

import pytest
from note_stock.qt import Qt
from note_stock.qt import QTest
from note_stock.qt import QPushButton

from note_stock.store import Store,now_ts
from note_stock.ui import MainWindow,STYLE


def test_each_review_row_has_working_restore_button_and_keeps_article_data(tmp_path,qt_application,monkeypatch):
    store=Store(tmp_path)
    states=['failed','held','unknown','overdue','review']
    ids=[]
    originals={}
    for index,state in enumerate(states):
        pid=store.create({'title':state+' テスト記事','body':'保持する本文','tags':['保持するタグ']})
        store.capture_native(pid,{'account':'tester','payload':{'hashtags':[],'free_body':'保持する本文'}},
                             {'title':state+' テスト記事','text':'保持する本文','document':{},'fingerprint':'same'})
        with store.connect() as c:
            c.execute('UPDATE posts SET status=?,error=?,due=?,submitted=? WHERE id=?',
                      (state,'以前のエラー',now_ts()+3600,int(state=='unknown'),pid))
        ids.append(pid)
        originals[pid]=store.get(pid)
    browser=Mock(side_effect=AssertionError('Restore must not access note'))
    monkeypatch.setattr('note_stock.remote.BrowserWorker',browser)
    previous_style=qt_application.styleSheet()
    qt_application.setStyleSheet(STYLE)
    window=MainWindow(store,offline=True)
    window.timer.stop()
    window.remote_controller.timer.stop()
    window.fail=Mock()
    try:
        window.show()
        window.filter_buttons['review'].click()
        qt_application.processEvents()
        assert window.list.count()==len(ids)
        assert window.grab().save(str(Path(__file__).resolve().parents[1]/'artifacts/restore-draft-buttons.png'))
        for index,pid in enumerate(ids):
            control=window.list.findChild(QPushButton,'restore_draft_'+pid)
            assert control is not None and control.text()=='下書きに戻す'
            QTest.mouseClick(control,Qt.MouseButton.LeftButton)
            qt_application.processEvents()
            current=store.get(pid)
            assert current['status']=='draft' and current['due'] is None and current['error']==''
            for field in ('title','body','tags','tags_revision','native_ready','native_document','native_fingerprint','submitted','draft_key','url'):
                assert current[field]==originals[pid][field],field
            assert store.history(pid)[0]['status']=='draft'
            assert states[index] in store.history(pid)[0]['message']
            assert window.list.count()==len(ids)-index-1
            window.fail.assert_not_called()
        browser.assert_not_called()
        assert store.operation_history()['total']==0
        assert all(p['status']=='draft' for p in Store(tmp_path).posts())
    finally:
        window.quitting=True
        window.close()
        qt_application.setStyleSheet(previous_style)


def test_restore_rechecks_current_state_and_preserves_unrelated_articles(tmp_path):
    store=Store(tmp_path)
    pid=store.create({'editor_kind':'legacy','title':'変更しない','body':'本文','tags':['タグ']})
    for state in ['draft','queued','posting','published']:
        with store.connect() as c:
            c.execute('UPDATE posts SET status=? WHERE id=?',(state,pid))
        before=store.get(pid)
        with pytest.raises(ValueError,match='要確認'):
            store.restore_draft(pid)
        assert store.get(pid)==before
