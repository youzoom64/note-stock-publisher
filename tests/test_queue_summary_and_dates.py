import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from note_stock.qt import Qt

from note_stock.discord_bot import NoteClient, Articles, article_text
from note_stock.store import Store, JST
from note_stock.ui import MainWindow
from discord_test_support import article_buttons


def queued_batch(store, count=3):
    batch=store.begin_queue_batch()
    jobs=[]
    for index in range(count):
        pid=store.create({"editor_kind":"legacy","title":f"記事 {index}"})
        jobs.append(store.enqueue("save_note_draft",pid,{"queue":True,"draft_queue":True,"queue_batch":batch}))
        # Exercise notification aggregation with simulated unfinished results.
        with store.connect() as c:
            c.execute("UPDATE jobs SET state='waiting' WHERE id=?",(jobs[-1],))
    store.seal_queue_batch(batch,[])
    return jobs


def test_batch_sends_only_one_durable_summary_after_every_result(tmp_path):
    store=Store(tmp_path)
    jobs=queued_batch(store)
    store.complete_job(jobs[0],True,"追加")
    store.complete_job(jobs[1],False,"ログイン切れ")
    assert not store.pending_notifications()
    reopened=Store(tmp_path)
    reopened.complete_job(jobs[2],True,"追加")
    notifications=reopened.pending_notifications()
    assert len(notifications)==1
    nid,body=notifications[0]
    assert nid.startswith("queue-batch:")
    assert "成功 2件 / 失敗 1件" in body["message"]
    assert "記事 1: ログイン切れ" in body["message"]
    reopened.complete_job(jobs[2],True,"追加")
    assert len(reopened.pending_notifications())==1


def test_batch_result_and_menu_share_one_discord_message(tmp_path):
    async def run():
        store=Store(tmp_path)
        store.set_setting("discord",{"owner":"1","guild":"2","channel":"3"})
        jobs=queued_batch(store,2)
        for jid in jobs:
            store.complete_job(jid,True,"追加")
        client=NoteClient(store)
        client.is_ready=lambda:True
        client.get_channel=lambda _:SimpleNamespace(id=3)
        payloads=[]
        async def send(channel,params):
            payloads.append(dict(params.payload))
            return {"id":"101"}
        client.http.send_message=send
        client.activate_menu=AsyncMock()
        client.cleanup_receipts=AsyncMock()
        await client.notifications.coro(client)
        await client.notifications.coro(client)
        assert len(payloads)==1
        assert payloads[0]["embeds"] and payloads[0]["components"]
        client.activate_menu.assert_awaited_once_with("101")
        await client.close()
    asyncio.run(run())


def test_legacy_pending_bulk_jobs_are_grouped_on_restart(tmp_path):
    store=Store(tmp_path)
    jobs=[]
    for index in range(2):
        pid=store.create({"editor_kind":"legacy","title":str(index)})
        jobs.append(store.enqueue("save_note_draft",pid,{"queue":True,"draft_queue":True}))
        with store.connect() as c:
            c.execute("UPDATE jobs SET state='waiting' WHERE id=?",(jobs[-1],))
            c.execute('DELETE FROM notifications WHERE id=?',('job:'+jobs[-1],))
    store.complete_job(jobs[0],True,"追加")
    store.recover_jobs()
    assert not store.pending_notifications()
    store.complete_job(jobs[1],True,"追加")
    assert len(store.pending_notifications())==1
    assert "成功 2件" in store.pending_notifications()[0][1]["message"]


def test_queue_dates_are_shown_in_gui_and_discord(tmp_path,qt_application,monkeypatch):
    async def run():
        stamp=datetime(2026,12,1,8,0,tzinfo=JST).timestamp()
        monkeypatch.setattr("note_stock.store.now_ts",lambda:stamp)
        store=Store(tmp_path)
        store.set_setting("account","tester")
        store.set_setting("recurrence",{"days":list(range(7)),"time":"09:30"})
        ids=[]
        for index in range(2):
            pid=store.create({"editor_kind":"legacy","title":str(index),"body":"本文"})
            store.save(pid,str(index),"本文",[],"","queued")
            ids.append(pid)
        plan=store.queue_schedule()
        early=store.create({"editor_kind":"legacy","title":"日時指定","body":"本文"})
        store.save(early,"日時指定","本文",[],"","scheduled",
                   due=datetime(2026,12,1,8,30,tzinfo=JST).timestamp())
        assert datetime.fromtimestamp(plan[ids[0]],JST).strftime("%Y/%m/%d %H:%M")=="2026/12/01 09:30"
        assert datetime.fromtimestamp(plan[ids[1]],JST).strftime("%Y/%m/%d %H:%M")=="2026/12/02 09:30"
        client=NoteClient(store)
        listing=Articles(client,"scheduled")
        assert [post["id"] for post in listing.selected]==[early]+ids
        assert "投稿予定順" in listing.list_text
        labels=[button.label for button in article_buttons(listing.parts[0])]
        assert any("2026/12/01 09:30" in label for label in labels)
        assert "2026-12-02 09:30" in article_text(store,store.get(ids[1]))
        window=MainWindow(store,offline=True)
        try:
            window.show()
            window.filter_buttons["queued"].click()
            assert window.order_buttons["planned"].isChecked()
            assert [window.list.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(window.list.count())]==[early]+ids
            window.load(ids[0])
            qt_application.processEvents()
            assert "2026/12/01 09:30" in window.planned_time_label.text()
            assert any("2026/12/02 09:30" in window.list.item(i).text() for i in range(window.list.count()))
        finally:
            window.quitting=True
            window.close()
            await client.close()
    asyncio.run(run())
