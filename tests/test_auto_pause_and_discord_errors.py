import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace

import discord
import pytest

from note_stock.discord_flow import flow
from note_stock.store import Store
from note_stock.worker import BrowserWorker
from test_discord_flow import setup,interaction


@pytest.mark.parametrize('action',['save_note_draft','sync_articles','preview_article'])
@pytest.mark.parametrize('paused',[False,True])
def test_unrelated_operation_error_preserves_automatic_posting_state(tmp_path,monkeypatch,action,paused):
    store=Store(tmp_path)
    store.set_setting('account_verified',True)
    store.set_setting('paused',paused)
    pid=store.create({'editor_kind':'legacy','title':'記事','body':'本文'})
    class Browser:
        def __init__(self,*args): self.context=SimpleNamespace(close=lambda:None)
        def open(self,*args): raise RuntimeError('この操作だけ失敗')
        def save_draft(self,*args): raise RuntimeError('この操作だけ失敗')
    monkeypatch.setattr('note_stock.worker.NoteBrowser',Browser)
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:nullcontext())
    monkeypatch.setattr('note_stock.headless_sync.open_note_session',lambda *args:nullcontext())
    worker=BrowserWorker(store,action,None if action=='sync_articles' else store.get(pid))
    outcomes=[]
    worker.outcome.connect(lambda status,*args:outcomes.append(status))
    worker.run()
    assert outcomes==['failed']
    assert Store(tmp_path).setting('paused') is paused
    assert store.setting('account_verified') is True


def test_failed_discord_result_screen_does_not_duplicate_result_or_pause(tmp_path):
    async def run():
        store,client,_=setup(tmp_path)
        store.set_setting('paused',False)
        store.set_setting('diagnostics',True)
        click=interaction()
        click.edit_original_response.side_effect=discord.HTTPException(
            SimpleNamespace(status=400,reason='Bad Request'),{'code':50035,'message':'Invalid form body'})
        await flow(client).finish(click,'下書き保存は成功',ok=True)
        notices=store.pending_notifications()
        assert len(notices)==1
        assert notices[0][1]['ok'] and notices[0][1]['message']=='下書き保存は成功'
        assert store.setting('paused') is False
        rows=[json.loads(line) for line in (store.root/'logs/diagnostics.jsonl').read_text(encoding='utf-8').splitlines()]
        assert rows[-1]['event']=='discord_result_screen_update_failed'
        assert rows[-1]['http_status']==400 and rows[-1]['discord_code']==50035
        await client.close()
    asyncio.run(run())
