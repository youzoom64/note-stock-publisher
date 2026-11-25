from discord_test_support import sent_followups,article_buttons
import asyncio
import json
from unittest.mock import AsyncMock
import pytest
from note_stock.store import Store,now_ts
from note_stock.remote import submit
from note_stock.discord_flow import flow
from note_stock.discord_bot import NoteClient,Menu,Articles,ArticleView,Confirm,authorized


@pytest.fixture(autouse=True)
def no_live_message_edits_in_unit_tests(monkeypatch):
    import discord.http
    monkeypatch.setattr(discord.http.HTTPClient,'edit_message',AsyncMock(return_value={}))
    monkeypatch.setattr(discord.http.HTTPClient,'get_message',AsyncMock(return_value={'content':'note管理'}))
    monkeypatch.setattr(discord.http.HTTPClient,'delete_message',AsyncMock())


def ui_interaction(number=1,message=99):
    from types import SimpleNamespace
    source=SimpleNamespace(id=message,edit=AsyncMock(),delete=AsyncMock()) if message is not None else None
    return SimpleNamespace(id=number,user=SimpleNamespace(id=1),guild_id=2,channel_id=3,
        message=source,extras={},original_response=AsyncMock(return_value=source),edit_original_response=AsyncMock(return_value=source),
        delete_original_response=AsyncMock(),
        response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock(),send_modal=AsyncMock(),defer=AsyncMock()),
        followup=SimpleNamespace(send=sent_followups()))


def configure_ui(store):
    store.set_setting('discord',{'owner':'1','guild':'2','channel':'3','menu':'99'})
    store.set_setting('account_verified',True)


def test_unread_article_disables_actions_and_never_claims_free_price(tmp_path):
    from note_stock.discord_bot import article_text
    async def run():
        store=Store(tmp_path); client=NoteClient(store)
        pid=store.create({'title':'a','body':'b'})
        view=ArticleView(client,store.get(pid))
        assert view.publish.disabled and view.schedule.disabled and view.tags.disabled
        assert not view.back.disabled and not view.refresh_article.disabled
        assert view.refresh_article.label=='本文・公開条件を読み込む'
        assert '価格: 未確認' in article_text(store,store.get(pid))
        assert '価格: 0円' not in article_text(store,store.get(pid))
        # Even an enabled screen from before a note-side change rechecks readiness.
        for control in (view.publish,view.schedule,view.tags):
            i=ui_interaction()
            await control.callback(i)
            i.response.send_modal.assert_not_awaited()
            i.response.send_message.assert_not_awaited()
            assert isinstance(i.response.edit_message.call_args.kwargs['view'],ArticleView)
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_only_rendered_latest_view_and_canonical_menu_accept_interactions(tmp_path):
    async def run():
        store=Store(tmp_path);configure_ui(store);client=NoteClient(store);i=ui_interaction()
        root=Menu(client,root=True);flow(client).install(root,i.message)
        assert await root.interaction_check(ui_interaction())
        old=Articles(client);flow(client).install(old,i.message)
        latest=Articles(client);flow(client).install(latest,i.message)
        assert not await old.interaction_check(ui_interaction())
        assert await latest.interaction_check(ui_interaction())
        assert not await root.interaction_check(ui_interaction())
        assert not root.is_finished()
        await client.close()
    asyncio.run(run())


def test_stale_confirmation_and_modal_cannot_enqueue_or_invalidate_latest(tmp_path):
    from note_stock.discord_bot import TagsModal
    async def run():
        store=Store(tmp_path);configure_ui(store);client=NoteClient(store)
        pid=store.create({'title':'a','body':'b'});old=Confirm(client,store.get(pid),'delete_note')
        flow(client).install(old,ui_interaction().message);modal=TagsModal(client,pid)
        latest=Articles(client);flow(client).install(latest,ui_interaction().message);session=latest.session
        for callback in [old.yes.callback,modal.on_submit]:
            i=ui_interaction();await callback(i)
            i.followup.send.assert_not_awaited();i.response.send_message.assert_not_awaited()
            i.edit_original_response.assert_not_awaited();i.message.delete.assert_not_awaited()
        assert store.setting('discord_active_ui')==session and store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_session_consumption_is_atomic_across_distinct_simultaneous_clicks(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store=Store(tmp_path); session='displayed-test-screen';store.set_setting('discord_active_ui',session)
    def click(key):
        try: return store.enqueue('sync_articles',request_key=key,ui_session=session)
        except ValueError: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(click,['click-1','click-2']))
    assert len([r for r in results if r])==1
    key='click-1' if results[0] else 'click-2'
    assert store.enqueue('sync_articles',request_key=key,ui_session=session)==next(r for r in results if r)
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1


