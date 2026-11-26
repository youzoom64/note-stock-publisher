import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from note_stock.qt import Qt
from note_stock.qt import QTest

from note_stock.store import Store,now_ts,tags_from_text
from note_stock.ui import MainWindow,jst_qdate


@pytest.mark.parametrize('prepared',[False,True],ids=['body-unread','body-loaded'])
def test_gui_enter_tags_then_queue_schedule_queue_preserves_tags(tmp_path,qt_application,monkeypatch,prepared):
    store=Store(tmp_path)
    store.set_setting('account','tester')
    store.set_setting('account_verified',True)
    pid=store.merge_catalog([{'key':'n123456789abc','title':'ハッシュタグ往復テスト',
                             'status':'draft','owner':'tester'}])[0]['id']
    if prepared:
        store.capture_native(pid,{'account':'tester','payload':{'hashtags':['#before']}},
                             {'title':'ハッシュタグ往復テスト','text':'テスト本文','document':{},'fingerprint':'test'})
    browser=Mock(side_effect=AssertionError('A state change must not open the browser'))
    monkeypatch.setattr('note_stock.remote.BrowserWorker',browser)
    expected=['keep_tag','queue_schedule']
    output=Path(__file__).resolve().parents[1]/'artifacts'
    label='loaded' if prepared else 'unread'
    records=[]
    window=MainWindow(store,offline=True)
    window.timer.stop()
    window.remote_controller.timer.stop()
    window.fail=Mock()

    def check(stage,status,revision=None):
        qt_application.processEvents()
        saved=store.get(pid)
        with store.connect() as c:
            raw=c.execute('SELECT value,revision FROM article_hashtags WHERE post_id=?',(pid,)).fetchone()
        assert tags_from_text(window.tags.text())==expected,(stage,window.tags.text())
        assert saved['tags']==json.loads(raw['value'])==expected,stage
        assert saved['status']==status and window.mode.currentData()==status,stage
        assert bool(saved['native_ready'])==prepared
        if revision is not None:
            assert raw['revision']==revision,stage
        window.fail.assert_not_called()
        browser.assert_not_called()
        assert store.operation_history()['total']==0
        if prepared:
            assert store.native_template(pid)['payload']['hashtags']==['#'+tag for tag in expected]
        records.append({'stage':stage,'status':saved['status'],'screen':window.tags.text(),
                        'database_tags':json.loads(raw['value']),'tags_revision':raw['revision']})
        assert window.grab().save(str(output/f'gui-tags-{label}-{stage}.png'))
        return raw['revision']

    def select_mode(status):
        window.mode.hidePopup()
        window.mode.setFocus()
        QTest.keyClick(window.mode,Qt.Key.Key_Home)
        for _ in range(window.mode.findData(status)):
            QTest.keyClick(window.mode,Qt.Key.Key_Down)
        assert window.mode.currentData()==status

    try:
        window.show()
        window.raise_()
        window.activateWindow()
        window.load(pid)
        qt_application.processEvents()
        QTest.mouseClick(window.tags,Qt.MouseButton.LeftButton)
        QTest.keyClick(window.tags,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(window.tags,'#keep_tag #queue_schedule')
        QTest.mouseClick(window.save_button,Qt.MouseButton.LeftButton)
        revision=check('tags-saved','draft')
        for stage,status in [('initial-queue','queued'),('queue-to-scheduled','scheduled'),('scheduled-to-queue','queued')]:
            select_mode(status)
            if status=='scheduled':
                window.when.setDateTime(jst_qdate(now_ts()+86400))
            QTest.mouseClick(window.save_button,Qt.MouseButton.LeftButton)
            check(stage,status,revision)
            # Reopen the row and DB after each transition, not just the in-memory form.
            window.load(None)
            window.load(pid)
            assert Store(tmp_path).get(pid)['tags']==expected
            check(stage+'-reopened',status,revision)
        (output/f'gui-tags-{label}-result.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(records,ensure_ascii=False),flush=True)
    finally:
        window.quitting=True
        window.close()
