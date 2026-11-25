from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from note_stock.store import Store,now_ts
from note_stock.worker import BrowserWorker
from note_stock.remote import RemoteController


def catalog_draft(key,title,rev='1'):
    return {'key':key,'title':title,'status':'draft','owner':'tester','updated':rev}


def test_idle_dispatch_never_imports_unselected_articles(tmp_path,monkeypatch):
    store=Store(tmp_path)
    store.set_setting('account','tester')
    store.set_setting('account_verified',True)
    first,selected=[row['id'] for row in store.merge_catalog([
        catalog_draft('n123456789abc','first'),catalog_draft('n23456789abcd','selected')])]
    worker=Mock()
    create_worker=Mock(return_value=worker)
    monkeypatch.setattr('note_stock.remote.BrowserWorker',create_worker)
    window=SimpleNamespace(quitting=False,dirty=True,busy=lambda:False,current_id=selected,
                           refresh_controls=Mock(),load=Mock(),statusBar=Mock())
    controller=SimpleNamespace(window=window,store=store,current=None,last_sync=now_ts(),
                               catalog_import_enabled=True,next_catalog_import_at=0,
                               catalog_import_finished=Mock(),worker_done=Mock())
    controller.start_catalog_import=lambda stamp:RemoteController.start_catalog_import(controller,stamp)
    RemoteController.tick(controller)
    create_worker.assert_not_called()
    window.dirty=False
    RemoteController.tick(controller)
    create_worker.assert_not_called()
    assert store.next_job() is None


def test_catalog_queues_all_unread_articles_and_retries_after_next_sync(tmp_path):
    store=Store(tmp_path)
    store.set_setting('account','tester')
    first=catalog_draft('n123456789abc','first')
    second=catalog_draft('n23456789abcd','second')
    p1,p2=[row['id'] for row in store.merge_catalog([first,second],True)]
    assert store.next_catalog_import(p2)['id']==p2
    picked=store.next_catalog_import()
    assert picked['id']==p1
    with store.connect() as c:
        c.execute('UPDATE posts SET native_ready=1 WHERE id=?',(p1,))
    store.finish_catalog_import(picked)
    assert store.next_catalog_import() is None
    store.merge_catalog([dict(first,updated='2'),second],True)
    assert {store.next_catalog_import()['id'],store.next_catalog_import()['id']}=={p1,p2}
    assert store.next_catalog_import() is None


@pytest.mark.parametrize('changed_during_import',[True,False])
def test_catalog_capture_preserves_existing_schedule_and_local_changes(tmp_path,monkeypatch,changed_during_import):
    store=Store(tmp_path)
    store.set_setting('account','tester')
    key='n123456789abc'
    pid=store.merge_catalog([catalog_draft(key,'draft')])[0]['id']
    planned=now_ts()+3600
    with store.connect() as c:
        c.execute("UPDATE posts SET status='scheduled',due=?,tags='[\"local\"]' WHERE id=?",(planned,pid))
    item=store.next_catalog_import()
    adapter=Mock()
    def edit(post,automatic):
        assert automatic and post['_catalog_import']
        assert store.get(pid)['status']=='scheduled'
        if changed_during_import:
            with store.connect() as c:
                c.execute("UPDATE posts SET title='my edit',updated=updated+1 WHERE id=?",(pid,))
        store.capture_native(pid,{'account':'tester','payload':{'free_body':'<p>本文</p>','hashtags':['#remote']}},
                             {'title':'draft','text':'本文','document':{},'fingerprint':'test'},post)
    adapter.edit.side_effect=edit
    monkeypatch.setattr('note_stock.worker.NoteBrowser',lambda *args:adapter)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
    monkeypatch.setattr('note_stock.headless_sync.open_note_session',lambda *args:nullcontext())
    worker=BrowserWorker(store,'prepare_publish_check',item)
    worker.catalog_import=True
    results=[]
    worker.outcome.connect(lambda *result:results.append(result))
    worker.run()
    latest=store.get(pid)
    assert latest['status']=='scheduled' and latest['due']==planned and latest['tags']==['local']
    assert results[0][0]==('failed' if changed_during_import else 'saved'),results
    assert latest['title']==('my edit' if changed_during_import else 'draft')
    assert bool(latest['native_ready'])!=changed_during_import
    assert latest['body']==('' if changed_during_import else '本文')
    adapter.publish.assert_not_called()
    assert not store.pending_notifications()


@pytest.mark.parametrize('mode',['scheduled','queued','draft'])
@pytest.mark.parametrize('capture_fails',[False,True])
def test_pc_posting_settings_capture_catalog_body_before_reserving(tmp_path,monkeypatch,mode,capture_fails):
    store=Store(tmp_path)
    store.set_setting('account','tester')
    pid=store.merge_catalog([catalog_draft('n123456789abc','draft')])[0]['id']
    with store.connect() as c:
        c.execute("UPDATE posts SET tags='[\"local\"]' WHERE id=?",(pid,))
    planned=now_ts()+3600
    adapter=Mock()
    def edit(post,automatic):
        assert automatic and post['_catalog_import']
        if capture_fails:
            raise ValueError('公開条件を取得できませんでした。')
        store.capture_native(pid,{'account':'tester','payload':{'free_body':'<p>existing note body</p>','hashtags':['#remote']}},
                             {'title':'draft','text':'existing note body','document':{},'fingerprint':'test'},post)
    adapter.edit.side_effect=edit
    monkeypatch.setattr('note_stock.worker.NoteBrowser',lambda *args:adapter)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
    monkeypatch.setattr('note_stock.headless_sync.open_note_session',lambda *args:nullcontext())
    worker=BrowserWorker(store,'save_note_draft',store.get(pid))
    worker.import_due=planned if mode=='scheduled' else None
    worker.items=['queue'] if mode=='queued' else []
    results=[]
    worker.outcome.connect(lambda *result:results.append(result))
    worker.run()
    latest=store.get(pid)
    assert results[0][0]==('failed' if capture_fails else 'saved'),results
    assert latest['status']==('failed' if capture_fails else mode)
    assert latest['due']==(planned if mode=='scheduled' and not capture_fails else None)
    assert latest['body']==('' if capture_fails else 'existing note body')
    assert latest['tags']==['local']
    assert bool(latest['native_ready'])!=capture_fails
    adapter.save_draft.assert_not_called()
    adapter.publish.assert_not_called()
    adapter.open.assert_called_once()
    assert not adapter.background
