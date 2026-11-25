import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from note_stock.remote import request_selected_article,RemoteController
from note_stock.ui import MainWindow
from note_stock.discord_bot import display_article,show_view,Menu,article_text
from test_discord_flow import setup,interaction


def catalog(store):
    store.set_setting('account','tester')
    return [p['id'] for p in store.merge_catalog([
        {'key':'n123456789abc','title':'selected','status':'draft','owner':'tester'},
        {'key':'n23456789abcd','title':'unselected','status':'draft','owner':'tester'}])]


def test_article_displays_saved_failure_reason(tmp_path):
    store,client,pid=setup(tmp_path)
    reason='note画面の「投稿」を確認できません。'
    store.finish(pid,'failed',reason)
    text=article_text(store,store.get(pid))
    assert '管理: 失敗' in text
    assert '理由: '+reason in text


def test_gui_click_reads_only_clicked_article_and_coalesces_repeated_clicks(tmp_path):
    store,client,_=setup(tmp_path)
    selected,other=catalog(store)
    window=SimpleNamespace(loading=False,current_id=selected,store=store,statusBar=Mock(),fail=Mock(),refresh_list=Mock())
    MainWindow.request_selected_article(window)
    MainWindow.request_selected_article(window)
    job=store.next_job()
    assert job['post_id']==selected and job['data']['selection_read']
    assert job['data']['background_read']
    assert job['action']=='refresh_article'
    assert store.operation_history()['total']==1
    assert not store.get(other)['native_ready']


def test_selected_read_can_run_while_user_enters_reservation_time(tmp_path):
    store,client,_=setup(tmp_path)
    selected,_=catalog(store)
    request_selected_article(store,selected)
    job=store.next_job(editing_locally=True)
    assert job['post_id']==selected and job['data']['selection_read']


def test_discord_selection_reads_only_selected_article(tmp_path):
    async def run():
        store,client,_=setup(tmp_path)
        selected,other=catalog(store)
        opened=interaction()
        reading=asyncio.create_task(display_article(opened,client,selected,read_selected=True))
        await asyncio.sleep(0)
        job=store.next_job()
        assert job['post_id']==selected and job['data']['selection_read']
        body='取得済み本文。'*180
        store.capture_native(selected,{'account':'tester','payload':{}},
                             {'title':'selected','text':body,'document':{},'fingerprint':'test'})
        store.complete_job(job['id'],True,'取得完了',notify=False)
        await asyncio.wait_for(reading,3)
        edited=opened.message.edit.call_args.kwargs
        assert '取得済み本文。' in edited['content']
        assert edited['attachments'][0].fp.read().decode('utf-8')==body
        assert store.operation_history()['total']==1
        assert not store.get(other)['native_ready']
        await client.close()
    asyncio.run(run())


def test_completed_read_does_not_replace_a_newer_discord_screen(tmp_path):
    async def run():
        store,client,_=setup(tmp_path)
        selected,_=catalog(store)
        opened=interaction()
        reading=asyncio.create_task(display_article(opened,client,selected,read_selected=True))
        await asyncio.sleep(0)
        job=store.next_job()
        await show_view(interaction(), 'edit_message', 'note管理', view=Menu(client))
        store.complete_job(job['id'],True,'取得完了',notify=False)
        await asyncio.wait_for(reading,3)
        opened.message.edit.assert_not_awaited()
        await client.close()
    asyncio.run(run())


def test_selected_article_animates_loading_dots_until_body_is_ready(tmp_path):
    async def run():
        store,client,_=setup(tmp_path)
        selected,_=catalog(store)
        client.loading_emoji='<a:note_loading:123456789012345678>'
        opened=interaction()
        reading=asyncio.create_task(display_article(opened,client,selected,read_selected=True))
        await asyncio.sleep(1.15)
        loading=opened.response.edit_message.call_args.kwargs
        assert loading['content'].startswith('<a:note_loading:123456789012345678> 本文取得中')
        assert loading['attachments']==[]
        assert loading['embeds']==[]
        opened.message.edit.assert_not_awaited()
        job=store.next_job()
        store.complete_job(job['id'],True,'取得完了',notify=False)
        await asyncio.wait_for(reading,3)
        assert '本文取得中' not in opened.message.edit.call_args.kwargs['content']
        assert opened.message.edit.call_args.kwargs['embeds']==[]
        assert opened.message.edit.await_count==1
        assert store.operation_history()['total']==1
        await client.close()
    asyncio.run(run())


def test_already_loaded_article_does_not_queue_another_read(tmp_path):
    store,client,_=setup(tmp_path)
    selected,_=catalog(store)
    with store.connect() as c:
        c.execute('UPDATE posts SET native_ready=1 WHERE id=?',(selected,))
    assert request_selected_article(store,selected) is None
    assert store.next_job() is None


def test_selection_read_result_stays_in_article_screen_instead_of_notifying_discord(tmp_path):
    store,client,_=setup(tmp_path)
    selected,_=catalog(store)
    job_id=request_selected_article(store,selected)
    window=SimpleNamespace(current_id=None,note_action_required=Mock(),notify=Mock(),refresh=Mock())
    controller=SimpleNamespace(store=store,window=window,current=store.next_job(),background_sync=False)
    RemoteController.finish(controller,'saved','本文取得済み','')
    assert store.job(job_id)['state']=='done'
    assert not store.pending_notifications()
