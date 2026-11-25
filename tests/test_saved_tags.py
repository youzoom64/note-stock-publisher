import pytest

from note_stock.native import NativeNoteBrowser
from note_stock.store import Store


def captured(store,tags=None):
    pid=store.create({'title':'記事','body':'本文',**({'tags':tags} if tags is not None else {})})
    template={'key':'n123456789abc','account':'tester',
              'payload':{'name':'記事','free_body':'本文','hashtags':['#note側']}}
    snapshot={'title':'記事','text':'本文','document':{},'fingerprint':'same'}
    store.capture_native(pid,template,snapshot)
    return pid,template,snapshot


@pytest.mark.parametrize('tags',[['残すタグ'],[]])
def test_schedule_queue_sync_and_restart_never_write_tag_authority(tmp_path,tags):
    store=Store(tmp_path)
    pid,template,snapshot=captured(store,tags)
    before=store.get(pid)
    for status,due in [('scheduled',9999999999),('queued',None),('draft',None)]:
        store.save_fields(pid,{'status':status,'due':due})
        template['payload']['hashtags']=[]
        previous=store.get(pid)
        store.capture_native(pid,template,snapshot,{**previous,'_previous_status':status,'_previous_due':due})
        current=Store(tmp_path).get(pid)
        assert current['tags']==tags
        assert current['tags_revision']==before['tags_revision']
        assert current['status']==status
    with store.connect() as c:
        with pytest.raises(Exception,match='dedicated hashtag'):
            c.execute("UPDATE posts SET tags='[]' WHERE id=?",(pid,))


def test_old_screen_changes_only_its_edited_field_and_rejects_old_tag_revision(tmp_path):
    store=Store(tmp_path)
    pid,_,_=captured(store,['元のタグ'])
    old=store.get(pid)
    store.set_tags(pid,['新しいタグ'],expected_revision=old['tags_revision'])
    store.save_fields(pid,{'status':'queued'},expected=old)
    assert store.get(pid)['tags']==['新しいタグ']
    with pytest.raises(ValueError,match='別の操作'):
        store.save_fields(pid,{'tags':[],'status':'draft'},expected=old,tags_revision=old['tags_revision'])
    assert store.get(pid)['status']=='queued'
    assert store.get(pid)['tags']==['新しいタグ']
    store.set_tags(pid,[],expected_revision=store.get(pid)['tags_revision'])
    assert Store(tmp_path).get(pid)['tags']==[]


def test_first_import_initializes_tags_once_and_legacy_migration_is_idempotent(tmp_path):
    import sqlite3
    store=Store(tmp_path)
    pid,template,snapshot=captured(store)
    assert store.get(pid)['tags']==['note側']
    # Reproduce the previous schema without changing the original saved value.
    with sqlite3.connect(store.db) as c:
        c.execute('DROP TRIGGER legacy_hashtags_read_only')
        c.execute('DROP TRIGGER hashtag_create')
        c.execute('DROP TRIGGER hashtag_delete')
        c.execute('DROP TABLE article_hashtags')
        c.execute('UPDATE posts SET tags=?,tags_overridden=1 WHERE id=?',('["移行するタグ"]',pid))
    migrated=Store(tmp_path)
    assert migrated.get(pid)['tags']==['移行するタグ']
    assert (tmp_path/'before-independent-hashtags.sqlite3').exists()
    template['payload']['hashtags']=[]
    migrated.capture_native(pid,template,snapshot)
    assert Store(tmp_path).get(pid)['tags']==['移行するタグ']


def test_status_update_does_not_rewrite_other_fields_and_field_conflicts_are_independent(tmp_path):
    store=Store(tmp_path)
    pid=store.create({'editor_kind':'legacy','title':' タイトル ','body':'本文\n\n','tags':['タグ']})
    old=store.get(pid)
    store.save_fields(pid,{'status':'queued'})
    assert store.get(pid)['title']==old['title']
    assert store.get(pid)['body']==old['body']
    assert store.get(pid)['tags_revision']==old['tags_revision']
    store.save_fields(pid,{'title':'別の操作で変更'})
    store.save_fields(pid,{'body':'本文だけの編集'},expected=old)
    assert store.get(pid)['title']=='別の操作で変更'
    with pytest.raises(ValueError,match='タイトル'):
        store.save_fields(pid,{'title':'古い画面から変更'},expected=old)
    assert store.get(pid)['title']=='別の操作で変更'


def test_explicit_tag_edit_is_saved_before_first_remote_read(tmp_path,monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from note_stock.worker import BrowserWorker
    store=Store(tmp_path)
    pid=store.create({'title':'記事','body':'本文'})
    original=store.get(pid)
    class Adapter:
        def __init__(self,*args): self.context=SimpleNamespace(close=lambda:None)
        def open(self,*args): pass
        def new_page(self): return None
        def sync_articles(self): pass
        def edit(self,post,automatic=False):
            assert store.get(pid)['tags']==['明示したタグ']
            store.capture_native(pid,{'account':'tester','payload':{'hashtags':[]}},
                                 {'title':'記事','text':'本文','document':{},'fingerprint':'same'},post)
        def update_tags(self,*args,**kwargs): pass
    monkeypatch.setattr('note_stock.worker.NoteBrowser',Adapter)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
    worker=BrowserWorker(store,'prepare_tags',original)
    worker.items=['明示したタグ']
    worker.tags_revision=original['tags_revision']
    outcomes=[]
    worker.outcome.connect(lambda status,*rest:outcomes.append(status))
    worker.run()
    assert outcomes==['saved']
    assert Store(tmp_path).get(pid)['tags']==['明示したタグ']


@pytest.mark.parametrize("tags", [["保存したタグ"], []])
def test_saved_tags_survive_automatic_recapture_and_restart(tmp_path,tags):
    store=Store(tmp_path)
    pid=store.create({"title":"記事","body":"本文"})
    template={"key":"n123456789abc","account":"tester",
              "payload":{"name":"記事","free_body":"本文","hashtags":["#note側の古いタグ"]}}
    snapshot={"title":"記事","text":"本文","document":{},"fingerprint":"same"}
    store.capture_native(pid,template,snapshot)
    store.save(pid,"記事","本文",tags,"","draft")
    previous=store.get(pid)
    store.capture_native(pid,template,snapshot,edit_state={**previous,"_preserve_app_tags":True})
    reopened=Store(tmp_path)
    assert reopened.get(pid)["tags"]==tags
    assert reopened.native_template(pid)["payload"]["hashtags"]==["#"+tag for tag in tags]
    adapter=NativeNoteBrowser(reopened)
    adapter.update_tags(pid,["遅れて返ってきた古い値"],use_current=True)
    assert reopened.get(pid)["tags"]==tags


def test_even_explicit_body_refresh_cannot_replace_managed_tags(tmp_path):
    store=Store(tmp_path)
    pid=store.create({"title":"記事","body":"本文","tags":["以前"]})
    template={"key":"n123456789abc","account":"tester",
              "payload":{"name":"記事","free_body":"本文","hashtags":["#noteで編集"]}}
    store.capture_native(pid,template,{"title":"記事","text":"本文","document":{},"fingerprint":"same"})
    assert store.get(pid)["tags"]==["以前"]
    assert store.get(pid)["remote_tags"]==["noteで編集"]
