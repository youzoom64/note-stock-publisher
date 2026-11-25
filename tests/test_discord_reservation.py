import asyncio
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock

import pytest
from note_stock.discord_bot import ReservationTime
from note_stock.discord_bot import ArticleView,Confirm,OLD_RESERVATION_PROGRESS,PublishCheckView,ReservationCalendar
from note_stock.discord_flow import flow
from note_stock.remote import RemoteController,submit
from note_stock.store import JST,now_ts
from note_stock.worker import BrowserWorker
from test_discord_flow import interaction,setup


def test_time_select_supports_every_hour_and_exact_minutes(tmp_path):
    async def run():
        store,client,pid=setup(tmp_path)
        calendar=ReservationCalendar(client,store.get(pid),('all',0,''),2099,12)
        clock=ReservationTime(calendar,2,9,32)
        assert len(clock.hours.options)==24
        assert any(option.value=='32' and option.default for option in clock.minutes.options)
        clock.hours._values=['23']
        selected=interaction()
        flow(client).install(clock,selected.message)
        await clock.select_hour(selected)
        updated=selected.response.edit_message.call_args.kwargs['view']
        assert (updated.hour,updated.minute)==(23,32)
        updated.minutes._values=['55']
        minutes=interaction()
        await updated.select_minute(minutes)
        updated=minutes.response.edit_message.call_args.kwargs['view']
        assert (updated.hour,updated.minute)==(23,55)
        step=interaction()
        await updated.later.callback(step)
        updated=step.response.edit_message.call_args.kwargs['view']
        assert (updated.hour,updated.minute)==(23,56)
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('owner,guild,channel',[(1,2,3),(111,222,333)])
def test_discord_reservation_of_unread_note_draft(tmp_path,monkeypatch,owner,guild,channel):
    def click():
        i=interaction()
        i.user.id,i.guild_id,i.channel_id=owner,guild,channel
        return i

    async def run():
        store,client,pid=setup(tmp_path)
        store.set_setting('discord',{'owner':str(owner),'guild':str(guild),'channel':str(channel)})
        store.checkpoint(pid,key='n123456789abc')
        opened=click()
        detail=ArticleView(client,store.get(pid))
        flow(client).install(detail,opened.message)
        await detail.schedule.callback(opened)
        calendar=opened.response.edit_message.call_args.kwargs['view']
        assert isinstance(calendar,ReservationCalendar)
        assert store.next_job() is None

        forward=click()
        forward.message=opened.message
        await calendar.following.callback(forward)
        month=forward.response.edit_message.call_args.kwargs['view']
        days=month.parts[0]
        day=days.children[0].children[0]
        chosen=click()
        chosen.message=days.message
        stranger=interaction()
        stranger.user.id=owner+1000
        stranger.guild_id,stranger.channel_id=guild,channel
        stranger.message=days.message
        assert not await days.interaction_check(stranger)
        assert await days.interaction_check(chosen)
        await day.callback(chosen)
        clock=opened.message.edit.call_args.kwargs['view']
        assert clock.day==1 and f'{month.month}月1日' in clock.text
        chosen.response.send_modal.assert_not_awaited()
        clock.hour,clock.minute=9,30
        date_confirm=click()
        date_confirm.message=opened.message
        await clock.confirm.callback(date_confirm)
        confirm=date_confirm.response.edit_message.call_args.kwargs['view']
        days.message.delete.assert_awaited_once()
        accepted=click()
        accepted.message=opened.message
        await confirm.yes.callback(accepted)
        job=store.next_job()
        assert job['action']=='schedule' and job['post_id']==pid
        assert len(store.operation_history()['items'])==1

        class Adapter:
            open=Mock()
            page=Mock()
            update_tags=Mock()
            sync_articles=Mock()
            publish=Mock()
            disconnect=Mock()
            def edit(self,post,automatic):
                assert automatic
                store.capture_native(post['id'],{'account':'test','payload':{'free_body':'<p>body</p>'}},
                    {'title':'sample','text':'body','document':{},'fingerprint':'test'},post)
            def new_page(self): return None

        adapter=Adapter()
        monkeypatch.setattr('note_stock.worker.NoteBrowser',lambda *args:adapter)
        monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
        monkeypatch.setattr('note_stock.headless_sync.open_note_session',lambda *args:nullcontext())
        worker=BrowserWorker(store,'prepare_schedule',store.get(pid))
        worker.import_due=job['data']['due']
        results=[]
        worker.outcome.connect(lambda *args:results.append(args))
        worker.run()
        status,message,url=results.pop()
        assert status=='saved',message
        store.complete_job(job['id'],True,message,url)
        assert store.get(pid)['status']=='scheduled'
        assert store.get(pid)['due']==job['data']['due']
        assert store.pending_notifications()[0][1]['ok']
        adapter.publish.assert_not_called()
        await client.close()
    asyncio.run(run())


