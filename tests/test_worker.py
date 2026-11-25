from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from note_stock.publisher import LoginRequired
from note_stock.store import Store
from note_stock.worker import BrowserWorker


@pytest.fixture
def headless_adapter(monkeypatch):
    calls = []

    @contextmanager
    def open_session(adapter, playwright):
        calls.append(adapter)
        adapter.background = False
        yield

    monkeypatch.setattr('note_stock.headless_sync.open_note_session', open_session)
    return calls


@pytest.mark.parametrize('submitted,login_error,expected', [(False,False,'failed'),(True,False,'unknown'),(False,True,'held')])
def test_worker_persists_failure_boundary_and_pauses(tmp_path, monkeypatch, headless_adapter, submitted, login_error, expected):
    store = Store(tmp_path)
    store.set_setting('paused', False)
    pid = store.create({'editor_kind':'legacy'})
    store.save(pid, '検証原稿', '本文', [], '', 'queued')
    post = store.claim(100, post_id=pid)
    class FakeBrowser:
        def __init__(self, *args):
            self.context = SimpleNamespace(close=lambda: None)
        def open(self, playwright):
            pytest.fail('Publishing must not open the visible browser')
        def publish(self, post):
            if submitted:
                store.checkpoint(pid, key='n123456789abc', submitted=True)
            raise LoginRequired('ログイン切れ') if login_error else RuntimeError('通信中断の模擬試験')
    monkeypatch.setattr('note_stock.worker.NoteBrowser', FakeBrowser)
    worker = BrowserWorker(store, 'publish', post)
    outputs = []
    worker.outcome.connect(lambda status, message, url: outputs.append(status))
    worker.run()
    assert outputs == [expected]
    assert len(headless_adapter) == 1
    assert store.get(pid)['status'] == expected
    assert store.setting('paused') is True


def test_two_instances_cannot_lock_same_store(tmp_path):
    from PySide6.QtCore import QLockFile
    first, second = QLockFile(str(tmp_path/'instance.lock')), QLockFile(str(tmp_path/'instance.lock'))
    assert first.tryLock(0)
    assert not second.tryLock(0)
    first.unlock()
    assert second.tryLock(0)
    second.unlock()


@pytest.mark.parametrize('scenario', ['changed-magazine','changed-price','external-publish','keeps-tags','rotated-token'])
def test_remote_publish_rechecks_conditions_and_preserves_requested_tags(tmp_path,monkeypatch,headless_adapter,scenario):
    import copy
    import json
    from note_stock.native import NativeNoteBrowser
    store=Store(tmp_path)
    store.set_setting('account','tester')
    store.set_setting('account_verified',True)
    row={'key':'n123456789abc','title':'検証','status':'draft','updated':'a'}
    pid=store.merge_catalog([row],True)[0]['id']
    payload={'name':'検証','status':'published','free_body':'本文','price':0,'magazine_ids':[1],'hashtags':['#希望タグ'],'line_add_friend_access_token':'previous-test-token'}
    snapshot={'document':{'kind':'html','value':'本文'},'fingerprint':'fixed','title':'検証','text':'本文'}
    template={'account':'tester','key':row['key'],'payload':payload,'url':'https://note.com/api/v1/text_notes/1','method':'PUT'}
    store.capture_native(pid,template,snapshot)
    sent=[]
    class FakeBrowser:
        def __init__(self,*args):
            self.store=store
            self.context=SimpleNamespace(close=lambda:None,new_page=lambda:None)
        def open(self,pw): pytest.fail('Publishing must not open the visible browser')
        def new_page(self):
            assert not self.background
            return self.context.new_page()
        def edit(self,post,automatic=False):
            updated=copy.deepcopy(template)
            if scenario=='changed-magazine': updated['payload']['magazine_ids']=[2]
            if scenario=='changed-price': updated['payload']['price']=300
            if scenario=='rotated-token': updated['payload']['line_add_friend_access_token']='fresh-test-token'
            # The frontend's defaults must never erase the requested publication tags.
            updated['payload']['hashtags']=[]
            store.capture_native(pid,updated,snapshot,post)
        def update_tags(self,pid,tags):
            NativeNoteBrowser.update_tags(self,pid,tags)
        def sync_articles(self):
            store.merge_catalog([{**row,'status':'published' if scenario=='external-publish' else 'draft'}],True)
        def publish(self,post):
            sent.append(post)
            return 'https://note.com/tester/n/'+row['key']
    monkeypatch.setattr('note_stock.worker.NoteBrowser',FakeBrowser)
    worker=BrowserWorker(store,'prepare_publish',store.get(pid))
    outputs=[]
    worker.outcome.connect(lambda status,message,url:outputs.append(status))
    worker.run()
    assert len(headless_adapter) == 1
    if scenario in {'keeps-tags','rotated-token'}:
        assert outputs==['published'] and len(sent)==1
        assert sent[0]['tags']==['希望タグ']
        assert store.native_template(pid)['payload']['hashtags']==['#希望タグ']
        if scenario=='rotated-token':
            assert store.native_template(pid)['payload']['line_add_friend_access_token']=='fresh-test-token'
    else:
        assert outputs==['failed'] and sent==[]
        assert store.setting('paused') is True