def test_current_confirmation_queues_once_and_cancel_creates_one_result(tmp_path):
    async def run():
        store=Store(tmp_path); configure_ui(store); client=NoteClient(store)
        pid=store.create({'title':'a','body':'b'})
        confirm=Confirm(client,store.get(pid),'delete_note')
        flow(client).install(confirm,ui_interaction().message)
        await confirm.yes.callback(ui_interaction(1))
        await confirm.yes.callback(ui_interaction(2))
        with store.connect() as c:
            assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
        cancel=Confirm(client,store.get(pid),'delete_note',('draft',0,'a'))
        i=ui_interaction(3); await cancel.no.callback(i)
        assert i.edit_original_response.call_args.kwargs['view'] is None
        records=store.pending_notifications()
        assert len(records)==1 and records[0][1]['ok'] and '取り消し' in records[0][1]['message']
        with store.connect() as c: assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
        await client.close()
    asyncio.run(run())


def test_one_available_server_is_never_implicitly_selected(tmp_path):
    from types import SimpleNamespace
    store=Store(tmp_path)
    member=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True))
    guild=SimpleNamespace(id=123456789012345678,name='Only server',owner_id=42,fetch_member=AsyncMock(return_value=member))
    fake=SimpleNamespace(store=store,guilds=[guild],application_info=AsyncMock(return_value=SimpleNamespace(team=None,owner=SimpleNamespace(id=42),id=55)))
    asyncio.run(NoteClient.prepare_channel(fake))
    assert len(store.setting('discord_guilds'))==1
    assert not store.setting('discord').get('guild')
    assert '選択' in store.setting('discord_connection')


def test_reconnect_reuses_private_channel_without_rewriting_its_permissions(tmp_path):
    import discord
    from types import SimpleNamespace
    class Target:
        def __init__(self,id): self.id=id
    store=Store(tmp_path)
    store.set_setting('discord',{'guild':'123456789012345678','channel':'22','category':'21','menu':'23','installation':'test'})
    owner=Target(42); owner.guild_permissions=SimpleNamespace(manage_guild=True)
    role,bot=Target(1),Target(2)
    overwrites={role:discord.PermissionOverwrite(view_channel=False),
        owner:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),
        bot:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,embed_links=True,attach_files=True)}
    category=SimpleNamespace(id=21)
    channel=SimpleNamespace(id=22,category_id=21,overwrites=overwrites,edit=AsyncMock())
    guild=SimpleNamespace(id=123456789012345678,name='test',owner_id=42,fetch_member=AsyncMock(return_value=owner),
        get_channel=lambda id:{21:category,22:channel}.get(id),default_role=role,me=bot,create_category=AsyncMock(),create_text_channel=AsyncMock())
    fake=SimpleNamespace(store=store,guilds=[guild],get_guild=lambda id:guild,
        application_info=AsyncMock(return_value=SimpleNamespace(team=None,owner=owner,id=55)),
        tree=SimpleNamespace(copy_global_to=lambda **kw:None,sync=AsyncMock()))
    asyncio.run(NoteClient.prepare_channel(fake))
    assert store.setting('discord_connection')=='管理メニュー準備完了'
    channel.edit.assert_not_called()
    guild.create_category.assert_not_called()
    guild.create_text_channel.assert_not_called()


