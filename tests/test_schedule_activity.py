import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from note_stock.schedule_activity import ScheduleActivity,register,service
from note_stock.store import Store,now_ts
from note_stock.secrets import protect
from note_stock.discord_flow import flow
from test_discord_flow import setup,interaction

def configure(s,pid):
    s.set_setting('account_verified',True)
    s.set_setting('discord',{'application':'4','owner':'1','guild':'2','channel':'3'})
    s.set_setting('discord_token',protect('test-bot-token'))
    s.set_setting('remote_relogin',{'https_url':'https://example.com','activity_confirmed':True,'schedule_verified':True})
    s.set_setting('discord_active_ui','screen')
    with s.connect() as c:c.execute("UPDATE posts SET native_ready=1,draft_key='n123456789abc' WHERE id=?",(pid,))

def request(token='session',body=None):
    return SimpleNamespace(headers={'Authorization':'Bearer '+token},query={},json=AsyncMock(return_value=body or {}))

def paired(tmp_path):
    s=Store(tmp_path);pid=s.create({'title':'test','body':'body'});configure(s,pid)
    api=ScheduleActivity(s);lid=api.prepare(pid,'1','screen');api.bind(lid,'instance')
    with s.connect() as c:c.execute('UPDATE activity_reservations SET session_hash=? WHERE id=?',(hashlib.sha256(b'session').hexdigest(),lid))
    return s,pid,api,lid

@pytest.mark.parametrize('bad',['identity','application','guild','channel','participants'])
def test_auth_rejects_spoofed_identity_or_activity(tmp_path,bad):
    async def run():
        s,pid,api,lid=paired(tmp_path)
        async def discord(path,token,**kwargs):
            if path=='/oauth2/token':return {'access_token':'oauth'}
            if path=='/users/@me':return {'id':'9' if bad=='identity' else '1'}
            if path=='/oauth2/@me':return {'application':{'id':'9' if bad=='application' else '4'}}
            return {'users':[] if bad=='participants' else ['1'],'location':{'guild_id':'9' if bad=='guild' else '2','channel_id':'9' if bad=='channel' else '3'}}
        api.discord=discord
        with pytest.raises(web.HTTPForbidden):await api.auth(request(body={'code':'code','verifier':'v'*64,'instance':'instance'}))
    asyncio.run(run())

def test_auth_and_reopen_uses_discord_identity_without_pairing_code(tmp_path):
    async def run():
        s,pid,api,lid=paired(tmp_path)
        async def discord(path,token,**kwargs):
            if path=='/oauth2/token':
                assert kwargs['data']['code_verifier']=='v'*64 and 'client_secret' not in kwargs['data']
                return {'access_token':'oauth'}
            if path=='/users/@me':return {'id':'1'}
            if path=='/oauth2/@me':return {'application':{'id':'4'}}
            return {'users':['1'],'location':{'guild_id':'2','channel_id':'3'}}
        api.discord=discord
        reply=json.loads((await api.auth(request(body={'code':'code','verifier':'v'*64,'instance':'instance'}))).body)
        assert reply['access_token']=='oauth'
        assert api.authorize(request(reply['session']))['post_id']==pid
        with pytest.raises(web.HTTPUnauthorized):api.authorize(request())
    asyncio.run(run())

def test_commit_duplicate_cancel_preserve_existing_reservation(tmp_path):
    async def run():
        s,pid,api,lid=paired(tmp_path);due=now_ts()+600
        first=json.loads((await api.commit(request(body={'due':due}))).body)
        second=json.loads((await api.commit(request(body={'due':due+60}))).body)
        assert first==second
        assert s.job(first['job_id'])['data']['due']==due
        await api.cancel(request())
        assert api.row('instance')['state']=='submitted'
        assert len(s.operation_history()['items'])==1
    asyncio.run(run())

@pytest.mark.parametrize('change',['published','revision','old_screen','past'])
def test_commit_rechecks_common_guards(tmp_path,change):
    async def run():
        s,pid,api,lid=paired(tmp_path)
        if change=='old_screen':s.set_setting('discord_active_ui','new-screen')
        if change in {'published','revision'}:
            with s.connect() as c:c.execute('UPDATE posts SET '+("status='published'" if change=='published' else 'updated=updated+1')+' WHERE id=?',(pid,))
        with pytest.raises(web.HTTPConflict):await api.commit(request(body={'due':now_ts()+(-60 if change=='past' else 600)}))
        assert s.next_job() is None
    asyncio.run(run())

def test_cancel_exit_repeat_and_new_launch_never_changes_post(tmp_path):
    async def run():
        s,pid,api,lid=paired(tmp_path);before=s.get(pid)
        await api.cancel(request());await api.cancel(request())
        assert s.get(pid)==before and len(s.pending_notifications())==1
        newer=api.prepare(pid,'1','screen');api.bind(newer,'new-instance')
        with pytest.raises(web.HTTPUnauthorized):api.authorize(request())
        assert api.row('new-instance')['post_id']==pid
    asyncio.run(run())

