"""Audit every return route without publishing, deleting, or creating real articles."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from note_stock.store import Store,now_ts
from note_stock.discord_bot import (NoteClient,Menu,Articles,ArticleView,InfoView,ActionResult,
    Confirm,ScheduleConfirm,ScheduleModal,TagsModal,NewModal,ArticleSelect,OperationHistory,RECORD_FOOTER)


def interaction(message=99):
    source=SimpleNamespace(id=message,edit=AsyncMock(),delete=AsyncMock()) if message is not None else None
    return SimpleNamespace(id=1000,user=SimpleNamespace(id=1),guild_id=2,channel_id=3,message=source,extras={},
        response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock(),send_modal=AsyncMock(),defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),original_response=AsyncMock(return_value=source),
        edit_original_response=AsyncMock(return_value=source))


def setup(tmp_path):
    store=Store(tmp_path)
    store.set_setting('discord',{'owner':'1','guild':'2','channel':'3','menu':'99'})
    store.set_setting('account_verified',True)
    client=NoteClient(store); client.http.edit_message=AsyncMock()
    client.http.get_message=AsyncMock(return_value={'content':'note管理'})
    client.http.delete_message=AsyncMock()
    return store,client


@pytest.mark.parametrize('page,expected',[('commands',InfoView),('status',InfoView),('schedules',Articles),('articles',Articles)])
def test_each_menu_page_has_a_working_home_route_on_the_same_message(tmp_path,page,expected):
    async def run():
        store,client=setup(tmp_path); i=interaction(); menu=Menu(client,root=True)
        await getattr(menu,page).callback(i)
        view=i.response.edit_message.call_args.kwargs['view']
        assert isinstance(view,expected)
        await view.back.callback(i)
        assert isinstance(i.response.edit_message.call_args.kwargs['view'],Menu)
        assert i.response.edit_message.await_count==2
        i.response.send_message.assert_not_awaited(); i.followup.send.assert_not_awaited()
        i.message.edit.assert_not_awaited(); client.http.edit_message.assert_not_awaited()
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('route,expected',[('back',Articles),('home',Menu)])
def test_action_receipt_can_return_to_list_or_home_without_more_messages(tmp_path,route,expected):
    async def run():
        store,client=setup(tmp_path); i=interaction(); view=ActionResult(client)
        view.message=i.message; view.message_id=99
        await getattr(view,route).callback(i)
        assert isinstance(i.response.edit_message.call_args.kwargs['view'],expected)
        i.response.send_message.assert_not_awaited(); i.followup.send.assert_not_awaited()
        i.message.edit.assert_not_awaited()
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('kind',['publish','delete','schedule'])
def test_cancel_confirmation_preserves_article_list_context_and_can_return_home(tmp_path,kind):
    async def run():
        store,client=setup(tmp_path)
        for n in range(42): store.create({'title':'対象 '+str(n),'body':'body'})
        post=store.posts()[0]; listing=('all',1,'対象 ')
        view=ScheduleConfirm(client,post['id'],now_ts()+600,listing) if kind=='schedule' else Confirm(client,post,kind+'_note',listing)
        i=interaction(); await view.no.callback(i)
        detail=i.response.edit_message.call_args.kwargs['view']
        assert isinstance(detail,ArticleView) and detail.listing==listing
        await detail.back.callback(i)
        articles=i.response.edit_message.call_args.kwargs['view']
        assert (articles.kind,articles.page,articles.query)==listing
        await articles.back.callback(i)
        assert isinstance(i.response.edit_message.call_args.kwargs['view'],Menu)
        assert i.response.edit_message.await_count==3
        i.response.send_message.assert_not_awaited(); i.followup.send.assert_not_awaited()
        assert store.get(post['id'])==post and store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_invalid_schedule_returns_to_article_with_retry_and_back_buttons(tmp_path):
    async def run():
        store,client=setup(tmp_path); pid=store.create({'title':'sample','body':'body'})
        with store.connect() as c: c.execute('UPDATE posts SET native_ready=1 WHERE id=?',(pid,))
        ArticleView(client,store.get(pid))
        modal=ScheduleModal(client,pid); modal.when._value='invalid date'
        i=interaction(); await modal.on_submit(i)
        assert '日本時間' in i.response.edit_message.call_args.kwargs['content']
        assert 'does not match' not in i.response.edit_message.call_args.kwargs['content']
        detail=i.response.edit_message.call_args.kwargs['view']
        assert isinstance(detail,ArticleView)
        assert not detail.schedule.disabled
        await detail.back.callback(i)
        await i.response.edit_message.call_args.kwargs['view'].back.callback(i)
        assert isinstance(i.response.edit_message.call_args.kwargs['view'],Menu)
        i.response.send_message.assert_not_awaited()
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('problem',['missing-title','invalid-tags'])
def test_new_draft_input_error_has_home_button_and_creates_no_draft(tmp_path,problem):
    async def run():
        store,client=setup(tmp_path); Menu(client)
        modal=NewModal(client); modal.name._value=' ' if problem=='missing-title' else 'title'; modal.body._value='body'
        if problem=='invalid-tags': modal.tags._value='bad#tag'
        i=interaction(); await modal.on_submit(i)
        view=i.edit_original_response.call_args.kwargs['view']
        assert isinstance(view,InfoView)
        await view.back.callback(i)
        assert isinstance(i.response.edit_message.call_args.kwargs['view'],Menu)
        i.followup.send.assert_not_awaited()
        assert store.posts()==[] and store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_invalid_tag_submission_has_both_return_routes_without_changing_article(tmp_path):
    async def run():
        store,client=setup(tmp_path); pid=store.create({'title':'sample','body':'body'})
        with store.connect() as c: c.execute('UPDATE posts SET native_ready=1 WHERE id=?',(pid,))
        before=store.get(pid); ArticleView(client,before)
        modal=TagsModal(client,pid); modal.tags._value='bad#tag'
        i=interaction(); await modal.on_submit(i)
        view=i.edit_original_response.call_args.kwargs['view']
        assert isinstance(view,ActionResult) and not view.back.disabled and not view.home.disabled
        await view.home.callback(i)
        assert isinstance(i.response.edit_message.call_args.kwargs['view'],Menu)
        i.followup.send.assert_not_awaited()
        assert store.get(pid)==before and store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_new_draft_slash_error_has_home_button(tmp_path):
    async def run():
        store,client=setup(tmp_path); i=interaction(message=None)
        await client.tree.get_command('note_new').callback(i,title='title',body='')
        view=i.followup.send.call_args.kwargs['view']
        assert isinstance(view,InfoView)
        back=interaction(message=150); await view.back.callback(back)
        assert isinstance(back.response.edit_message.call_args.kwargs['view'],Menu)
        back.response.send_message.assert_not_awaited()
        assert store.posts()==[] and store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_empty_filter_has_home_button_and_stays_on_same_message(tmp_path):
    async def run():
        store,client=setup(tmp_path); store.create({'title':'sample','body':'body'})
        listing=Articles(client); listing.filter._values=['published']
        i=interaction(); await listing.filter.callback(i)
        empty=i.response.edit_message.call_args.kwargs['view']
        assert empty.total==0 and empty.kind=='published'
        await empty.back.callback(i)
        assert isinstance(i.response.edit_message.call_args.kwargs['view'],Menu)
        assert i.response.edit_message.await_count==2
        i.response.send_message.assert_not_awaited(); i.followup.send.assert_not_awaited()
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('route,modal_class',[('schedule',ScheduleModal),('tags',TagsModal),('new',NewModal)])
def test_opening_modal_keeps_parent_usable_when_discord_cancels_input(tmp_path,route,modal_class):
    async def run():
        store,client=setup(tmp_path)
        pid=store.create({'title':'sample','body':'body'})
        with store.connect() as c: c.execute('UPDATE posts SET native_ready=1 WHERE id=?',(pid,))
        parent=Menu(client) if route=='new' else ArticleView(client,store.get(pid))
        session=parent.session; i=interaction()
        await getattr(parent,route).callback(i)
        assert isinstance(i.response.send_modal.call_args.args[0],modal_class)
        assert store.setting('discord_active_ui')==session
        assert not parent.is_finished()
        assert await parent.interaction_check(interaction())
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('kind',['select','confirm','schedule','publish','delete'])
def test_missing_article_returns_to_list_instead_of_trapping_the_user(tmp_path,kind):
    async def run():
        store,client=setup(tmp_path); pid=store.create({'title':'sample','body':'body'})
        post=store.get(pid); i=interaction()
        if kind=='select':
            view=ArticleSelect(client,[post]); view._values=[pid]
        elif kind=='confirm': view=Confirm(client,post,'delete_note')
        elif kind=='schedule': view=ScheduleConfirm(client,pid,now_ts()+600)
        else: view=ArticleView(client,post)
        store.delete(pid)
        if kind=='select': await view.callback(i)
        elif kind in {'confirm','schedule'}: await view.no.callback(i)
        else: await getattr(view,kind).callback(i)
        listing=i.response.edit_message.call_args.kwargs['view']
        assert isinstance(listing,Articles) and listing.total==0
        await listing.back.callback(i)
        assert isinstance(i.response.edit_message.call_args.kwargs['view'],Menu)
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_saved_page_is_clamped_when_the_list_shrinks(tmp_path):
    async def run():
        store,client=setup(tmp_path); store.create({'title':'sample','body':'body'})
        listing=Articles(client,page=5)
        assert listing.page==0 and listing.prev.disabled and listing.next.disabled
        assert any(isinstance(item,ArticleSelect) for item in listing.children)
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('command',['note_help','note_status'])
def test_information_slash_commands_also_have_home_button(tmp_path,command):
    async def run():
        store,client=setup(tmp_path); i=interaction(message=None)
        await client.tree.get_command(command).callback(i)
        view=i.response.send_message.call_args.kwargs['view']
        assert isinstance(view,InfoView)
        back=interaction(message=150); await view.back.callback(back)
        assert isinstance(back.response.edit_message.call_args.kwargs['view'],Menu)
        back.response.send_message.assert_not_awaited()
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_execution_records_are_separate_from_new_menus_and_never_edited(tmp_path):
    async def run():
        store,client=setup(tmp_path); messages={99:{'content':'note管理'}}; sequence=[]; edits=[]; deleted=[]
        client.is_ready=lambda:True; client.get_channel=lambda _:SimpleNamespace(id=3)
        async def send(channel,params):
            mid=150+len(sequence); sequence.append(mid); messages[mid]=dict(params.payload)
            return {'id':str(mid)}
        async def get(channel,mid): return messages[mid]
        async def edit(channel,mid,params): edits.append(mid); messages[mid].update(params.payload)
        async def delete(channel,mid): deleted.append(mid); messages.pop(mid,None)
        client.http.send_message=send; client.http.get_message=get; client.http.edit_message=edit
        client.http.delete_message=delete
        first=store.notification(True,'一件目の実行結果')
        await client.notifications.coro(client)
        first_ids=store.notification_message_ids(first)
        result_id=int(first_ids['result_id']); menu_id=int(first_ids['menu_id'])
        record=dict(messages[result_id])
        assert result_id<menu_id and not record.get('components')
        store.notification(False,'二件目の失敗結果')
        await client.notifications.coro(client)
        assert messages[result_id]==record and result_id not in edits and result_id not in deleted
        assert menu_id in deleted and menu_id not in messages
        assert len(sequence)==4 and messages[sequence[-1]].get('components')
        await client.close()
    asyncio.run(run())


def test_retry_after_result_send_does_not_send_the_execution_record_twice(tmp_path):
    import discord
    async def run():
        store,client=setup(tmp_path); client.is_ready=lambda:True; client.get_channel=lambda _:SimpleNamespace(id=3)
        nid=store.notification(True,'保存完了'); calls=[]
        async def send(channel,params):
            calls.append(dict(params.payload))
            if len(calls)==2:
                raise discord.HTTPException(SimpleNamespace(status=503,reason='temporary'),'temporary')
            return {'id':str(150+len(calls))}
        client.http.send_message=send
        await client.notifications.coro(client)
        assert store.notification_message_ids(nid)['result_id']=='151'
        assert store.pending_notifications()
        await client.notifications.coro(client)
        assert len(calls)==3 and sum(not row.get('components') for row in calls)==1
        assert not store.pending_notifications()
        assert store.notification_message_ids(nid)=={'result_id':'151','menu_id':'153'}
        await client.close()
    asyncio.run(run())


def test_execution_receipt_loses_controls_but_is_not_replaced_by_expired_text(tmp_path):
    async def run():
        store,client=setup(tmp_path); pid=store.create({'title':'sample','body':'body'})
        i=interaction(); view=Confirm(client,store.get(pid),'delete_note')
        view.message=i.message; view.message_id=99
        await view.yes.callback(i)
        receipt=i.edit_original_response.call_args.kwargs
        assert receipt['content'].startswith('受付しました。') and receipt['view'] is None
        assert view not in client.operation_views
        assert not store.setting('discord')['menu']
        await client.activate_menu(200)
        i.message.edit.assert_not_awaited(); client.http.edit_message.assert_not_awaited()
        assert i.edit_original_response.await_count==1
        await client.close()
    asyncio.run(run())


def test_legacy_result_text_is_preserved_when_its_buttons_expire(tmp_path):
    async def run():
        store,client=setup(tmp_path); original='完了: 予約しました。公開URLと実行内容の記録。'
        client.http.get_message=AsyncMock(return_value={'content':original})
        captured=[]
        async def edit(channel,mid,params): captured.append(dict(params.payload))
        client.http.edit_message=edit
        await client.disable_menu(99)
        assert 'content' not in captured[0] and not captured[0].get('components')
        assert 'embeds' not in captured[0]  # Existing receipt embeds are retained by Discord.
        client.http.delete_message.assert_not_awaited()
        await client.close()
    asyncio.run(run())


def test_history_keeps_completed_job_and_article_name_after_article_deletion(tmp_path):
    store=Store(tmp_path); pid=store.create({'title':'履歴に残す記事名','body':'body'})
    jid=store.enqueue('save_note_draft',pid,request_key='manual-save')
    store.complete_job(jid,True,'保存と再読込を確認しました。')
    store.delete(pid)
    auto=store.enqueue('sync_articles',request_key='sync:automatic')
    store.complete_job(auto,True,'自動同期')
    history=store.operation_history()
    assert history['total']==1 and history['items'][0]['subject']=='履歴に残す記事名'
    assert history['items'][0]['result']['message']=='保存と再読込を確認しました。'


def test_operation_history_pages_and_home_do_not_remove_records_or_add_messages(tmp_path):
    async def run():
        store,client=setup(tmp_path)
        for n in range(7):
            jid=store.enqueue('sync_articles',request_key='manual-'+str(n)); store.complete_job(jid,True,'同期完了 '+str(n))
        i=interaction(); menu=Menu(client,root=True); await menu.history.callback(i)
        page=i.response.edit_message.call_args.kwargs['view']
        assert isinstance(page,OperationHistory) and page.total==7 and not page.next.disabled
        await page.next.callback(i)
        last=i.response.edit_message.call_args.kwargs['view']
        assert last.page==1 and not last.prev.disabled and last.next.disabled
        await last.back.callback(i)
        assert isinstance(i.response.edit_message.call_args.kwargs['view'],Menu)
        assert store.operation_history()['total']==7
        assert i.response.edit_message.await_count==3
        i.response.send_message.assert_not_awaited(); i.followup.send.assert_not_awaited()
        await client.close()
    asyncio.run(run())


def test_startup_cleans_old_menu_placeholders_but_preserves_record_messages(tmp_path):
    async def run():
        store,client=setup(tmp_path)
        def message(mid,content,buttons):
            return SimpleNamespace(id=mid,author=SimpleNamespace(id=55),content=content,
                components=[SimpleNamespace(children=buttons)] if buttons else [])
        old=message(80,'操作メニュー（終了）',[])
        disabled=message(81,'note管理',[SimpleNamespace(custom_id='note:new',disabled=True)])
        record=message(82,'完了: 実行結果',[])
        current=message(99,'note管理',[SimpleNamespace(custom_id='note:new',disabled=False)])
        async def history(**kw):
            for row in [current,record,disabled,old]: yield row
        fake=SimpleNamespace(store=store,user=SimpleNamespace(id=55),disable_menu=AsyncMock())
        await NoteClient.disable_older_menus(fake,SimpleNamespace(history=history))
        assert [args.args[0] for args in fake.disable_menu.await_args_list]==[81,80]
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('ready',[True,False])
def test_gui_bridge_requests_a_fresh_menu_without_enqueuing_an_article_action(tmp_path,monkeypatch,ready):
    from note_stock.commands import Commands
    store=Store(tmp_path); calls=[]
    async def send_menu(): return 200
    def dispatch(coroutine,loop):
        coroutine.close(); calls.append(loop)
        return SimpleNamespace(add_done_callback=lambda callback:None)
    monkeypatch.setattr(asyncio,'run_coroutine_threadsafe',dispatch)
    bot=SimpleNamespace(client=SimpleNamespace(is_ready=lambda:ready,send_menu=send_menu),loop=object())
    window=SimpleNamespace(discord_panel=SimpleNamespace(bot=bot))
    result=Commands(store,window).handle({'command':'discord','data':{'action':'menu'}})
    assert result['ok']==ready and bool(calls)==ready
    assert store.next_job() is None
