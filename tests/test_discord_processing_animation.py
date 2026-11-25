import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from note_stock.discord_flow import flow
from note_stock.loading_indicator import body_loading_text, loading_text
from test_discord_flow import setup, interaction

EMOJI = "<a:note_loading:123456789012345678>"

def test_all_progress_uses_inline_application_emoji(tmp_path):
    async def run():
        store,client,pid=setup(tmp_path)
        client.loading_emoji=EMOJI
        ui=flow(client)
        for text in ("処理中: 投稿・更新", "note同期", "再ログインの待機を中止しています…"):
            i=interaction()
            await ui.progress(i,text)
            assert i.edit_original_response.call_args.kwargs["content"]==EMOJI+" "+text
        assert body_loading_text(client)==EMOJI+" 本文取得中"
        assert loading_text(client,EMOJI+" 処理中")==EMOJI+" 処理中"
        await client.close()
    asyncio.run(run())

def test_deferred_wait_uses_emoji_for_commands_and_buttons(tmp_path):
    async def run():
        store,client,pid=setup(tmp_path)
        client.loading_emoji=EMOJI
        for mid in (None,99):
            i=interaction(mid)
            await flow(client).defer(i,thinking=mid is None)
            assert i.edit_original_response.call_args.kwargs["content"]==EMOJI+" 処理中"
        await client.close()
    asyncio.run(run())

def test_stale_input_does_not_replace_current_screen_with_loading(tmp_path):
    async def run():
        store,client,pid=setup(tmp_path)
        i=interaction()
        i.extras["note_expected_ui"]="stale"
        await flow(client).obsolete(i)
        i.edit_original_response.assert_not_awaited()
        await client.close()
    asyncio.run(run())
