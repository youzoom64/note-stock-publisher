import pytest

from note_stock.commands import Commands
from note_stock.store import Store


def test_cli_shared_drafts_tags_and_pausing(tmp_path):
    store=Store(tmp_path)
    commands=Commands(store)
    created=commands.handle({'command':'create','data':{'title':'CLI原稿','body':'本文\n改行','tags':'#日記,#旅行','status':'draft'}})
    assert created['ok']
    pid=created['result']['id']
    assert commands.handle({'command':'get','id':pid})['result']['tags']==['日記','旅行']
    assert commands.handle({'command':'update','id':pid,'data':{'body':'更新した本文'}})['ok']
    assert store.get(pid)['body']=='更新した本文'
    assert not commands.handle({'command':'resume'})['ok']
    store.set_setting('account_verified',True)
    assert commands.handle({'command':'resume'})['ok']
    assert store.setting('paused') is False
    assert commands.handle({'command':'pause'})['ok']
    assert commands.handle({'command':'duplicate','id':pid})['result']['status']=='draft'
    assert len(commands.handle({'command':'export'})['result'])==2
    assert commands.handle({'command':'delete','id':pid})['ok']
    assert len(store.posts())==1


def test_cli_uses_same_post_lock_and_validates_settings(tmp_path):
    store=Store(tmp_path)
    commands=Commands(store)
    pid=store.create({'editor_kind':'legacy'})
    store.save(pid,'本文のある原稿','本文',[],'','queued')
    store.claim(100,post_id=pid)
    assert not commands.handle({'command':'update','id':pid,'data':{'title':'上書き禁止'}})['ok']
    assert store.get(pid)['title']=='本文のある原稿'
    assert not commands.handle({'command':'settings','data':{'diagnostics':'yes'}})['ok']
    assert not commands.handle({'command':'settings','data':{'recurrence':{'days':[7],'time':'09:00'}}})['ok']
    assert commands.handle({'command':'settings','data':{'favorite_tags':'#日記 #日記 #旅'}})['ok']
    assert store.setting('favorite_tags')==['日記','旅']


def test_corrupt_settings_preserved_and_not_silently_restored(tmp_path):
    store=Store(tmp_path)
    with store.connect() as c:
        c.execute('INSERT INTO settings VALUES(?,?)',('recurrence','{broken'))
        c.execute('INSERT INTO settings VALUES(?,?)',('ui_state','{"search":42}'))
    default={'days':[0],'time':'09:00'}
    assert store.setting('recurrence',default)==default
    assert store.setting('ui_state',{})=={}
    assert len(store.setting_errors)==2
    with store.connect() as c:
        assert c.execute('SELECT value FROM settings WHERE key=?',('recurrence',)).fetchone()[0]=='{broken'


def test_browser_change_requires_account_reverification(tmp_path):
    store=Store(tmp_path)
    store.set_setting('account_verified',True)
    store.set_setting('paused',False)
    commands=Commands(store)
    assert not commands.handle({'command':'settings','data':{'browser':'unknown'}})['ok']
    assert commands.handle({'command':'settings','data':{'browser':'edge'}})['ok']
    assert store.setting('browser')=='edge'
    assert store.setting('paused') is True
    assert store.setting('account_verified') is False


def test_gui_import_normalizes_due_before_starting_worker(tmp_path):
    from datetime import datetime
    class Window:
        dirty=False
        def busy(self): return False
        def start_worker(self,*args,**kwargs): self.received=kwargs
        def refresh(self): pass
    store=Store(tmp_path)
    store.set_setting('account_verified',True)
    window=Window()
    commands=Commands(store,window)
    assert not commands.handle({'command':'import','data':{'due':'2027-01-01T09:00'}})['ok']
    due='2027-01-01T09:00:00+09:00'
    assert commands.handle({'command':'import','data':{'keys':['n123456789abc'],'due':due}})['ok']
    assert window.received['due']==datetime.fromisoformat(due).timestamp()


@pytest.mark.parametrize('command', ['create','edit','publish'])
@pytest.mark.parametrize('due', ['not-a-date','2027-01-01T09:00',True,float('nan')])
def test_invalid_due_does_not_create_drafts_or_acquire_work(tmp_path,command,due):
    store=Store(tmp_path)
    store.set_setting('account_verified',True)
    pid=store.create({'editor_kind':'legacy','title':'検証','body':'本文'})
    before=store.get(pid)
    result=Commands(store).handle({'command':command,'id':pid,'data':{'due':due}})
    assert not result['ok']
    assert len(store.posts())==1
    assert store.get(pid)==before


@pytest.mark.parametrize('submitted,expected', [(False,'failed'),(True,'unknown')])
def test_publish_dispatch_failure_releases_unsubmitted_claim(tmp_path,submitted,expected):
    class Window:
        dirty=False
        def busy(self): return False
        def start_worker(self,action,post,*args,**kwargs):
            if submitted:
                store.checkpoint(post['id'],submitted=True)
            raise RuntimeError('開始時の障害')
    store=Store(tmp_path)
    store.set_setting('account_verified',True)
    pid=store.create({'editor_kind':'legacy','title':'検証','body':'本文'})
    result=Commands(store,Window()).handle({'command':'publish','id':pid})
    assert not result['ok']
    assert store.get(pid)['status']==expected
    assert bool(store.get(pid)['submitted'])==submitted
    assert store.setting('paused') is True
    other=store.create({'editor_kind':'legacy','title':'次の記事','body':'本文'})
    assert store.claim(100,post_id=other)['id']==other


def test_active_worker_is_not_unlocked_by_dispatch_error(tmp_path):
    class Window:
        dirty=False
        running=False
        def busy(self): return self.running
        def start_worker(self,*args,**kwargs):
            self.running=True
            raise RuntimeError('開始後の表示更新障害')
    store=Store(tmp_path)
    store.set_setting('account_verified',True)
    pid=store.create({'editor_kind':'legacy','title':'検証','body':'本文'})
    assert not Commands(store,Window()).handle({'command':'publish','id':pid})['ok']
    assert store.get(pid)['status']=='posting'
