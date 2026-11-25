import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
import pytest
from note_stock.store import Store
from note_stock.discord_bot import NoteClient,RECORD_FOOTER

def receipt(store,key='test',ok=True,token=''):
    jid=store.enqueue('sync_articles',request_key=key)
    store.record_receipt(jid,'99','3','5' if token else '',token)
    store.complete_job(jid,ok,'確定した実行結果')
    store.record_notification_message('job:'+jid,'result_id','101')
    store.record_notification_message('job:'+jid,'menu_id','102')
    return jid

@pytest.mark.parametrize('ok',[True,False])
def test_completed_receipt_is_removed_after_restart_and_never_touches_results(tmp_path,ok):
    async def run():
        store=Store(tmp_path);jid=receipt(store,ok=ok)
        store=Store(tmp_path);client=NoteClient(store)
        client.http.get_message=AsyncMock(return_value={'content':'note同期...','embeds':[]})
        client.http.delete_message=AsyncMock()
        await client.cleanup_receipts()
        client.http.delete_message.assert_awaited_once_with(3,99)
        assert not store.finished_receipts()
        assert store.job(jid)['result']['message']=='確定した実行結果'
        assert store.notification_message_ids('job:'+jid)=={'result_id':'101','menu_id':'102'}
        await client.close()
    asyncio.run(run())

def test_receipt_waits_until_both_result_and_new_menu_are_sent(tmp_path):
    store=Store(tmp_path);jid=store.enqueue('sync_articles')
    store.record_receipt(jid,99,3);store.complete_job(jid,True,'完了')
    assert not store.finished_receipts()
    store.record_notification_message('job:'+jid,'result_id',101)
    assert not store.finished_receipts()
    store.record_notification_message('job:'+jid,'menu_id',102)
    assert [r['job_id'] for r in store.finished_receipts()]==[jid]

@pytest.mark.parametrize('content,embeds',[('操作メニュー',[]),
    ('処理中: old',[{'footer':{'text':RECORD_FOOTER}}]),('完了: 実行記録',[])])
def test_cleanup_never_removes_a_final_record_or_reused_menu(tmp_path,content,embeds):
    async def run():
        store=Store(tmp_path);receipt(store);client=NoteClient(store)
        client.http.get_message=AsyncMock(return_value={'content':content,'embeds':embeds})
        client.http.delete_message=AsyncMock()
        await client.cleanup_receipts()
        client.http.delete_message.assert_not_awaited()
        assert not store.finished_receipts()
        await client.close()
    asyncio.run(run())

def test_private_receipt_uses_its_webhook_and_erases_the_encrypted_token(tmp_path,monkeypatch):
    async def run():
        store=Store(tmp_path);jid=receipt(store,token='encrypted-test-token');client=NoteClient(store)
        webhook=SimpleNamespace(fetch_message=AsyncMock(return_value=SimpleNamespace(content='処理中: note同期',embeds=[])),delete_message=AsyncMock())
        monkeypatch.setattr('note_stock.discord_bot.unprotect',lambda value:'temporary-test-token')
        monkeypatch.setattr(discord.Webhook,'partial',lambda *a,**k:webhook)
        await client.cleanup_receipts()
        webhook.delete_message.assert_awaited_once_with(99)
        with store.connect() as c:assert c.execute('SELECT webhook_token FROM job_receipts WHERE job_id=?',(jid,)).fetchone()[0]==''
        await client.close()
    asyncio.run(run())

def test_one_cleanup_failure_does_not_block_other_receipts_and_can_retry(tmp_path):
    async def run():
        store=Store(tmp_path);receipt(store,'first');receipt(store,'second');client=NoteClient(store)
        error=discord.HTTPException(SimpleNamespace(status=500,reason='test'),{'message':'test','code':0})
        client.http.get_message=AsyncMock(side_effect=[error,{'content':'受付しました。','embeds':[]}])
        client.http.delete_message=AsyncMock()
        await client.cleanup_receipts()
        assert len(store.finished_receipts())==1 and client.http.delete_message.await_count==1
        client.http.get_message=AsyncMock(return_value={'content':'受付しました。','embeds':[]})
        await client.cleanup_receipts()
        assert not store.finished_receipts() and client.http.delete_message.await_count==2
        await client.close()
    asyncio.run(run())