@pytest.mark.parametrize('old_marker_visible',[True,False])
def test_new_bot_uses_its_own_private_channel_in_existing_server(tmp_path,old_marker_visible):
    import discord
    from types import SimpleNamespace

    class Target:
        def __init__(self,id): self.id=id

    store=Store(tmp_path)
    store.set_setting('discord',{'guild':'123','channel':'22','category':'21','menu':'23','installation':'new'})
    owner=Target(42)
    owner.guild_permissions=SimpleNamespace(manage_guild=True)
    role,bot=Target(1),Target(55)
    inaccessible=discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),{'code':50001,'message':'Missing Access'})
    old=SimpleNamespace(id=22,topic='note-stock:44:old' if old_marker_visible else None,
                        category_id=21,overwrites={},edit=AsyncMock(side_effect=inaccessible))
    old_category=SimpleNamespace(id=21)
    category=SimpleNamespace(id=31)
    channel=SimpleNamespace(id=32,send=AsyncMock(return_value=SimpleNamespace(id=33)))
    async def empty_history(**kwargs):
        if False:
            yield None
    channel.history=empty_history
    guild=SimpleNamespace(id=123,name='test',owner_id=42,fetch_member=AsyncMock(return_value=owner),
        get_channel=lambda key:{21:old_category,22:old}.get(key),default_role=role,me=bot,text_channels=[old],
        create_category=AsyncMock(return_value=category),create_text_channel=AsyncMock(return_value=channel))
    fake=SimpleNamespace(store=store,guilds=[guild],get_guild=lambda _:guild,user=bot,
        application_info=AsyncMock(return_value=SimpleNamespace(team=None,owner=owner,id=55)),
        tree=SimpleNamespace(copy_global_to=lambda **kw:None,sync=AsyncMock()))
    asyncio.run(NoteClient.prepare_channel(fake))
    if old_marker_visible:
        old.edit.assert_not_called()
    else:
        old.edit.assert_awaited_once()
    permissions=guild.create_text_channel.await_args.kwargs['overwrites']
    assert permissions[role].view_channel is False and permissions[bot].view_channel is True
    assert store.setting('discord')['channel']=='32' and store.setting('discord')['menu']=='33'


def test_jobs_survive_restart_deduplicate_and_do_not_resend_running(tmp_path):
    store=Store(tmp_path)
    jid=store.enqueue('sync_articles',request_key='request-1')
    assert store.enqueue('sync_articles',request_key='request-1')==jid
    assert store.next_job()['id']==jid
    assert store.next_job() is None
    again=Store(tmp_path)
    again.recover_jobs()
    assert again.job(jid)['state']=='failed'
    assert again.next_job() is None
    notifications=again.pending_notifications()
    assert len(notifications)==1
    again.delivered(notifications[0][0])
    assert not Store(tmp_path).pending_notifications()


def test_replayed_new_draft_has_one_recovery_buffer(tmp_path):
    store=Store(tmp_path)
    first=store.incoming_draft('same-modal',{'title':'one','body':'body'})
    assert store.incoming_draft('same-modal',{'title':'replay','body':'body'})==first
    assert len(store.posts())==1 and store.get(first)['title']=='one'


def test_unchanged_catalog_sync_does_not_invalidate_confirmation(tmp_path):
    store=Store(tmp_path)
    store.set_setting('account','tester')
    row={'key':'n123456789abc','title':'one','status':'draft','updated':'a'}
    pid=store.merge_catalog([row],True)[0]['id']
    version=store.get(pid)['updated']
    store.merge_catalog([row],True)
    assert store.get(pid)['updated']==version


def test_get_does_not_enqueue_a_save_and_legacy_delete_never_deletes_note(tmp_path):
    from note_stock.commands import Commands
    store=Store(tmp_path)
    store.set_setting('account_verified',True)
    pid=store.create({'title':'title','body':'body'})
    store.checkpoint(pid,key='n123456789abc')
    assert Commands(store).handle({'command':'get','id':pid})['ok']
    assert store.next_job() is None
    assert not Commands(store).handle({'command':'delete','id':pid})['ok']
    assert store.get(pid)['draft_key']


