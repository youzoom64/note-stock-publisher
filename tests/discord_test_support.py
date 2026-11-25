from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord

def article_buttons(view):
    return [item for item in view.walk_children() if isinstance(item,discord.ui.Button)]

_ids=count(10000)
def sent_followups():
    sent=[]
    async def send(**kwargs):
        message=SimpleNamespace(id=next(_ids),content=kwargs.get('content',''),embeds=[],
            flags=SimpleNamespace(ephemeral=kwargs.get('ephemeral',False)),edit=AsyncMock(),delete=AsyncMock())
        sent.append(message)
        return message
    mock=AsyncMock(side_effect=send);mock.messages=sent
    return mock
