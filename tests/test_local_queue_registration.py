from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from note_stock.qt import Qt

from note_stock.remote import submit
from note_stock.store import Store,now_ts
from note_stock.ui import MainWindow,jst_qdate
from note_stock.worker import BrowserWorker


def drafts(store,count=4):
    store.set_setting('account','tester')
    return [store.merge_catalog([{'key':f'n{index:012d}','title':f'記事{index}',
             'status':'draft','owner':'tester'}])[0]['id'] for index in range(count)]


def forbid_browser(monkeypatch):
    browser=Mock(side_effect=AssertionError('Registration must not open a browser or retrieve article content'))
    monkeypatch.setattr('note_stock.remote.BrowserWorker',browser)
    monkeypatch.setattr('note_stock.worker.NoteBrowser',browser)
    return browser


def test_bulk_queue_completes_immediately_in_display_order_without_body_read(tmp_path,qt_application,monkeypatch):
    store=Store(tmp_path)
    ids=drafts(store)
    browser=forbid_browser(monkeypatch)
    window=MainWindow(store,offline=True)
    try:
        window.show()
        window.filter_buttons['draft'].click()
        window.order_buttons['oldest'].click()
        displayed=[window.list.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(window.list.count())]
        window.check_all_drafts()
        window.queue_checked_articles()
        queued=store.posts()
        assert [p['id'] for p in queued]==displayed
        assert all(p['status']=='queued' and not p['native_ready'] and p['body']=='' for p in queued)
        assert store.next_job() is None
        assert all(j['state']=='done' for j in store.operation_history()['items'])
        assert len(store.pending_notifications())==1
        browser.assert_not_called()
    finally:
        window.quitting=True
        window.close()


def test_queue_registration_keeps_tags_and_payload_and_works_without_login(tmp_path,monkeypatch):
    store=Store(tmp_path)
    pid=drafts(store,1)[0]
    store.set_tags(pid,['保持'],expected_revision=store.get(pid)['tags_revision'])
    before=store.get(pid)
    browser=forbid_browser(monkeypatch)
    jid=submit(store,'save_note_draft',pid,{'queue':True,'draft_queue':True},key='same')
    assert store.job(jid)['state']=='done'
    after=store.get(pid)
    assert after['tags']==before['tags'] and after['tags_revision']==before['tags_revision']
    assert after['body']==before['body'] and after['native_ready']==0
    assert submit(store,'save_note_draft',pid,{'queue':True},key='same')==jid
    browser.assert_not_called()


def test_schedule_and_tags_are_local_and_do_not_prepare_article(tmp_path,monkeypatch):
    store=Store(tmp_path)
    pid=drafts(store,1)[0]
    browser=forbid_browser(monkeypatch)
    due=now_ts()+600
    jid=submit(store,'schedule',pid,{'due':due})
    assert store.job(jid)['state']=='done'
    assert store.get(pid)['status']=='scheduled' and store.get(pid)['due']==due
    jid=submit(store,'tags',pid,{'tags':'#新タグ'})
    assert store.job(jid)['state']=='done'
    assert store.get(pid)['tags']==['新タグ'] and not store.get(pid)['native_ready']
    browser.assert_not_called()


def test_old_queue_worker_cannot_use_draft_save_or_capture(tmp_path,monkeypatch):
    store=Store(tmp_path)
    pid=drafts(store,1)[0]
    browser=forbid_browser(monkeypatch)
    worker=BrowserWorker(store,'save_note_draft',store.get(pid))
    worker.items=['queue']
    worker.append_to_queue=True
    results=[]
    worker.outcome.connect(lambda status,*args:results.append(status))
    worker.run()
    assert results==['saved'] and store.get(pid)['status']=='queued'
    assert not store.get(pid)['native_ready']
    browser.assert_not_called()


@pytest.mark.parametrize('status',['queued','scheduled','draft'])
def test_gui_state_save_does_not_submit_body_work(tmp_path,monkeypatch,status):
    store=Store(tmp_path)
    pid=drafts(store,1)[0]
    browser=forbid_browser(monkeypatch)
    window=SimpleNamespace(store=store,current_id=pid,current=store.get(pid),image='',dirty=True,
                           mode=Mock(),when=Mock(),tags=Mock(),save_state=Mock(),refresh=Mock(),
                           load=Mock(),statusBar=Mock(),fail=Mock(),notify=Mock())
    window.mode.currentData.return_value=status
    window.tags.text.return_value=''
    window.when.dateTime.return_value=jst_qdate(now_ts()+600)
    assert MainWindow.save_current(window)
    assert store.get(pid)['status']==status
    assert not store.get(pid)['native_ready']
    assert store.operation_history()['total']==0
    browser.assert_not_called()