def test_already_published_remote_draft_cannot_be_sent_again(tmp_path):
    from note_stock.native import NativeNoteBrowser
    from note_stock.publisher import VerificationFailed
    from types import SimpleNamespace
    store=Store(tmp_path)
    store.set_setting('account','tester')
    pid=store.merge_catalog([{'key':'n123456789abc','title':'title','status':'draft','updated':'a'}],True)[0]['id']
    snapshot={'title':'title','text':'body','document':{'kind':'html','value':'body'},'fingerprint':'x'}
    store.capture_native(pid,{'key':'n123456789abc','account':'tester','url':'https://note.com/api/v1/text_notes/1','method':'PUT',
                             'payload':{'status':'published','name':'title'}},snapshot)
    post=store.get(pid)
    adapter=NativeNoteBrowser(store)
    adapter.context=SimpleNamespace(new_page=lambda:None)
    adapter.sync_articles=lambda:store.merge_catalog([{'key':'n123456789abc','title':'title','status':'published','updated':'b'}],True)
    with pytest.raises(VerificationFailed,match='すでに公開'):
        adapter.publish(post)


def test_catalog_partial_sync_keeps_other_records_and_reimport_identity(tmp_path):
    store=Store(tmp_path)
    store.set_setting('account','tester')
    rows=[{'key':'n123456789abc','title':'draft','status':'draft','updated':'a'},
          {'key':'n123456789abd','title':'public','status':'published','updated':'a'}]
    results=store.merge_catalog(rows,True)
    pid=results[0]['id']
    assert store.get(results[1]['id'])['status']=='published'
    assert store.merge_catalog(rows[:1],False)[0]['id']==pid
    assert len(store.posts())==2
    assert not store.setting('catalog_sync')['complete']
    with store.connect() as c:
        c.execute("UPDATE posts SET status='scheduled',native_ready=1,due=? WHERE id=?",(now_ts()+600,pid))
    due=store.get(pid)['due']
    rows[0]['updated']='b'
    store.merge_catalog(rows[:1],True)
    assert store.get(pid)['status']=='review'
    assert store.get(pid)['due']==due


def test_confirmation_rejects_stale_revision_and_requires_prepared_note(tmp_path):
    store=Store(tmp_path)
    store.set_setting('account_verified',True)
    pid=store.create({'title':'a','body':'b'})
    post=store.get(pid)
    with pytest.raises(ValueError): submit(store,'publish_note',pid,{'confirmed_updated':post['updated']-1})
    with pytest.raises(ValueError): submit(store,'publish_note',pid,{'confirmed_updated':post['updated']})
    with pytest.raises(ValueError): submit(store,'schedule',pid,{'due':float('nan')})
    jid=submit(store,'delete_note',pid,{'confirmed_updated':post['updated']},'same-click')
    assert submit(store,'delete_note',pid,{'confirmed_updated':post['updated']},'same-click')==jid


def test_authorization_checks_user_guild_and_channel_independently(tmp_path):
    store=Store(tmp_path)
    assert not authorized(store,1,2,3)
    store.set_setting('discord',{'owner':'1','guild':'2','channel':'3'})
    assert authorized(store,1,2,3)
    assert not authorized(store,9,2,3)
    assert not authorized(store,1,9,3)
    assert not authorized(store,1,2,9)
    assert not authorized(store,1,None,None)


def test_views_construct_persistently_and_page_long_catalog(tmp_path):
    async def run():
        store=Store(tmp_path)
        for n in range(42): store.create({'title':'記事'+str(n),'body':'test'})
        client=NoteClient(store)
        assert not client.intents.message_content
        assert Menu(client,root=True).is_persistent()
        assert not Menu(client).is_persistent()
        assert Articles(client).total==42
        assert Articles(client,page=4).next.disabled
        post=store.posts()[0]
        ArticleView(client,post)
        Confirm(client,post,'delete_note')
        from note_stock.discord_commands import command_names
        assert {c.name for c in client.tree.get_commands()}==set(command_names().values())
        await client.close()
    asyncio.run(run())


