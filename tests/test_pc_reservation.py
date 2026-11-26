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
def test_save_button_registers_time_without_reading_or_saving_note(screen,monkeypatch,already_loaded):
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
    assert store.get(pid)['status']=='scheduled'
    with store.connect() as c:
        row=c.execute("SELECT * FROM jobs WHERE state='waiting'").fetchone()
    assert row is None
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
    assert bool(saved['native_ready'])==already_loaded
    assert saved['body']==('remote content' if already_loaded else '')
    assert saved['status']=='scheduled' and saved['due']==planned
    assert store.next_job() is None
    adapter.save_draft.assert_not_called()
    adapter.publish.assert_not_called()
    adapter.open.assert_not_called()
    adapter.edit.assert_not_called()


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


def test_manual_reservation_completes_even_while_browser_is_busy(tmp_path):
    store=Store(tmp_path)
    pid=store.create({'editor_kind':'legacy'})
    job=store.enqueue('schedule',pid,{'due':now_ts()+60})
    worker=Mock(catalog_import=True)
    controller=SimpleNamespace(store=store,worker=worker,window=SimpleNamespace(busy=lambda:True,quitting=False))
    RemoteController.tick(controller)
    assert store.job(job)['state']=='done'
    assert store.get(pid)['status']=='scheduled'
    worker.cancel.set.assert_not_called()


def test_scheduled_to_queue_gui_save_does_not_resubmit_or_erase_tags(screen,monkeypatch):
    app,store,pid,window=screen
    store.capture_native(pid,{'account':'tester','payload':{'free_body':'remote content','hashtags':['#保持するタグ']}},
                         {'title':'existing note','text':'remote content','document':{},'fingerprint':'same'})
    store.save_fields(pid,{'status':'scheduled','due':now_ts()+600})
    window.load(pid)
    window.tags.text.return_value='#保持するタグ'
    store.set_tags(pid,['別の操作で保存したタグ'],expected_revision=window.current['tags_revision'])
    window.mode.currentData.return_value='queued'
    assert window.save_current()
    assert store.get(pid)['tags']==['別の操作で保存したタグ']
    assert store.get(pid)['status']=='queued'
    assert store.get(pid)['due'] is None
    # Reproduce the native draft-save recapture that used to replace the tags.
    class Adapter:
        def __init__(self,*args): self.context=SimpleNamespace(close=lambda:None)
        def open(self,*args): pass
        def save_draft(self,post):
            begun=store.begin_native_edit(pid)
            store.capture_native(pid,{'account':'tester','payload':{'free_body':'remote content','hashtags':[]}},
                                 {'title':'existing note','text':'remote content','document':{},'fingerprint':'same'},begun)
    monkeypatch.setattr('note_stock.worker.NoteBrowser',Adapter)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
    monkeypatch.setattr('note_stock.headless_sync.open_note_session',lambda *args:nullcontext())
    worker=BrowserWorker(store,'save_note_draft',store.get(pid))
    worker.items=['queue']
    outcomes=[]
    worker.outcome.connect(lambda status,*rest:outcomes.append(status))
    worker.run()
    assert outcomes==['saved']
    assert Store(store.root).get(pid)['tags']==['別の操作で保存したタグ']