def test_day_buttons_fit_discord_limits_and_disable_past_days(tmp_path):
    async def run():
        store,client,pid=setup(tmp_path)
        today=datetime.now(JST).date()
        year=today.year+1
        month=ReservationCalendar(client,store.get(pid),('all',0,''),year,1)
        blocks=month.parts[0].to_components()
        buttons=[button for row in blocks for button in row['components']]
        assert all(len(row['components'])<=5 for row in blocks)
        assert len(buttons)==31 and len(blocks)+len(buttons)<=40
        assert not any(button['disabled'] for button in buttons)
        current=ReservationCalendar(client,store.get(pid),('all',0,''),today.year,today.month)
        buttons=[button for row in current.parts[0].to_components() for button in row['components']]
        assert all(button['disabled'] for button in buttons[:today.day-1])
        assert not buttons[today.day-1]['disabled']
        assert buttons[today.day-1]['label'].startswith(f'{today.day}日')
        await client.close()
    asyncio.run(run())


def test_discord_calendar_is_fallback_when_activity_cannot_launch(tmp_path,monkeypatch):
    async def run():
        store,client,pid=setup(tmp_path)
        store.checkpoint(pid,key='n123456789abc')
        with store.connect() as c:
            c.execute('UPDATE posts SET native_ready=1 WHERE id=?',(pid,))
        store.set_setting('remote_relogin',{'schedule_verified':True})
        def unavailable(*args):
            raise ValueError('HTTPS中継が利用できません。')
        monkeypatch.setattr('note_stock.schedule_activity.service',lambda store:SimpleNamespace(prepare=unavailable))
        click=interaction()
        detail=ArticleView(client,store.get(pid))
        flow(client).install(detail,click.message)
        await detail.schedule.callback(click)
        assert isinstance(click.response.edit_message.call_args.kwargs['view'],ReservationCalendar)
        assert not store.pending_notifications()
        await client.close()
    asyncio.run(run())


def test_old_reservation_wait_is_retired_after_reconnect(tmp_path):
    async def run():
        store,client,pid=setup(tmp_path)
        jid=store.enqueue('refresh_article',pid,request_key='reservation-preflight:past')
        store.recover_jobs()
        assert store.job(jid)['state']=='failed' and not store.pending_notifications()
        async def history(**_):
            yield SimpleNamespace(author=SimpleNamespace(id=1),id=10,components=[],content=OLD_RESERVATION_PROGRESS)
            yield SimpleNamespace(author=SimpleNamespace(id=2),id=11,components=[],content=OLD_RESERVATION_PROGRESS)
            yield SimpleNamespace(author=SimpleNamespace(id=1),id=12,components=[],content='別のメッセージ')
        client._connection.user=SimpleNamespace(id=1)
        await client.disable_older_menus(SimpleNamespace(history=history))
        client.disable_menu.assert_awaited_once_with(10)
        await client.close()
    asyncio.run(run())


def test_reservation_is_not_stuck_behind_unsaved_pc_edits(tmp_path,monkeypatch):
    store,_,pid=setup(tmp_path)
    store.checkpoint(pid,key='n123456789abc')
    store.enqueue('sync_articles')
    jid=submit(store,'schedule',pid,{'due':now_ts()+600})
    window=SimpleNamespace(quitting=False,busy=lambda:False,dirty=True,current_id=pid)
    controller=SimpleNamespace(window=window,store=store,current=None,last_sync=now_ts(),finish=Mock())
    browser=Mock()
    monkeypatch.setattr('note_stock.remote.BrowserWorker',browser)
    RemoteController.tick(controller)
    assert controller.current['id']==jid
    assert '編集中' in controller.finish.call_args.args[1]
    browser.assert_not_called()


