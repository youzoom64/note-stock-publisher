import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from note_stock.loading_indicator import prepare_loading_emoji, body_loading_text


def test_loading_emoji_reuses_the_application_owned_animation():
    emoji=SimpleNamespace(name='note_loading',animated=True,available=True)
    client=SimpleNamespace(fetch_application_emojis=AsyncMock(return_value=[emoji]),
                           create_application_emoji=AsyncMock())
    asyncio.run(prepare_loading_emoji(client))
    client.create_application_emoji.assert_not_awaited()
    assert body_loading_text(client)==str(emoji)+' 本文取得中'


def test_new_application_gets_an_animated_emoji_once():
    import discord
    from PIL import Image
    from io import BytesIO
    emoji=discord.PartialEmoji(name='note_loading',animated=True,id=123456789012345678)
    client=SimpleNamespace(fetch_application_emojis=AsyncMock(return_value=[]),
                           create_application_emoji=AsyncMock(return_value=emoji))
    asyncio.run(prepare_loading_emoji(client))
    image=Image.open(BytesIO(client.create_application_emoji.call_args.kwargs['image']))
    assert image.size==(128,128) and image.n_frames>1 and image.info['loop']==0
    assert body_loading_text(client)=='<a:note_loading:123456789012345678> 本文取得中'