def test_article_back_preserves_search_page_and_does_not_change_article_or_queue(tmp_path):
    from types import SimpleNamespace
    async def run():
        store=Store(tmp_path)
        for n in range(42): store.create({'title':'対象 '+str(n),'body':'test'})
        store.create({'title':'対象外の別記事','body':'other'})
        client=NoteClient(store)
        listing=Articles(client,'all',1,'対象 ')
        original_options=[item['id'] for item in listing.selected]
        pid=original_options[0]; before=store.get(pid)
        flow(client).install(listing,ui_interaction().message)
        row=listing.parts[0]
        configure_ui(store)
        interaction=ui_interaction()
        await article_buttons(row)[0].callback(interaction)
        detail=interaction.response.edit_message.call_args.kwargs['view']
        assert isinstance(detail,ArticleView) and detail.listing==('all',1,'対象 ',30)
        assert listing.is_finished()
        interaction.response.edit_message.reset_mock()
        await detail.back.callback(interaction)
        restored=interaction.response.edit_message.call_args.kwargs['view']
        assert (restored.kind,restored.page,restored.query)==('all',1,'対象 ')
        assert [item['id'] for item in restored.selected]==original_options
        assert detail.is_finished() and store.get(pid)==before
        interaction.response.send_message.assert_not_awaited()
        with store.connect() as connection:
            assert connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
        await client.close()
    asyncio.run(run())


def test_list_back_returns_to_management_menu_without_running_any_action(tmp_path):
    from types import SimpleNamespace
    async def run():
        store=Store(tmp_path); client=NoteClient(store)
        listing=Articles(client)
        interaction=SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await listing.back.callback(interaction)
        result=interaction.response.edit_message.call_args.kwargs
        assert result['content']=='note管理' and isinstance(result['view'],Menu)
        assert listing.is_finished()
        with store.connect() as connection:
            assert connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
        await client.close()
    asyncio.run(run())


def test_notification_outbox_uses_supported_transport_and_dedup_nonce(tmp_path):
    async def run():
        store=Store(tmp_path)
        store.set_setting('discord',{'channel':'123'})
        jid=store.enqueue('sync_articles')
        store.complete_job(jid,True,'同期完了')
        client=NoteClient(store)
        client.is_ready=lambda:True
        channel=type('Channel',(),{'id':123})()
        client.get_channel=lambda _:channel
        captured=[]
        async def send(cid,params):
            captured.append((cid,dict(params.payload)))
            return {'id':str(150+len(captured))}
        client.http.send_message=send
        await client.notifications.coro(client)
        assert len(captured)==2 and all(item[1]['enforce_nonce'] is True for item in captured)
        assert not captured[0][1].get('components')
        assert not captured[0][1].get('content')
        assert captured[0][1]['embeds'][0]['description']=='同期完了'
        assert not captured[0][1].get('message_reference')
        assert captured[1][1]['message_reference']=={
            'message_id':'151','channel_id':'123','fail_if_not_exists':False}
        assert captured[1][1]['allowed_mentions'].get('replied_user') is False
        buttons=[button for row in captured[1][1]['components'] for button in row['components']]
        assert {button['label'] for button in buttons}>={'記事一覧','新規下書き','予約一覧','接続状態'}
        assert store.setting('discord')['menu']=='152'
        assert not store.pending_notifications()
        await client.notifications.coro(client)
        assert len(captured)==2
        await client.close()
    asyncio.run(run())


def test_failure_notification_also_has_menu_and_only_latest_menu_is_active(tmp_path):
    async def run():
        store=Store(tmp_path); configure_ui(store); client=NoteClient(store)
        old=Articles(client)
        store.notification(True,'保存完了'); store.notification(False,'接続要確認')
        client.is_ready=lambda:True
        client.get_channel=lambda _:type('Channel',(),{'id':3})()
        sent=[]
        async def send(cid,params):
            sent.append(dict(params.payload)); return {'id':str(150+len(sent))}
        client.http.send_message=send
        await client.notifications.coro(client)
        assert len(sent)==4 and all(sent[n].get('components') for n in (1,3))
        assert not sent[0].get('components') and not sent[2].get('components')
        assert sent[0]['embeds'][0]['title'].startswith('完了:') and sent[2]['embeds'][0]['title'].startswith('要確認:')
        assert not sent[0].get('content') and not sent[2].get('content')
        assert sent[1]['message_reference']['message_id']=='151'
        assert sent[3]['message_reference']['message_id']=='153'
        assert store.setting('discord')['menu']=='154'
        assert not await old.interaction_check(ui_interaction())
        persistent=Menu(client,root=True)
        assert not await persistent.interaction_check(ui_interaction(message=152))
        assert not persistent.is_finished()
        assert await persistent.interaction_check(ui_interaction(message=154))
        assert not store.pending_notifications()
        await client.close()
    asyncio.run(run())


