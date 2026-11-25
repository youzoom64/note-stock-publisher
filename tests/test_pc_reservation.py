from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from note_stock.store import Store,now_ts
from note_stock.ui import MainWindow,jst_qdate
from note_stock.worker import BrowserWorker
from note_stock.remote import RemoteController,submit


@pytest.fixture
def screen(tmp_path):
    app=SimpleNamespace(processEvents=Mock())
    store=Store(tmp_path)
    store.set_setting('account','tester')
    store.set_setting('account_verified',True)
    pid=store.merge_catalog([{'key':'n123456789abc','title':'existing note','status':'draft','owner':'tester','updated':'1'}])[0]['id']
    window=SimpleNamespace(store=store,current_id=pid,current=store.get(pid),image='',dirty=True,
                           mode=Mock(),when=Mock(),tags=Mock(),save_state=Mock(),refresh=Mock(),
                           statusBar=Mock(),fail=Mock(),quitting=False,worker=None,
                           busy=lambda:False,note_progress=Mock(),note_action_required=Mock())
    window.mode.currentData.return_value='scheduled'
    window.tags.text.return_value=''
    window.when.setDateTime.side_effect=lambda value: setattr(window.when,'selected',value)
    window.when.dateTime.side_effect=lambda:window.when.selected
    window.load=lambda value:setattr(window,'current',store.get(value))
    window.save_current=lambda:MainWindow.save_current(window)
    controller=SimpleNamespace(window=window,store=store,current=None,last_sync=now_ts(),worker_done=Mock())
    def finish(status,message,url):
        store.complete_job(controller.current['id'],status=='saved',message,url,notify=False)
        controller.current=None
    controller.finish=finish
    controller.tick=lambda:RemoteController.tick(controller)
    window.remote_controller=controller
    yield app,store,pid,window


@pytest.mark.parametrize('already_loaded',[False,True])
def test_save_button_automatically_reads_and_reserves_without_draft_save(screen,monkeypatch,already_loaded):
    app,store,pid,window=screen
    snapshot={'title':'existing note','text':'remote content','document':{},'fingerprint':'same'}
    template={'account':'tester','payload':{'free_body':'<p>remote content</p>'}}
    if already_loaded:
        store.capture_native(pid,template,snapshot)
        window.load(pid)
    planned=(int(now_ts())//60+2)*60
    window.mode.setCurrentIndex(window.mode.findData('scheduled'))
    window.when.setDateTime(jst_qdate(planned))
    assert window.save_current()
    assert store.get(pid)['status']=='draft'
    with store.connect() as c:
        row=c.execute("SELECT * FROM jobs WHERE state='waiting'").fetchone()
    assert row['action']=='schedule'
    adapter=Mock()
    adapter.edit.side_effect=lambda post,automatic:store.capture_native(pid,template,snapshot,post)
    monkeypatch.setattr('note_stock.worker.NoteBrowser',lambda *args:adapter)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
    monkeypatch.setattr('note_stock.headless_sync.open_note_session',lambda *args:nullcontext())
    def worker(*args,**kwargs):
        kwargs.pop('parent',None)
        result=BrowserWorker(*args,**kwargs)
        result.start=lambda:result.run()
        return result
    monkeypatch.setattr('note_stock.remote.BrowserWorker',worker)
    window.remote_controller.tick()
    app.processEvents()
    saved=store.get(pid)
    assert saved['native_ready'] and saved['body']=='remote content'
    assert saved['status']=='scheduled' and saved['due']==planned
    assert store.job(row['id'])['state']=='done'
    adapter.save_draft.assert_not_called()
    adapter.publish.assert_not_called()
    assert adapter.open.call_count==(0 if already_loaded else 1)
    if not already_loaded:
        assert not adapter.background
    assert adapter.edit.call_count==(0 if already_loaded else 1)


def test_past_time_rejected_before_saving_or_queueing(screen):
    app,store,pid,window=screen
    before=store.get(pid)
    window.mode.setCurrentIndex(window.mode.findData('scheduled'))
    window.when.setDateTime(jst_qdate(now_ts()-60))
    assert not window.save_current()
    assert store.get(pid)==before
    assert store.next_job() is None


@pytest.mark.parametrize('action',['schedule','save_note_draft'])
def test_api_rejects_past_time_for_unread_article(tmp_path,action):
    store=Store(tmp_path)
    store.set_setting('account','tester')
    store.set_setting('account_verified',True)
    pid=store.merge_catalog([{'key':'n123456789abc','title':'draft','status':'draft','owner':'tester'}])[0]['id']
    with pytest.raises(ValueError,match='未来'):
        submit(store,action,pid,{'due':now_ts()-1})
    assert store.next_job() is None


def test_manual_reservation_preempts_catalog_import(tmp_path):
    store=Store(tmp_path)
    store.enqueue('schedule','test',{'due':now_ts()+60})
    worker=Mock(catalog_import=True)
    controller=SimpleNamespace(store=store,worker=worker,window=SimpleNamespace(busy=lambda:True,quitting=False))
    RemoteController.tick(controller)
    worker.cancel.set.assert_called_once()
