from discord_test_support import sent_followups,article_buttons
"""Screen ownership contracts, including late input after returning home."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from note_stock.store import Store
from note_stock.discord_flow import flow,ManagedModal
from note_stock.discord_bot import (NoteClient,Menu,Articles,ArticleView,Confirm,
    TagsModal,NewModal,ArticleSearchModal,InfoView,OperationHistory,RecoveryView,show_view)

def interaction(mid=99):
    source=SimpleNamespace(id=mid,content='',embeds=[],edit=AsyncMock(),delete=AsyncMock()) if mid else None
    if source:source.edit.return_value=source
    return SimpleNamespace(id=123,user=SimpleNamespace(id=1),guild_id=2,channel_id=3,message=source,extras={},
        response=SimpleNamespace(is_done=lambda:False,defer=AsyncMock(),edit_message=AsyncMock(),send_message=AsyncMock(),send_modal=AsyncMock()),
        followup=SimpleNamespace(send=sent_followups()),edit_original_response=AsyncMock(return_value=source),
        original_response=AsyncMock(return_value=source),delete_original_response=AsyncMock())

def setup(tmp_path):
    store=Store(tmp_path);store.set_setting('discord',{'owner':'1','guild':'2','channel':'3'})
    store.set_setting('account_verified',True)
    pid=store.create({'title':'sample','body':'body'})
    client=NoteClient(store);client.disable_menu=AsyncMock()
    return store,client,pid

def form(client,pid,kind):
    listing=Articles(client)
    parent=Menu(client) if kind=='new' else ArticleView(client,client.store.get(pid)) if kind=='tags' else listing
    modal={'new':lambda:NewModal(client),'tags':lambda:TagsModal(client,pid),
        'search':lambda:ArticleSearchModal(listing)}[kind]()
    return parent,modal

@pytest.mark.parametrize('kind',['new','tags','search'])
def test_late_form_submission_after_home_never_sends_notice_or_changes_current_screen(tmp_path,kind):
    async def run():
        store,client,pid=setup(tmp_path);ui=flow(client);i=interaction()
        parent,modal=form(client,pid,kind);ui.install(parent,i.message)
        await ui.form(i,modal)
        home=Menu(client);back=interaction();await show_view(back,'edit_message','note管理',view=home)
        token=ui.current();before=store.posts();late=interaction()
        await modal.on_submit(late)
        late.response.defer.assert_awaited_once()
        late.response.send_message.assert_not_awaited();late.followup.send.assert_not_awaited()
        late.response.edit_message.assert_not_awaited();late.edit_original_response.assert_not_awaited()
        late.delete_original_response.assert_not_awaited();late.message.delete.assert_not_awaited()
        assert ui.current()==token and store.posts()==before and store.next_job() is None
        assert not store.pending_notifications()
        assert await home.interaction_check(interaction())
        await client.close()
    asyncio.run(run())

@pytest.mark.parametrize('kind',['new','tags','search'])
def test_native_form_cancel_keeps_parent_active_without_creating_a_new_screen(tmp_path,kind):
    async def run():
        _,client,pid=setup(tmp_path);parent,modal=form(client,pid,kind);i=interaction();ui=flow(client)
        ui.install(parent,i.message);token=ui.current();await ui.form(i,modal)
        # Discord sends no dismissal callback. The next click must work.
        assert ui.current()==token and await parent.interaction_check(interaction())
        i.response.edit_message.assert_not_awaited();i.followup.send.assert_not_awaited()
        await client.close()
    asyncio.run(run())

def test_constructing_unrendered_views_and_failed_delivery_cannot_expire_visible_menu(tmp_path):
    async def run():
        store,client,pid=setup(tmp_path);ui=flow(client);i=interaction();home=Menu(client);ui.install(home,i.message)
        token=ui.current();Articles(client);ArticleView(client,store.get(pid));Confirm(client,store.get(pid),'delete_note')
        assert ui.current()==token and client.operation_views=={home}
        broken=interaction();broken.response.edit_message.side_effect=RuntimeError('delivery failed')
        with pytest.raises(RuntimeError):await show_view(broken,'edit_message','list',view=Articles(client))
        assert ui.current()==token and await home.interaction_check(interaction())
        await client.close()
    asyncio.run(run())

def test_every_screen_rejects_old_buttons_without_erasing_new_screen_on_same_message(tmp_path):
    async def run():
        store,client,pid=setup(tmp_path);ui=flow(client);post=store.get(pid)
        for old in [Menu(client),Articles(client),ArticleView(client,post),Confirm(client,post,'delete_note'),
                    InfoView(client),OperationHistory(client),RecoveryView(client)]:
            ui.install(old,interaction().message);home=Menu(client);ui.install(home,interaction().message)
            for control in old.children:
                i=interaction()
                assert not await old.interaction_check(i)
                i.response.send_message.assert_not_awaited();i.followup.send.assert_not_awaited()
                i.message.delete.assert_not_awaited();i.delete_original_response.assert_not_awaited()
            assert await home.interaction_check(interaction())
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())

def test_form_routing_has_no_individual_response_or_obsolete_notice_fallback():
    source=Path('note_stock/discord_bot.py').read_text(encoding='utf-8')
    tree=ast.parse(source)
    assert 'modal_allowed' not in source and 'async def open_modal' not in source
    assert '最新の操作メニューからやり直してください' not in source
    for cls in [n for n in tree.body if isinstance(n,ast.ClassDef) and n.name.endswith('Modal')]:
        assert any(isinstance(base,ast.Name) and base.id=='ManagedModal' for base in cls.bases)
        assert not any(isinstance(n,ast.AsyncFunctionDef) and n.name=='on_submit' for n in cls.body)
    # Screen writers cannot be added to individual callbacks again.
    forbidden={'send_message','edit_message','send_modal','edit_original_response','followup'}
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in forbidden:
            # Bot-owned durable outbox and cleanup use self.http, never Interaction.
            assert isinstance(node.func.value,ast.Attribute) and node.func.value.attr=='http',ast.unparse(node)


def test_thirty_article_message_group_has_one_lifecycle_and_leaves_no_old_controls(tmp_path):
    async def run():
        store,client,pid=setup(tmp_path)
        for n in range(29):store.create({'title':'対象 '+str(n),'body':'body'})
        ui=flow(client);i=interaction();listing=Articles(client)
        await show_view(i,'edit_message','list',view=listing)
        assert len(ui.state()['message_ids'])==4 and ui.state()['phase']=='ready'
        assert len(client.operation_views)==4
        assert all(part.session==listing.session for part in listing.parts)
        for part in listing.parts:
            click=interaction();click.message=part.message
            assert await part.interaction_check(click)
        row=listing.parts[-2];click=interaction();click.message=row.message
        assert await row.interaction_check(click)
        await article_buttons(row)[-1].callback(click)
        detail=i.message.edit.call_args.kwargs['view']
        assert detail.pid==listing.selected[-1]['id']
        assert ui.state()['message_ids']==['99']
        assert client.operation_views=={detail}
        assert all(part.message.delete.await_count==1 for part in listing.parts)
        i.message.delete.assert_not_awaited()
        back=interaction();back.message=i.message
        await detail.back.callback(back)
        restored=back.response.edit_message.call_args.kwargs['view']
        footer=restored.parts[-1];home=interaction();home.message=footer.message
        assert await footer.interaction_check(home)
        await footer.back.callback(home)
        menu=back.message.edit.call_args.kwargs['view']
        assert isinstance(menu,Menu) and client.operation_views=={menu}
        assert all(part.message.delete.await_count==1 for part in restored.parts)
        assert store.next_job() is None and not store.pending_notifications()
        await client.close()
    asyncio.run(run())


def test_input_during_list_delivery_is_acknowledged_without_destroying_the_group(tmp_path):
    async def run():
        _,client,_=setup(tmp_path);ui=flow(client);listing=Articles(client);i=interaction()
        entered=asyncio.Event();release=asyncio.Event();send=i.followup.send.side_effect
        async def delayed(**kwargs):
            entered.set();await release.wait();return await send(**kwargs)
        i.followup.send.side_effect=delayed
        task=asyncio.create_task(show_view(i,'edit_message','list',view=listing))
        await asyncio.wait_for(entered.wait(),timeout=2)
        assert ui.state()['phase']=='building'
        click=interaction();assert not await listing.interaction_check(click)
        click.response.send_message.assert_not_awaited();click.message.delete.assert_not_awaited()
        release.set();await task
        assert ui.state()['phase']=='ready' and ui.current()==listing.session
        assert await listing.interaction_check(interaction())
        await client.close()
    asyncio.run(run())