def test_previous_screen_expires_before_any_stale_button_click(tmp_path):
    from types import SimpleNamespace
    async def run():
        store=Store(tmp_path); configure_ui(store); client=NoteClient(store)
        old=Articles(client); old.message=SimpleNamespace(id=100,edit=AsyncMock(),delete=AsyncMock()); flow(client).install(old,old.message)
        current=Articles(client); current.message=SimpleNamespace(id=101,edit=AsyncMock(),delete=AsyncMock()); flow(client).install(current,current.message)
        await client.retire_views(current)
        old.message.delete.assert_awaited_once()
        old.message.edit.assert_not_awaited()
        assert old.is_finished() and all(item.disabled for item in old.children)
        assert not store.setting('discord')['menu']
        assert current.message.edit.await_count==0
        await client.close()
    asyncio.run(run())


def test_replacing_same_message_never_overwrites_new_controls_with_expired_screen(tmp_path):
    from types import SimpleNamespace
    async def run():
        store=Store(tmp_path); client=NoteClient(store)
        old=Articles(client); old.message=SimpleNamespace(id=100,edit=AsyncMock(),delete=AsyncMock()); flow(client).install(old,old.message)
        current=Menu(client); current.message=SimpleNamespace(id=100,edit=AsyncMock(),delete=AsyncMock()); flow(client).install(current,current.message)
        await client.retire_views(current)
        old.message.edit.assert_not_awaited()
        old.message.delete.assert_not_awaited()
        assert old.is_finished() and all(not item.disabled for item in current.children)
        await client.close()
    asyncio.run(run())


def test_late_retirement_never_deletes_newer_view_on_same_message(tmp_path):
    from types import SimpleNamespace
    async def run():
        store=Store(tmp_path); client=NoteClient(store)
        old=Articles(client); old.message=SimpleNamespace(id=100,edit=AsyncMock(),delete=AsyncMock()); flow(client).install(old,old.message)
        current=Menu(client); current.message=old.message; flow(client).install(current,current.message)
        await client.retire_views(old)
        old.message.delete.assert_not_awaited()
        old.message.edit.assert_not_awaited()
        assert old.is_finished() and not current.is_finished()
        assert current in client.operation_views
        await client.close()
    asyncio.run(run())


