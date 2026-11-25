from discord_test_support import sent_followups,article_buttons
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
import pytest
from note_stock.discord_flow import flow
from note_stock.store import Store
from note_stock.discord_bot import NoteClient,Articles,ArticleSearchModal

def interaction():
    source=SimpleNamespace(id=99,edit=AsyncMock(),delete=AsyncMock(),content='',embeds=[])
    return SimpleNamespace(id=123,user=SimpleNamespace(id=1),guild_id=2,channel_id=3,message=source,
        response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock(),send_modal=AsyncMock()),
        original_response=AsyncMock(return_value=source),followup=SimpleNamespace(send=sent_followups()))

def fixture(tmp_path,count=123):
    store=Store(tmp_path);store.set_setting('discord',{'owner':'1','guild':'2','channel':'3'})
    for n in range(count):
        pid=store.create({'title':f'記事 {n:03d}','body':f'本文 keyword{n:03d}'})
        with store.connect() as c:c.execute('UPDATE posts SET created=? WHERE id=?',(1700000000+n,pid))
    return store,NoteClient(store)

def test_thirty_rows_are_newest_first_and_next_page_has_no_overlap(tmp_path):
    async def run():
        store,client=fixture(tmp_path)
        size=30;first=Articles(client);second=Articles(client,page=1)
        assert len(first.selected)==size and first.selected[0]['title']=='記事 122'
        assert not any(isinstance(item,discord.ui.Select) for item in first.children)
        assert not set(p['id'] for p in first.selected)&set(p['id'] for p in second.selected)
        assert not first.next.disabled and first.prev.disabled
        last=Articles(client,page=100,size=size)
        assert last.next.disabled and len(last.selected)==123%size
        assert '記事 122' in article_buttons(first.parts[0])[0].label
        assert sum(len(article_buttons(part)) for part in first.parts[:-1])==size
        await client.close()
    asyncio.run(run())

def test_search_covers_articles_outside_current_page_and_preserves_context(tmp_path):
    async def run():
        store,client=fixture(tmp_path)
        listing=Articles(client,page=2,size=30)
        modal=ArticleSearchModal(listing);modal.query._value='keyword000';i=interaction()
        flow(client).install(listing,i.message)
        await modal.on_submit(i)
        found=i.response.edit_message.call_args.kwargs['view']
        assert found.total==1 and found.selected[0]['title']=='記事 000' and found.size==30 and found.page==0
        assert '検索: keyword000' in found.list_text
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())

def test_thirty_long_emoji_titles_are_clickable_and_fit_button_limits(tmp_path):
    async def run():
        store,client=fixture(tmp_path,30)
        with store.connect() as c:c.execute('UPDATE posts SET title=?',('長い題名😀'*30,))
        listing=Articles(client)
        buttons=[button for part in listing.parts[:-1] for button in article_buttons(part)]
        assert len(buttons)==30 and len({b.custom_id for b in buttons})==30
        assert all(len(b.label.encode('utf-16-le'))//2<=80 for b in buttons)
        assert [len(article_buttons(part)) for part in listing.parts[:-1]]==[15,15]
        for part in listing.parts[:-1]:
            assert isinstance(part,discord.ui.LayoutView) and part.total_children_count<=40
            assert all(len(row.children)==1 for row in part.children if isinstance(row,discord.ui.ActionRow))
        assert len(listing.children)<=25
        await client.close()
    asyncio.run(run())


def test_old_fifty_setting_defaults_to_thirty_and_exposes_ten_and_thirty(tmp_path):
    async def run():
        store,client=fixture(tmp_path)
        store.set_setting('discord_article_page_size',50)
        listing=Articles(client,page=1,query='記事 ',size=50);i=interaction()
        assert listing.size==30 and len(listing.selected)==30
        labels={button.label for button in listing.children}
        assert {'10件','30件'}<=labels and '50件' not in labels
        await listing.next.callback(i)
        changed=i.response.edit_message.call_args.kwargs['view']
        assert changed.page==2 and changed.query=='記事 ' and changed.size==30
        await changed.prev.callback(i)
        assert i.response.edit_message.call_args.kwargs['view'].page==1
        await client.close()
    asyncio.run(run())


def test_size_buttons_switch_visible_articles_and_keep_search_context(tmp_path):
    async def run():
        store,client=fixture(tmp_path)
        listing=Articles(client,page=1,query='記事 ',size=30);i=interaction()
        await listing.size10.callback(i)
        ten=i.response.edit_message.call_args.kwargs['view']
        assert ten.size==10 and ten.page==3 and len(article_buttons(ten.parts[0]))==10
        assert ten.query=='記事 ' and Articles(client).size==10
        await ten.size30.callback(i)
        thirty=i.response.edit_message.call_args.kwargs['view']
        assert thirty.size==30 and thirty.page==1
        assert sum(len(article_buttons(part)) for part in thirty.parts[:-1])==30
        assert thirty.query=='記事 ' and Articles(client).size==30
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_every_article_title_opens_its_own_article_and_keeps_listing_context(tmp_path):
    async def run():
        store,client=fixture(tmp_path,123);listing=Articles(client,page=1);i=interaction()
        for index,button in enumerate(b for part in listing.parts[:-1] for b in article_buttons(part)):
            flow(client).install(listing,i.message);i.extras={}
            await button.callback(i)
            detail=i.response.edit_message.call_args.kwargs['view']
            assert detail.pid==listing.selected[index]['id'] and detail.listing==listing.context
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())


def test_recent_publication_is_first_and_numbers_do_not_become_markdown_lists(tmp_path):
    async def run():
        store,client=fixture(tmp_path,2)
        old=store.posts()[0]
        with store.connect() as c:c.execute("UPDATE posts SET status='published' WHERE id=?",(old['id'],))
        jid=store.enqueue('publish_note',old['id']);store.complete_job(jid,True,'公開済み')
        listing=Articles(client)
        assert listing.selected[0]['id']==old['id']
        assert old['title'] in article_buttons(listing.parts[0])[0].label
        published,draft=article_buttons(listing.parts[0])
        assert published.style==discord.ButtonStyle.success
        assert draft.style==discord.ButtonStyle.primary
        assert '記事の番号' not in listing.list_text
        await client.close()
    asyncio.run(run())
