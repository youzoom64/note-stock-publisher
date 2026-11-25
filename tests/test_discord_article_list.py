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

def test_fifteen_rows_are_newest_first_and_next_page_has_no_overlap(tmp_path):
    async def run():
        store,client=fixture(tmp_path)
        size=15;first=Articles(client);second=Articles(client,page=1)
        assert len(first.selected)==size and first.selected[0]['title']=='記事 122'
        assert not any(isinstance(item,discord.ui.Select) for item in first.children)
        assert not set(p['id'] for p in first.selected)&set(p['id'] for p in second.selected)
        assert not first.next.disabled and first.prev.disabled
        last=Articles(client,page=100)
        assert last.next.disabled and len(last.selected)==123%size
        assert '記事 122' in article_buttons(first.parts[0])[0].label
        assert sum(len(article_buttons(part)) for part in first.parts[:-1])==size
        await client.close()
    asyncio.run(run())


def test_only_captured_articles_have_the_document_prefix(tmp_path):
    async def run():
        store,client=fixture(tmp_path,2)
        captured=store.posts()[0]
        with store.connect() as c:
            c.execute('UPDATE posts SET native_ready=1 WHERE id=?',(captured['id'],))
        listing=Articles(client)
        buttons=article_buttons(listing.parts[0])
        assert sum(button.label.startswith('📃 ') for button in buttons)==1
        assert next(button for button in buttons if captured['title'] in button.label).label.startswith('📃 ')
        assert store.get(captured['id'])['title']==captured['title']
        from note_stock.discord_bot import article_text
        assert article_text(store,store.get(captured['id'])).startswith('📃 ')
        await client.close()
    asyncio.run(run())

def test_search_covers_articles_outside_current_page_and_preserves_context(tmp_path):
    async def run():
        store,client=fixture(tmp_path)
        listing=Articles(client,page=2)
        modal=ArticleSearchModal(listing);modal.query._value='keyword000';i=interaction()
        flow(client).install(listing,i.message)
        await modal.on_submit(i)
        found=i.response.edit_message.call_args.kwargs['view']
        assert found.total==1 and found.selected[0]['title']=='記事 000' and found.page==0
        assert '検索: keyword000' in found.list_text
        assert store.next_job() is None
        await client.close()
    asyncio.run(run())

def test_fifteen_long_emoji_titles_fit_in_one_message(tmp_path):
    async def run():
        store,client=fixture(tmp_path,30)
        with store.connect() as c:c.execute('UPDATE posts SET title=?',('長い題名😀'*30,))
        listing=Articles(client)
        buttons=[button for part in listing.parts[:-1] for button in article_buttons(part)]
        assert len(buttons)==15 and len({b.custom_id for b in buttons})==15
        assert all(len(b.label.encode('utf-16-le'))//2<=80 for b in buttons)
        assert [len(article_buttons(part)) for part in listing.parts[:-1]]==[15]
        for part in listing.parts[:-1]:
            assert isinstance(part,discord.ui.LayoutView) and part.total_children_count<=40
            assert all(len(row.children)==1 for row in part.children if isinstance(row,discord.ui.ActionRow))
        assert len(listing.children)<=25
        await client.close()
    asyncio.run(run())


def test_old_page_size_setting_is_ignored_and_navigation_keeps_search(tmp_path):
    async def run():
        store,client=fixture(tmp_path)
        store.set_setting('discord_article_page_size',50)
        listing=Articles(client,page=1,query='記事 ');i=interaction()
        assert len(listing.selected)==15
        labels={button.label for button in listing.children}
        assert not labels.intersection({'10件','15件','30件','50件'})
        await listing.next.callback(i)
        changed=i.response.edit_message.call_args.kwargs['view']
        assert changed.page==2 and changed.query=='記事 ' and len(changed.selected)==15
        await changed.prev.callback(i)
        assert i.response.edit_message.call_args.kwargs['view'].page==1
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