def test_queued_reservation_expires_and_reports_failure_without_execution(tmp_path):
    store,_,pid=setup(tmp_path)
    store.checkpoint(pid,key='n123456789abc')
    jid=submit(store,'schedule',pid,{'due':now_ts()+900})
    store.expire_waiting_reservations(now_ts()+122)
    assert store.next_job() is None
    assert store.job(jid)['state']=='failed' and not store.get(pid)['due']
    assert store.pending_notifications()[0][0]=='job:'+jid


@pytest.mark.parametrize('outcome',['draft','published','review','failed'])
def test_publish_button_checks_note_conditions_before_offering_confirmation(tmp_path,outcome):
    async def run():
        store,client,pid=setup(tmp_path)
        store.checkpoint(pid,key='n123456789abc')
        if outcome=='published':
            store.finish(pid,outcome)
        elif outcome=='review':
            with store.connect() as c:
                c.execute("UPDATE posts SET status='review' WHERE id=?",(pid,))
        opened=interaction()
        article=ArticleView(client,store.get(pid))
        flow(client).install(article,opened.message)
        assert not article.publish.disabled
        pending=asyncio.create_task(article.publish.callback(opened))
        job=None
        for _ in range(50):
            job=store.next_job()
            if job:
                break
            await asyncio.sleep(.01)
        assert job and job['action']=='refresh_article'
        assert job['request_key'].startswith('publish-preflight:')
        assert isinstance(opened.edit_original_response.call_args.kwargs['view'],PublishCheckView)
        if outcome!='failed':
            with store.connect() as c:
                c.execute("UPDATE posts SET native_ready=1,native_summary=? WHERE id=?",('{"price":300}',pid))
        store.complete_job(job['id'],outcome!='failed','読み込みに失敗しました。')
        await asyncio.wait_for(pending,timeout=3)
        response=opened.edit_original_response.call_args.kwargs
        assert store.next_job() is None
        assert not store.pending_notifications()
        if outcome=='failed':
            assert isinstance(response['view'],ArticleView) and '読み込みに失敗しました' in response['content']
        else:
            assert isinstance(response['view'],Confirm) and '300円' in response['content']
            await response['view'].yes.callback(interaction())
            assert store.next_job()['action']=='publish_note'
        await client.close()
    asyncio.run(run())


def test_linked_draft_can_open_tag_input_before_native_capture(tmp_path):
    async def run():
        store,client,pid=setup(tmp_path)
        store.checkpoint(pid,key='n123456789abc')
        opened=interaction()
        article=ArticleView(client,store.get(pid))
        flow(client).install(article,opened.message)
        assert not article.tags.disabled
        await article.tags.callback(opened)
        modal=opened.response.send_modal.call_args.args[0]
        modal.tags._value='日記 メモ'
        await modal.on_submit(interaction())
        job=store.next_job()
        assert job['action']=='tags' and job['data']['tags']==['日記','メモ']
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('success',[True,False])
def test_preparing_publish_keeps_public_article_and_never_publishes(tmp_path,monkeypatch,success):
    store,_,pid=setup(tmp_path)
    key='n123456789abc'
    url='https://note.com/test/n/'+key
    store.checkpoint(pid,key=key)
    store.finish(pid,'published',url=url)
    adapter=Mock()
    def edit(post,automatic):
        assert automatic
        if not success:
            raise ValueError('公開条件を読み込めませんでした。')
        store.capture_native(pid,{'account':'test','payload':{'price':400}},
                             {'document':{},'fingerprint':'test','text':'body','title':'sample'},post)
    adapter.edit.side_effect=edit
    monkeypatch.setattr('note_stock.worker.NoteBrowser',lambda *args:adapter)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
    monkeypatch.setattr('note_stock.headless_sync.open_note_session',lambda *args:nullcontext())
    worker=BrowserWorker(store,'prepare_publish_check',store.get(pid))
    outcomes=[]
    worker.outcome.connect(lambda status,message,_:outcomes.append((status,message)))
    worker.run()
    current=store.get(pid)
    assert outcomes[0][0]==('saved' if success else 'failed'),outcomes
    assert current['status']=='published' and current['url']==url and bool(current['native_ready'])==success
    adapter.publish.assert_not_called()
