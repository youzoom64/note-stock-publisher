import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from note_stock.store import Store,now_ts
from note_stock.remote import submit,RemoteController
from note_stock.worker import BrowserWorker
from note_stock.discord_bot import ArticleView
from test_discord_flow import setup,interaction
from note_stock.discord_flow import flow

def published(store,pid,status='published'):
    store.set_setting('account','tester')
    with store.connect() as c:
        c.execute("UPDATE posts SET status=?,draft_key='n123456789abc',native_ready=1 WHERE id=?",(status,pid))
        c.execute("INSERT OR REPLACE INTO catalog VALUES('tester','n123456789abc',?,'published','a',?)",(pid,now_ts()))

@pytest.mark.parametrize('status',['published','scheduled','draft','review'])
def test_common_paths_reject_public_article_without_mutation(tmp_path,status):
    s=Store(tmp_path);s.set_setting('account_verified',True);pid=s.create({'title':'public','body':'body'})
    published(s,pid,status);before=s.get(pid)
    for call in [lambda:submit(s,'schedule',pid,{'due':now_ts()+600}),lambda:s.set_schedule(pid,now_ts()+600),
                 lambda:s.save(pid,'public','body',[],'','scheduled',now_ts()+600)]:
        with pytest.raises(ValueError,match='投稿済み'):call()
        assert s.get(pid)==before
    assert s.next_job() is None

def test_worker_stale_schedule_does_not_open_browser_or_damage_public_post(tmp_path,monkeypatch):
    s=Store(tmp_path);pid=s.create({'title':'public'});published(s,pid)
    browser=Mock();monkeypatch.setattr('note_stock.worker.NoteBrowser',browser)
    worker=BrowserWorker(s,'prepare_schedule',s.get(pid));results=[]
    worker.outcome.connect(lambda status,message,url:results.append((status,message)))
    worker.run()
    assert results[0][0]=='failed' and '投稿済み' in results[0][1]
    browser.assert_not_called();assert s.get(pid)['status']=='published'

def test_ui_button_stale_callback_and_slash_reject_published_schedule(tmp_path):
    async def run():
        s,client,pid=setup(tmp_path);published(s,pid)
        view=ArticleView(client,s.get(pid));assert view.schedule.disabled and not view.publish.disabled
        i=interaction();flow(client).install(view,i.message)
        await view.schedule.callback(i)
        assert isinstance(i.response.edit_message.call_args.kwargs['view'],ArticleView)
        other=interaction(mid=None);await client.article_command(other,'schedule',pid)
        assert s.next_job() is None
        await client.close()
    asyncio.run(run())

def test_sync_repairs_public_reservation_preserving_draft_reservation(tmp_path):
    s=Store(tmp_path);s.set_setting('account','tester')
    row={'key':'n123456789abc','title':'public','status':'draft','updated':'a'}
    pid=s.merge_catalog([row])[0]['id']
    draft=s.create({'title':'draft'})
    with s.connect() as c:
        c.execute("UPDATE posts SET status='scheduled',due=? WHERE id IN (?,?)",(now_ts()+600,pid,draft))
    before=s.get(draft)
    s.merge_catalog([{**row,'status':'published'}])
    assert s.get(pid)['status']=='published' and s.get(pid)['due'] is None
    assert s.get(draft)==before

def test_queued_request_rechecks_public_state_before_worker_start(tmp_path,monkeypatch):
    s=Store(tmp_path);s.set_setting('account_verified',True)
    pid=s.create({'title':'draft'})
    with s.connect() as c:c.execute("UPDATE posts SET native_ready=1,draft_key='n123456789abc' WHERE id=?",(pid,))
    jid=submit(s,'schedule',pid,{'due':now_ts()+600})
    published(s,pid)
    window=SimpleNamespace(quitting=False,busy=lambda:False,dirty=False)
    controller=SimpleNamespace(window=window,store=s,current=None,last_sync=now_ts(),finish=Mock())
    browser=Mock();monkeypatch.setattr('note_stock.remote.BrowserWorker',browser)
    RemoteController.tick(controller)
    assert controller.current['id']==jid
    assert controller.finish.call_args.args[0]=='failed'
    assert '投稿済み' in controller.finish.call_args.args[1]
    browser.assert_not_called()
    assert s.get(pid)['status']=='published'
