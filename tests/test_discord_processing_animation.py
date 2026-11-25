import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from note_stock.store import Store
from note_stock.discord_bot import NoteClient,GuardView,Menu
from note_stock.discord_flow import flow,processing_frame


def test_animation_cycles_dots_and_stops_when_the_job_finishes(tmp_path):
    async def run():
        store=Store(tmp_path);client=NoteClient(store);ui=flow(client)
        jid=store.enqueue('sync_articles');frames=[]
        async def edit(**kwargs):
            frames.append(kwargs['content'])
            if len(frames)==3:store.complete_job(jid,True,'同期完了')
        message=SimpleNamespace(id=99,edit=AsyncMock(side_effect=edit))
        ui.install(GuardView(client),message)
        content='処理中: note同期\n受付番号: test'
        task=ui.animate_processing(jid,message,content,interval=0.001)
        await asyncio.wait_for(task,timeout=1)
        assert [frame.split('\n')[0] for frame in frames]==['note同期..','note同期...','note同期.']
        assert all(frame.endswith('受付番号: test') for frame in frames)
        assert len(store.pending_notifications())==1
        await client.close()
    asyncio.run(run())


def test_new_screen_cancels_animation_before_it_can_edit_the_menu(tmp_path):
    async def run():
        store=Store(tmp_path);client=NoteClient(store);ui=flow(client)
        jid=store.enqueue('sync_articles');message=SimpleNamespace(id=99,edit=AsyncMock())
        ui.install(GuardView(client),message)
        task=ui.animate_processing(jid,message,'処理中: note同期',interval=0.01)
        ui.install(Menu(client),message)
        await asyncio.gather(task,return_exceptions=True)
        message.edit.assert_not_awaited()
        assert store.job(jid)['state']=='waiting' and not store.pending_notifications()
        await client.close()
    asyncio.run(run())