def test_expired_private_menu_removes_buttons_without_waiting_for_a_click(tmp_path):
    from types import SimpleNamespace
    async def run():
        store=Store(tmp_path); client=NoteClient(store)
        menu=Menu(client); menu.message=SimpleNamespace(id=100,edit=AsyncMock(),delete=AsyncMock()); flow(client).install(menu,menu.message)
        client.send_menu=AsyncMock(side_effect=lambda:store.set_setting("discord_active_ui","new-menu"))
        assert menu.timeout==600
        await menu.on_timeout()
        menu.message.delete.assert_awaited_once()
        menu.message.edit.assert_not_awaited()
        client.send_menu.assert_awaited_once()
        assert menu.session!=store.setting('discord_active_ui')
        assert menu not in client.operation_views
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_any_owner_message_returns_menu_without_reading_content_or_enqueuing(tmp_path):
    from types import SimpleNamespace
    async def run():
        store=Store(tmp_path); configure_ui(store); client=NoteClient(store)
        old=Articles(client)
        class Message:
            author=SimpleNamespace(id=1,bot=False)
            guild=SimpleNamespace(id=2)
            channel=SimpleNamespace(id=3)
            reply=AsyncMock(return_value=SimpleNamespace(id=150))
            @property
            def content(self): raise AssertionError('Message content must not be read')
        message=Message()
        channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=150)))
        client.get_channel=lambda _:channel
        await client.on_message(message)
        assert client.intents.guild_messages and not client.intents.message_content
        assert isinstance(channel.send.call_args.kwargs['view'],Menu)
        assert store.setting('discord')['menu']=='150'
        assert not await old.interaction_check(ui_interaction())
        channel.send.reset_mock(); message.author=SimpleNamespace(id=1,bot=True)
        await client.on_message(message); channel.send.assert_not_awaited()
        message.author=SimpleNamespace(id=9,bot=False)
        await client.on_message(message); channel.send.assert_not_awaited()
        message.author=SimpleNamespace(id=1,bot=False); message.channel=SimpleNamespace(id=4)
        await client.on_message(message); channel.send.assert_not_awaited()
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_renamed_slash_commands_use_guards_and_publication_confirmation(tmp_path):
    async def run():
        store=Store(tmp_path); configure_ui(store)
        store.set_setting('discord_commands',{'publish':'投稿する','sync':'同期する'})
        client=NoteClient(store)
        assert client.tree.get_command('note_publish') is None
        assert client.tree.get_command('投稿する') is not None
        pid=store.create({'title':'sample','body':'body'})
        i=ui_interaction(message=None)
        await client.tree.get_command('投稿する').callback(i,pid)
        assert isinstance(i.response.send_message.call_args.kwargs['view'],ArticleView)
        with store.connect() as c: c.execute('UPDATE posts SET native_ready=1 WHERE id=?',(pid,))
        i=ui_interaction(2,message=None)
        await client.tree.get_command('投稿する').callback(i,pid)
        assert isinstance(i.response.send_message.call_args.kwargs['view'],Confirm)
        assert store.next_job() is None
        unauthorized=ui_interaction(3); unauthorized.user.id=9
        assert await client.article_choices(unauthorized,'')==[]
        await client.tree.get_command('同期する').callback(unauthorized)
        assert store.next_job() is None
        i=ui_interaction(4); await client.tree.get_command('同期する').callback(i)
        assert store.next_job()['action']=='sync_articles'
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('superseded',[False,True])
def test_new_command_cannot_save_after_a_new_menu_supersedes_it(tmp_path,superseded):
    async def run():
        store=Store(tmp_path); configure_ui(store); client=NoteClient(store)
        i=ui_interaction(message=None)
        if superseded: i.response.defer.side_effect=lambda **kw:store.set_setting('discord_active_ui','superseded')
        await client.tree.get_command('note_new').callback(i,title='test',body='body')
        assert len(store.posts())==(0 if superseded else 1)
        job=store.next_job()
        assert job is None if superseded else job['action']=='save_note_draft'
        await client.close()
    asyncio.run(run())


def test_menu_article_back_round_trip_only_edits_one_message(tmp_path):
    async def run():
        store=Store(tmp_path); configure_ui(store); client=NoteClient(store)
        pid=store.create({'title':'sample','body':'body'})
        interaction=ui_interaction()
        menu=Menu(client,root=True)
        await menu.articles.callback(interaction)
        listing=interaction.response.edit_message.call_args.kwargs['view']
        flow(client).install(listing,ui_interaction().message)
        await article_buttons(listing.parts[0])[0].callback(interaction)
        detail=interaction.response.edit_message.call_args.kwargs['view']
        await detail.back.callback(interaction)
        restored=interaction.response.edit_message.call_args.kwargs['view']
        assert isinstance(restored,Articles)
        await restored.back.callback(interaction)
        returned=interaction.response.edit_message.call_args.kwargs['view']
        assert isinstance(returned,Menu)
        await returned.articles.callback(interaction)
        assert interaction.response.edit_message.await_count==5
        interaction.response.send_message.assert_not_awaited()
        assert all(call.kwargs.get("view") is not None for call in interaction.followup.send.call_args_list)
        interaction.message.edit.assert_not_awaited()
        client.http.edit_message.assert_not_awaited()
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())