def test_both_discord_entrypoints_launch_activity_without_calendar_or_browser(tmp_path,monkeypatch):
    from note_stock.discord_bot import ArticleView
    async def run():
        s,client,pid=setup(tmp_path);configure(s,pid)
        client.disable_menu=AsyncMock()
        view=ArticleView(client,s.get(pid));i=interaction();flow(client).install(view,i.message)
        i.response.launch_activity=AsyncMock(return_value=SimpleNamespace(activity_id='one'))
        await view.schedule.callback(i)
        i.response.launch_activity.assert_awaited_once();i.response.send_modal.assert_not_awaited()
        assert flow(client).state()['phase']=='activity'
        assert not await view.interaction_check(interaction())
        j=interaction(mid=None);j.response.launch_activity=AsyncMock(return_value=SimpleNamespace(activity_id='two'))
        await client.article_command(j,'schedule',pid)
        j.response.launch_activity.assert_awaited_once()
        assert service(s).row('two')['post_id']==pid
        assert s.next_job() is None
        await client.close()
    asyncio.run(run())

def test_discord_reservation_calendar_without_relay_queues_after_confirmation(tmp_path):
    from datetime import datetime, timedelta
    from note_stock.discord_bot import ArticleView, ReservationCalendar
    from note_stock.store import JST

    async def run():
        store,client,pid=setup(tmp_path)
        configure(store,pid)
        store.set_setting('remote_relogin',{})
        client.disable_menu=AsyncMock()
        today=datetime.now(JST)
        selected=today+timedelta(days=1)
        article=ArticleView(client,store.get(pid))
        opened=interaction()
        flow(client).install(article,opened.message)
        await article.schedule.callback(opened)
        view=opened.response.edit_message.call_args.kwargs['view']
        assert isinstance(view,ReservationCalendar)
        if view.month!=selected.month:
            changed=interaction()
            changed.message=opened.message
            await view.following.callback(changed)
            view=changed.response.edit_message.call_args.kwargs['view']
        days=view.parts[0]
        day=next(item for row in days.children for item in row.children if item.label.startswith(f'{selected.day}日 '))
        pick=interaction()
        pick.message=days.message
        await day.callback(pick)
        modal=pick.response.send_modal.call_args.args[0]
        modal.clock._value='09:30'
        entered=interaction()
        entered.message=days.message
        await modal.on_submit(entered)
        confirmation=opened.message.edit.call_args.kwargs['view']
        assert store.next_job() is None
        await confirmation.yes.callback(interaction())
        job=store.next_job()
        assert job['action']=='schedule' and job['post_id']==pid
        assert datetime.fromtimestamp(job['data']['due'],JST).strftime('%d %H:%M')==f'{selected.day:02d} 09:30'
        await client.close()

    asyncio.run(run())


@pytest.mark.parametrize('activity_configured',[True,False])
def test_schedule_button_opens_calendar_without_reading_unchecked_draft(tmp_path,activity_configured):
    from note_stock.discord_bot import ArticleView, ReservationCalendar

    async def run():
        store,client,pid=setup(tmp_path)
        configure(store,pid)
        store.set_setting('remote_relogin',{'schedule_verified':activity_configured})
        with store.connect() as connection:
            connection.execute('UPDATE posts SET native_ready=0 WHERE id=?',(pid,))
        article=ArticleView(client,store.get(pid))
        assert not article.schedule.disabled
        click=interaction()
        flow(client).install(article,click.message)
        await article.schedule.callback(click)
        result=click.response.edit_message.call_args.kwargs
        assert isinstance(result['view'],ReservationCalendar)
        assert not store.get(pid)['native_ready']
        assert store.next_job() is None and not store.pending_notifications()
        await client.close()

    asyncio.run(run())


def test_http_state_requires_session_and_month_counts_use_japan_time(tmp_path):
    async def run():
        s,pid,api,lid=paired(tmp_path)
        app=web.Application();app.router.add_get('/state',api.state)
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/state')).status==401
            response=await client.get('/state?year=2028&month=2',headers={'Authorization':'Bearer session'})
            data=await response.json();assert response.status==200 and data['title']=='test'
            assert data['month']==2 and data['counts']=={}
            assert (await client.get('/state?month=13',headers={'Authorization':'Bearer session'})).status==400
    asyncio.run(run())


def test_exit_before_authorization_finishes_once_and_preserves_reservation(tmp_path):
    store,pid,api,launch_id=paired(tmp_path)
    before=store.get(pid)
    api.closed(launch_id)
    api.closed(launch_id)
    assert store.get(pid)==before and store.next_job() is None
    [(record_id,result)]=store.pending_notifications()
    assert record_id=='activity-disconnect:'+launch_id
    assert result['ok'] and '本人確認を完了せず' in result['message']
    assert api.current_launch() is None


def test_late_exit_does_not_replace_a_newer_menu(tmp_path):
    store,pid,api,launch_id=paired(tmp_path)
    store.set_setting('discord_active_ui','newer-screen')
    api.closed(launch_id)
    assert not store.pending_notifications()
    assert store.setting('discord_active_ui')=='newer-screen'
