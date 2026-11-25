"""Discord Gateway client. Only bot tokens; no user-token API or public listener."""
import asyncio
import concurrent.futures
from datetime import datetime
import json
import logging
import threading
import uuid
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks
from .remote import submit
from .secrets import unprotect
from .store import JST, STATUSES, now_ts
from .discord_commands import command_names,command_help

EXPIRED_SCREEN='操作メニュー（終了）'
ACTION_NAMES={'sync_articles':'note同期','save_note_draft':'下書き保存','refresh_article':'本文・公開条件の読み込み',
    'schedule':'予約・日時変更','cancel_schedule':'予約取消','publish_note':'投稿・更新','delete_note':'記事削除',
    'tags':'タグ変更','pause':'自動投稿の停止','resume':'自動投稿の再開'}
RECORD_FOOTER='note操作記録'
MENU_TEXT='## 操作メニュー\n次の操作を選んでください。'


def is_execution_record(content='',embeds=()):
    if content.startswith(('完了:','要確認:','受付しました。')): return True
    for embed in embeds:
        data=embed.to_dict() if hasattr(embed,'to_dict') else embed
        if data.get('footer',{}).get('text')==RECORD_FOOTER: return True
    return False


async def remove_old_controls(message):
    try:
        if is_execution_record(getattr(message,'content',''),getattr(message,'embeds',[])):
            await message.edit(view=None)
        else:
            await message.delete()
    except discord.NotFound:
        pass


def operation_embed(store,nid,body):
    job=None
    if nid.startswith('job:'):
        try: job=store.job(nid[4:])
        except ValueError: pass
    action=ACTION_NAMES.get(job['action'],'記事操作') if job else '操作結果'
    embed=discord.Embed(title=('完了: ' if body['ok'] else '要確認: ')+action,
        description=(body['message']+('\n'+body['url'] if body.get('url') else ''))[:4000],
        timestamp=datetime.fromtimestamp(job['updated'] if job else now_ts(),JST))
    if job:
        subject=job['data'].get('_history_title','')
        if not subject and job.get('post_id'):
            try: subject=store.get(job['post_id'])['title']
            except ValueError: subject=job['post_id']
        if subject: embed.add_field(name='記事',value=subject[:250],inline=False)
        embed.add_field(name='受付番号',value=job['id'][:8])
    embed.set_footer(text=RECORD_FOOTER)
    return embed


def authorized(store,user,guild,channel):
    config=store.setting('discord',{})
    return bool(config.get('owner') and config.get('guild') and config.get('channel') and
                str(user)==str(config['owner']) and str(guild)==str(config['guild']) and str(channel)==str(config['channel']))


def new_ui_session(store):
    session=uuid.uuid4().hex
    store.set_setting('discord_active_ui',session)
    return session


def session_for(view,interaction):
    return getattr(interaction,'extras',{}).get('note_ui_session',view.session)


async def show_view(i,method,*args,**kwargs):
    view=kwargs.get('view')
    source=getattr(i,'message',None)
    editing=method=='edit_message'
    if view is not None and source is not None and method=='send_message':
        method='edit_message'; editing=True
        kwargs.pop('ephemeral',None)
    if method=='edit_message' and args:
        kwargs['content']=args[0]; args=args[1:]
    if view is not None and source is not None:
        old_content=getattr(source,'content','')
        if old_content.startswith(('完了:','要確認:')) and not any(
                getattr(getattr(embed,'footer',None),'text','')==RECORD_FOOTER for embed in getattr(source,'embeds',[])):
            legacy=discord.Embed(title='以前の操作記録',description=old_content[:4000])
            legacy.set_footer(text=RECORD_FOOTER)
            kwargs.setdefault('embeds',[legacy,*getattr(source,'embeds',[])][:10])
    if view is not None and source is not None and method=='followup':
        kwargs.pop('ephemeral',None); kwargs.pop('wait',None)
        if args: kwargs['content']=args[0]
        result=await i.edit_original_response(**kwargs)
        editing=True
    elif method=='followup':
        if view is not None: kwargs['wait']=True
        result=await i.followup.send(*args,**kwargs)
    else:
        result=await getattr(i.response,method)(*args,**kwargs)
    if view is not None:
        message=result if isinstance(result,discord.Message) else None
        if message is None and hasattr(i,'original_response'):
            try: message=await i.original_response()
            except discord.HTTPException: pass
        view.message=message
        view.message_id=getattr(source,'id',None) if editing else getattr(message,'id',None)
        await view.client.retire_views(view)
    return result


async def modal_allowed(client,interaction,session):
    if not authorized(client.store,interaction.user.id,interaction.guild_id,interaction.channel_id):
        await interaction.response.send_message('操作権限がありません。',ephemeral=True)
        return False
    if client.store.setting('discord_active_ui','')!=session:
        await interaction.response.send_message('最新の操作メニューからやり直してください。',ephemeral=True)
        return False
    return True


def article_text(store,post):
    remote=store.catalog_state(post['id']).get('remote_status','未保存')
    summary=post.get('native_summary',{})
    due=datetime.fromtimestamp(post['due'],JST).strftime('%Y-%m-%d %H:%M') if post.get('due') else 'なし'
    ready=bool(post.get('native_ready')) and post.get('status')!='review'
    price=str(summary['price'])+'円' if ready and 'price' in summary else '未確認'
    preparation='' if ready else '\n本文・公開条件は未確認です。先に「本文・公開条件を読み込む」を押してください。'
    return (f"{post['title'] or '無題'}\nnote: {remote} / 管理: {STATUSES.get(post['status'],post['status'])}"
            f"\n予約: {due}（日本時間） / 価格: {price}"
            f"\nタグ: {' '.join('#'+t for t in post['tags'])}{preparation}\n\n{post['body'][:900]}")[:1800]


async def display_article(i,client,pid,listing=('all',0,''),notice='',method='edit_message'):
    try:
        post=client.store.get(pid)
        text=article_text(client.store,post)
        view=ArticleView(client,post,listing)
    except ValueError:
        view=Articles(client,*listing)
        text=f'この記事は見つかりません。記事 {view.total}件 / {view.page+1}ページ目'
    await show_view(i,method,(notice+'\n\n' if notice else '')+text,view=view,
                    **({'ephemeral':True} if method=='send_message' else {}))


class GuardView(discord.ui.View):
    def __init__(self,client,timeout=600,root=False):
        super().__init__(timeout=timeout)
        self.client=client
        self.root=root
        self.session=None if root else new_ui_session(client.store)
        self.message=None
        self.message_id=None
        if not hasattr(client,'operation_views'): client.operation_views=set()
        if not root: client.operation_views.add(self)

    async def on_timeout(self):
        if self.root: return
        active=self.client.store.setting('discord_active_ui','')==self.session
        if active:
            new_ui_session(self.client.store)
        self.client.operation_views.discard(self)
        for item in self.children: item.disabled=True
        if self.message is not None:
            try: await remove_old_controls(self.message)
            except discord.HTTPException:
                self.client.store.set_setting('discord_connection','期限切れ画面の表示を更新できませんでした。実行は拒否しています。')
        if active: await self.client.send_menu()

    async def interaction_check(self,interaction):
        if not authorized(self.client.store,interaction.user.id,interaction.guild_id,interaction.channel_id):
            await interaction.response.send_message('この操作は登録した本人専用です。',ephemeral=True)
            return False
        if self.root:
            menu=self.client.store.setting('discord',{}).get('menu')
            if menu and str(getattr(getattr(interaction,'message',None),'id',''))==str(menu):
                interaction.extras['note_ui_session']=new_ui_session(self.client.store)
                return True
        elif self.client.store.setting('discord_active_ui','')==self.session:
            return True
        for item in self.children: item.disabled=True
        source=getattr(interaction,'message',None)
        if source is not None and is_execution_record(getattr(source,'content',''),getattr(source,'embeds',[])):
            await interaction.response.edit_message(view=None)
        else:
            await interaction.response.defer()
            await interaction.delete_original_response()
        if not self.root: self.stop()
        return False


class InfoView(GuardView):
    @discord.ui.button(label='管理メニューへ戻る')
    async def back(self,i,b):
        await show_view(i,'edit_message',content='note管理',view=Menu(self.client))
        self.stop()


class OperationHistory(GuardView):
    def __init__(self,client,page=0):
        super().__init__(client)
        history=client.store.operation_history(page)
        self.page=history['page']; self.total=history['total']
        rows=[f'操作履歴 {self.total}件 / {self.page+1}ページ目']
        states={'waiting':'受付','running':'処理中','done':'完了','failed':'失敗'}
        for item in history['items']:
            stamp=datetime.fromtimestamp(item['updated'],JST).strftime('%m/%d %H:%M:%S')
            rows.append(f"{stamp}｜{states.get(item['state'],item['state'])}｜{ACTION_NAMES.get(item['action'],'記事操作')}"
                        +(f"\n記事: {item['subject'][:80]}" if item['subject'] else '')+
                        f"\n{item['result'].get('message','')[:150]}\n受付番号: {item['id'][:8]}")
        if not history['items']: rows.append('実行履歴はまだありません。')
        self.text='\n\n'.join(rows)[:1950]
        self.prev.disabled=self.page==0; self.next.disabled=(self.page+1)*5>=self.total
    async def show(self,i,page):
        view=OperationHistory(self.client,page)
        await show_view(i,'edit_message',content=view.text,view=view)
    @discord.ui.button(label='前へ')
    async def prev(self,i,b): await self.show(i,self.page-1)
    @discord.ui.button(label='次へ')
    async def next(self,i,b): await self.show(i,self.page+1)
    @discord.ui.button(label='管理メニューへ戻る',row=1)
    async def back(self,i,b):
        await show_view(i,'edit_message',content='note管理',view=Menu(self.client))


class ActionResult(GuardView):
    def __init__(self,client,listing=('all',0,'')):
        super().__init__(client)
        self.listing=listing
    @discord.ui.button(label='記事一覧へ戻る')
    async def back(self,i,b):
        view=Articles(self.client,*self.listing)
        await show_view(i,'edit_message',content=f'記事 {view.total}件 / {view.page+1}ページ目',view=view)
        self.stop()
    @discord.ui.button(label='管理メニューへ戻る')
    async def home(self,i,b):
        await show_view(i,'edit_message',content='note管理',view=Menu(self.client))
        self.stop()


class Confirm(GuardView):
    def __init__(self,client,post,action,listing=('all',0,'')):
        super().__init__(client,180)
        self.pid,self.action,self.updated=post['id'],action,post['updated']
        self.listing=listing

    @discord.ui.button(label='確認して実行',style=discord.ButtonStyle.danger)
    async def yes(self,i,button):
        await self.client.accept(i,self.action,self.pid,{'confirmed_updated':self.updated},ui_session=self.session,listing=self.listing)
        self.stop()

    @discord.ui.button(label='やめる')
    async def no(self,i,button):
        await display_article(i,self.client,self.pid,self.listing)
        self.stop()


class ScheduleModal(discord.ui.Modal,title='投稿・更新日時（日本時間）'):
    when=discord.ui.TextInput(label='日時 例: 2026-12-01 09:00',max_length=16)
    def __init__(self,client,pid,listing=('all',0,'')):
        super().__init__()
        self.client,self.pid=client,pid
        self.session=client.store.setting('discord_active_ui','')
        self.listing=listing
    async def on_submit(self,i):
        if not await modal_allowed(self.client,i,self.session): return
        try:
            try:
                due=datetime.strptime(str(self.when).strip(),'%Y-%m-%d %H:%M').replace(tzinfo=JST).timestamp()
            except ValueError:
                raise ValueError('日時は「2030-01-01 09:00」の形式で入力してください（日本時間）。') from None
            if due<=now_ts():
                raise ValueError('未来の日時を指定してください。')
            await show_view(i,'send_message',article_text(self.client.store,self.client.store.get(self.pid))+
                '\n指定日時: '+str(self.when)+'\nこの日時で予約しますか？',
                view=ScheduleConfirm(self.client,self.pid,due,self.listing),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        except ValueError as exc:
            await display_article(i,self.client,self.pid,self.listing,notice=str(exc),method='send_message')


class ScheduleConfirm(GuardView):
    def __init__(self,client,pid,due,listing=('all',0,'')):
        super().__init__(client,180)
        self.pid,self.due=pid,due
        self.updated=client.store.get(pid)['updated']
        self.listing=listing
    @discord.ui.button(label='この日時で予約',style=discord.ButtonStyle.primary)
    async def confirm(self,i,b):
        await self.client.accept(i,'schedule',self.pid,{'due':self.due,'confirmed_updated':self.updated},ui_session=self.session,listing=self.listing)
        self.stop()
    @discord.ui.button(label='やめて記事へ戻る')
    async def no(self,i,b):
        await display_article(i,self.client,self.pid,self.listing)
        self.stop()


class TagsModal(discord.ui.Modal,title='ハッシュタグ'):
    tags=discord.ui.TextInput(label='タグを空白またはカンマで区切る',required=False,max_length=1000)
    def __init__(self,client,pid,listing=('all',0,'')):
        super().__init__()
        self.client,self.pid=client,pid
        self.tags.default=' '.join(client.store.get(pid)['tags'])
        self.session=client.store.setting('discord_active_ui','')
        self.listing=listing
    async def on_submit(self,i):
        if not await modal_allowed(self.client,i,self.session): return
        await self.client.accept(i,'tags',self.pid,{'tags':str(self.tags)},ui_session=self.session,listing=self.listing)


class NewModal(discord.ui.Modal,title='noteへ下書き保存'):
    name=discord.ui.TextInput(label='記事タイトル',max_length=200)
    body=discord.ui.TextInput(label='本文（長文は /note_new のテキスト添付）',style=discord.TextStyle.paragraph,max_length=4000)
    tags=discord.ui.TextInput(label='ハッシュタグ',required=False,max_length=1000)
    def __init__(self,client):
        super().__init__()
        self.client=client
        self.session=client.store.setting('discord_active_ui','')
    async def on_submit(self,i):
        if not await modal_allowed(self.client,i,self.session): return
        await i.response.defer(ephemeral=True,thinking=getattr(i,'message',None) is None)
        from .store import tags_from_text
        try:
            if self.client.store.setting('discord_active_ui','')!=self.session:
                raise ValueError('最新の操作メニューからやり直してください。')
            if not str(self.name).strip() or not str(self.body).strip():
                raise ValueError('タイトルと本文を入力してください。')
            pid=self.client.store.incoming_draft(i.id,{'title':str(self.name),'body':str(self.body),'tags':tags_from_text(str(self.tags))})
            await self.client.accept(i,'save_note_draft',pid,already_deferred=True,ui_session=self.session)
        except Exception as exc:
            message=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else '受付できませんでした（'+type(exc).__name__+'）。'
            if self.client.store.setting('discord_active_ui','')==self.session:
                await show_view(i,'followup',message,ephemeral=True,view=InfoView(self.client))
            else:
                await show_view(i,'followup',message,ephemeral=True)


class ArticleView(GuardView):
    def __init__(self,client,post,listing=('all',0,'')):
        super().__init__(client)
        self.pid=post['id']
        self.listing=listing
        ready=bool(post.get('native_ready')) and post.get('status')!='review'
        self.publish.disabled=not ready
        self.schedule.disabled=not ready
        self.tags.disabled=not ready
        self.refresh_article.label='内容・公開条件を読み直す' if ready else '本文・公開条件を読み込む'
        if post['draft_key']:
            self.add_item(discord.ui.Button(label='noteで編集',url='https://editor.note.com/notes/'+post['draft_key']+'/edit/',row=4))
        if post['url']:
            self.add_item(discord.ui.Button(label='公開記事を開く',url=post['url'],row=4))
    @discord.ui.button(label='記事一覧へ戻る',row=0)
    async def back(self,i,b):
        view=Articles(self.client,*self.listing)
        await show_view(i,'edit_message',content=f'記事 {view.total}件 / {view.page+1}ページ目',view=view)
        self.stop()
    @discord.ui.button(label='予約・日時変更',style=discord.ButtonStyle.primary,row=1)
    async def schedule(self,i,b):
        if not await self.prepared(i): return
        await i.response.send_modal(ScheduleModal(self.client,self.pid,self.listing))
    @discord.ui.button(label='内容・公開条件を読み直す',row=1)
    async def refresh_article(self,i,b):
        await self.client.accept(i,'refresh_article',self.pid,ui_session=self.session,listing=self.listing)
    @discord.ui.button(label='タグ設定',row=2)
    async def tags(self,i,b):
        if not await self.prepared(i): return
        await i.response.send_modal(TagsModal(self.client,self.pid,self.listing))
    @discord.ui.button(label='予約取消',row=2)
    async def cancel(self,i,b):
        await self.client.accept(i,'cancel_schedule',self.pid,ui_session=self.session,listing=self.listing)
    @discord.ui.button(label='今すぐ投稿・更新',row=3)
    async def publish(self,i,b):
        if not await self.prepared(i): return
        post=self.client.store.get(self.pid)
        await show_view(i,'send_message',article_text(self.client.store,post)+'\n今すぐ投稿・更新しますか？',
                                      view=Confirm(self.client,post,'publish_note',self.listing),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
    async def prepared(self,i):
        try: post=self.client.store.get(self.pid)
        except ValueError:
            await display_article(i,self.client,self.pid,self.listing)
            return False
        if not post['native_ready'] or post['status']=='review':
            await show_view(i,'edit_message',content=article_text(self.client.store,post),
                view=ArticleView(self.client,post,self.listing),allowed_mentions=discord.AllowedMentions.none())
            self.stop()
            return False
        return True
    @discord.ui.button(label='noteから削除',style=discord.ButtonStyle.danger,row=3)
    async def delete(self,i,b):
        try: post=self.client.store.get(self.pid)
        except ValueError:
            await display_article(i,self.client,self.pid,self.listing)
            return
        await show_view(i,'send_message',article_text(self.client.store,post)+'\nnote上の記事を削除します。よろしいですか？',
                                      view=Confirm(self.client,post,'delete_note',self.listing),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


class ArticleSelect(discord.ui.Select):
    def __init__(self,client,posts,listing=('all',0,'')):
        self.client=client
        self.listing=listing
        super().__init__(placeholder='記事を選択',options=[discord.SelectOption(label=(p['title'] or '無題')[:100],
            value=p['id'],description=STATUSES.get(p['status'],p['status'])[:100]) for p in posts])
    async def callback(self,i):
        await display_article(i,self.client,self.values[0],self.listing)
        if self.view: self.view.stop()


class Articles(GuardView):
    def __init__(self,client,kind='all',page=0,query=''):
        super().__init__(client)
        self.kind,self.page,self.query=kind,page,query
        posts=[p for p in client.store.posts() if (kind=='all' or (kind=='scheduled' and p['status'] in {'scheduled','queued','native_reserved'})
               or (kind=='draft' and client.store.catalog_state(p['id']).get('remote_status')=='draft')
               or (kind=='published' and client.store.catalog_state(p['id']).get('remote_status')=='published'))
               and query.casefold() in (p['title']+' '+p['body']).casefold()]
        self.total=len(posts)
        self.page=max(0,min(page,max(0,(self.total-1)//20)))
        selected=posts[self.page*20:self.page*20+20]
        if selected:
            self.add_item(ArticleSelect(client,selected,(kind,page,query)))
        self.prev.disabled=self.page==0
        self.next.disabled=(self.page+1)*20>=len(posts)
    @discord.ui.button(label='前へ')
    async def prev(self,i,b):
        await self.show(i,self.page-1)
    @discord.ui.button(label='次へ')
    async def next(self,i,b):
        await self.show(i,self.page+1)
    @discord.ui.select(placeholder='表示する状態',options=[discord.SelectOption(label=l,value=v) for l,v in
        [('すべて','all'),('下書き','draft'),('予約','scheduled'),('公開済み','published')]])
    async def filter(self,i,select):
        view=Articles(self.client,select.values[0],0,self.query)
        await show_view(i,'edit_message',content=f'記事 {view.total}件 / 1ページ目',view=view)
    async def show(self,i,page):
        view=Articles(self.client,self.kind,page,self.query)
        await show_view(i,'edit_message',content=f'記事 {view.total}件 / {view.page+1}ページ目',view=view)
    @discord.ui.button(label='管理メニューへ戻る',row=4)
    async def back(self,i,b):
        await show_view(i,'edit_message',content='note管理',view=Menu(self.client))
        self.stop()


class Menu(GuardView):
    def __init__(self,client,root=False):
        super().__init__(client,None if root else 600,root=root)
    @discord.ui.button(label='記事一覧',custom_id='note:articles')
    async def articles(self,i,b):
        view=Articles(self.client)
        await show_view(i,'send_message',f'記事 {view.total}件',view=view,ephemeral=True)
    @discord.ui.button(label='新規下書き',custom_id='note:new',style=discord.ButtonStyle.primary)
    async def new(self,i,b):
        await i.response.send_modal(NewModal(self.client))
    @discord.ui.button(label='予約一覧',custom_id='note:schedules')
    async def schedules(self,i,b):
        view=Articles(self.client,'scheduled')
        await show_view(i,'send_message',f'予約 {view.total}件',view=view,ephemeral=True)
    @discord.ui.button(label='自動投稿 停止／再開',custom_id='note:pause')
    async def pause(self,i,b):
        await self.client.accept(i,'resume' if self.client.store.setting('paused',True) else 'pause',ui_session=session_for(self,i))
    @discord.ui.button(label='接続状態',custom_id='note:status')
    async def status(self,i,b):
        store=self.client.store
        sync=store.setting('catalog_sync',{})
        at=datetime.fromtimestamp(sync['at'],JST).strftime('%m/%d %H:%M:%S') if sync.get('at') else '未同期'
        await show_view(i,'send_message',f"Discord: 接続中 / note: {'確認済み' if store.setting('account_verified',False) else 'ログイン要確認'}"
            f"\n自動投稿: {'停止' if store.setting('paused',True) else '動作中'}\n最終同期: {at} / {'全件取得' if sync.get('complete') else '全件取得未確認'}"
            '\nPC停止・スリープ中は操作と投稿が止まります。',ephemeral=True,view=InfoView(self.client))
    @discord.ui.button(label='noteを同期',custom_id='note:sync')
    async def sync(self,i,b):
        await self.client.accept(i,'sync_articles',ui_session=session_for(self,i))
    @discord.ui.button(label='コマンド一覧',custom_id='note:commands')
    async def commands(self,i,b):
        await show_view(i,'send_message',command_help(self.client.store),view=InfoView(self.client),ephemeral=True)
    @discord.ui.button(label='操作履歴',custom_id='note:history')
    async def history(self,i,b):
        view=OperationHistory(self.client)
        await show_view(i,'send_message',view.text,view=view,ephemeral=True)


class NoteClient(discord.Client):
    def __init__(self,store):
        super().__init__(intents=discord.Intents(guilds=True,guild_messages=True),allowed_mentions=discord.AllowedMentions.none())
        self.store=store
        self.tree=app_commands.CommandTree(self)
        names=command_names(store.setting('discord_commands',{}))
        @self.tree.command(name=names['menu'],description='noteの管理メニュー・記事検索')
        async def note(i:discord.Interaction,search:str=''):
            if not await self.guard(i): return
            if search:
                view=Articles(self,query=search)
                await show_view(i,'send_message',f'検索結果 {view.total}件',view=view,ephemeral=True)
            else:
                await show_view(i,'send_message','note管理',view=Menu(self),ephemeral=True)
        @self.tree.command(name=names['new'],description='画像・長文を添えてnoteに下書き保存')
        async def new(i:discord.Interaction,title:str,body:str='',image:discord.Attachment=None,text:discord.Attachment=None):
            if not await self.guard(i): return
            request_session=new_ui_session(store)
            await i.response.defer(ephemeral=True,thinking=True)
            try:
                if text:
                    if text.size>2_000_000 or Path(text.filename).suffix.lower()!='.txt':
                        raise ValueError('本文は2MB以下のUTF-8 .txtを添付してください。')
                    body=(await text.read()).decode('utf-8-sig')
                if not title.strip() or not body.strip():
                    raise ValueError('タイトルと本文が必要です。')
                relative=''
                if image:
                    if image.size>20_000_000 or Path(image.filename).suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.gif'}:
                        raise ValueError('画像は20MB以下のPNG/JPEG/WebP/GIFを添付してください。')
                    folder=store.root/'images'
                    folder.mkdir(exist_ok=True)
                    target=folder/(uuid.uuid4().hex+Path(image.filename).suffix.lower())
                    await image.save(target)
                    relative=str(target.relative_to(store.root))
                if store.setting('discord_active_ui','')!=request_session:
                    raise ValueError('最新の操作メニューからやり直してください。')
                pid=store.incoming_draft(i.id,{'title':title,'body':body,'image':relative})
                await self.accept(i,'save_note_draft',pid,already_deferred=True,ui_session=request_session)
            except Exception as exc:
                message=str(exc) if isinstance(exc,ValueError) else '添付の読み込みに失敗しました。'
                if store.setting('discord_active_ui','')==request_session:
                    await show_view(i,'followup',message,ephemeral=True,view=InfoView(self))
                else:
                    await show_view(i,'followup',message,ephemeral=True)

        @self.tree.command(name=names['list'],description='記事一覧・記事検索')
        async def listing(i:discord.Interaction,search:str=''):
            if not await self.guard(i): return
            view=Articles(self,query=search)
            await show_view(i,'send_message',f'記事 {view.total}件',view=view,ephemeral=True)

        @self.tree.command(name=names['schedules'],description='予約した記事の一覧')
        async def schedules(i:discord.Interaction):
            if not await self.guard(i): return
            view=Articles(self,'scheduled')
            await show_view(i,'send_message',f'予約 {view.total}件',view=view,ephemeral=True)

        @self.tree.command(name=names['status'],description='note・Discordの接続状態')
        async def status(i:discord.Interaction):
            if not await self.guard(i): return
            await Menu(self).status.callback(i)

        @self.tree.command(name=names['help'],description='利用できるコマンドの一覧')
        async def help_command(i:discord.Interaction):
            if not await self.guard(i): return
            await show_view(i,'send_message',command_help(store),view=InfoView(self),ephemeral=True)

        for action,remote_action in [('sync','sync_articles'),('pause','pause'),('resume','resume')]:
            self.add_simple_command(names[action],remote_action)
        for action in ('read','schedule','cancel','publish','delete','tags'):
            self.add_article_command(names[action],action)

    def add_simple_command(self,name,action):
        async def run(i:discord.Interaction):
            if not await self.guard(i): return
            await self.accept(i,action,ui_session=new_ui_session(self.store))
        descriptions={'sync_articles':'noteの記事一覧を同期','pause':'自動投稿を停止','resume':'自動投稿を再開'}
        self.tree.add_command(app_commands.Command(name=name,description=descriptions[action],callback=run))

    def add_article_command(self,name,action):
        async def run(i:discord.Interaction,article:str):
            await self.article_command(i,action,article)
        descriptions={'read':'本文・公開条件を読み込む','schedule':'日時を指定して予約','cancel':'予約を取り消す',
                      'publish':'内容を確認して今すぐ投稿・更新','delete':'確認してnoteから削除','tags':'投稿時のハッシュタグを変更'}
        command=app_commands.Command(name=name,description=descriptions[action],callback=run)
        command.autocomplete('article')(self.article_choices)
        self.tree.add_command(command)

    async def article_choices(self,i:discord.Interaction,current:str):
        if not authorized(self.store,i.user.id,i.guild_id,i.channel_id): return []
        return [app_commands.Choice(name=(p['title'] or '無題')[:85]+' / '+STATUSES.get(p['status'],p['status']),value=p['id'])
                for p in self.store.posts() if current.casefold() in (p['title']+' '+p['id']).casefold()][:25]

    async def article_command(self,i,action,pid):
        if not await self.guard(i): return
        try:
            post=self.store.get(pid)
        except ValueError:
            await show_view(i,'send_message','記事を候補から選び直してください。',ephemeral=True,view=Articles(self))
            return
        if action in {'schedule','publish','tags'} and (not post['native_ready'] or post['status']=='review'):
            await show_view(i,'send_message',article_text(self.store,post),view=ArticleView(self,post),ephemeral=True)
        elif action=='schedule':
            await i.response.send_modal(ScheduleModal(self,pid))
        elif action=='tags':
            await i.response.send_modal(TagsModal(self,pid))
        elif action in {'publish','delete'}:
            prompt='今すぐ投稿・更新しますか？' if action=='publish' else 'note上の記事を削除します。よろしいですか？'
            await show_view(i,'send_message',article_text(self.store,post)+'\n'+prompt,
                view=Confirm(self,post,'publish_note' if action=='publish' else 'delete_note'),ephemeral=True)
        else:
            await self.accept(i,'refresh_article' if action=='read' else 'cancel_schedule',pid,ui_session=new_ui_session(self.store))

    async def on_message(self,message):
        if message.author.bot or not authorized(self.store,message.author.id,
                getattr(message.guild,'id',None),message.channel.id): return
        # Message bodies are not inspected; no privileged Message Content intent.
        new_ui_session(self.store)
        await self.send_menu()

    async def send_menu(self):
        channel=self.get_channel(int(self.store.setting('discord',{}).get('channel') or 0))
        if channel is None: raise RuntimeError('Discord管理チャンネルに接続してから実行してください。')
        sent=await channel.send(MENU_TEXT,view=Menu(self,root=True),allowed_mentions=discord.AllowedMentions.none())
        await self.activate_menu(sent.id)
        return sent.id

    async def activate_menu(self,message_id):
        config=self.store.setting('discord',{})
        if int(message_id)>int(config.get('menu') or 0):
            old=config.get('menu')
            config['menu']=str(message_id)
            self.store.set_setting('discord',config)
            new_ui_session(self.store)
            await self.retire_views(None,keep_root=True)
            if old: await self.disable_menu(old)

    async def disable_menu(self,message_id):
        try:
            from discord.http import handle_message_parameters
            channel=int(self.store.setting('discord',{})['channel'])
            original=await self.http.get_message(channel,int(message_id))
            if is_execution_record(original.get('content',''),original.get('embeds',[])):
                with handle_message_parameters(view=None) as params:
                    await self.http.edit_message(channel,int(message_id),params=params)
            else:
                await self.http.delete_message(channel,int(message_id))
        except discord.NotFound:
            pass
        except discord.HTTPException:
            self.store.set_setting('discord_connection','古い操作メニューの無効表示を更新できませんでした。古い画面の実行は拒否しています。')

    async def retire_views(self,active,keep_root=False):
        # Never let a late response disable a newer screen.
        if active is not None and not active.root and active.session!=self.store.setting('discord_active_ui',''):
            active_id=active.message_id or getattr(active.message,'id',None)
            current_session=self.store.setting('discord_active_ui','')
            same_message_current=active_id is not None and (
                str(active_id)==str(self.store.setting('discord',{}).get('menu','')) or any(
                    v is not active and v.session==current_session and
                    str(v.message_id or getattr(v.message,'id',None))==str(active_id)
                    for v in self.operation_views))
            if same_message_current:
                self.operation_views.discard(active)
                active.stop()
                return
            candidates=[active]
        else:
            candidates=[v for v in self.operation_views if v is not active]
        root=None
        if active is not None and not active.root and not keep_root and candidates!=[active]:
            config=self.store.setting('discord',{})
            root=config.get('menu')
            if root:
                config['menu']=''
                self.store.set_setting('discord',config)
                if str(root)==str(active.message_id or getattr(active.message,'id','')):
                    root=None
        for old in candidates:
            self.operation_views.discard(old)
            old_id=old.message_id or getattr(old.message,'id',None)
            active_id=(active.message_id or getattr(active.message,'id',None)) if active is not None else None
            if active is not None and old is not active and old_id==active_id:
                if not old.root: old.stop()
                continue
            for item in old.children: item.disabled=True
            if old.message is not None:
                try: await remove_old_controls(old.message)
                except discord.HTTPException:
                    self.store.set_setting('discord_connection','古い操作画面の無効表示を更新できませんでした。実行は拒否しています。')
            if not old.root: old.stop()
        if root: await self.disable_menu(root)

    async def disable_older_menus(self,channel):
        if not hasattr(channel,'history'): return
        async for message in channel.history(limit=None):
            if message.author.id!=self.user.id or str(message.id)==str(self.store.setting('discord',{}).get('menu','')): continue
            buttons=[item for row in message.components for item in getattr(row,'children',[])]
            if buttons or message.content.startswith(
                    ('操作メニュー（終了）','この操作画面は終了しています。')):
                await self.disable_menu(message.id)

    async def guard(self,i):
        if authorized(self.store,i.user.id,i.guild_id,i.channel_id): return True
        await show_view(i,'send_message','登録した管理チャンネル・本人から操作してください。',ephemeral=True)
        return False

    async def accept(self,i,action,pid=None,data=None,already_deferred=False,ui_session=None,listing=('all',0,'')):
        if not authorized(self.store,i.user.id,i.guild_id,i.channel_id):
            return
        if not already_deferred:
            await i.response.defer(ephemeral=True,thinking=getattr(i,'message',None) is None)
        try:
            jid=submit(self.store,action,pid,data,str(i.id),ui_session=ui_session)
            receipt='受付しました。処理結果は管理チャンネルへ通知します。\n受付番号: '+jid[:8]
            saved=await i.edit_original_response(content=receipt,view=None)
            message_id=getattr(getattr(i,'message',None),'id',None) or getattr(saved,'id',None)
            for view in list(self.operation_views):
                if str(view.message_id or getattr(view.message,'id',None))==str(message_id):
                    self.operation_views.discard(view); view.stop()
            config=self.store.setting('discord',{})
            if str(config.get('menu'))==str(message_id):
                config['menu']=''; self.store.set_setting('discord',config)
        except (ValueError,RuntimeError) as exc:
            if ui_session is not None and self.store.setting('discord_active_ui','')!=ui_session:
                await show_view(i,'followup',str(exc),ephemeral=True)
            else:
                await show_view(i,'followup',str(exc),ephemeral=True,view=ActionResult(self,listing))

    async def setup_hook(self):
        self.add_view(Menu(self,root=True))
        self.notifications.start()

    async def on_ready(self):
        if not hasattr(self,'setup_lock'):
            self.setup_lock=asyncio.Lock()
        async with self.setup_lock:
            await self.prepare_channel()

    async def on_guild_join(self,guild):
        # Initial Gateway login may precede the owner's invitation approval.
        # Finish onboarding as soon as Discord confirms the Bot joined.
        await self.wait_until_ready()
        await self.on_ready()

    async def prepare_channel(self):
        self.store.set_setting('discord_connection','接続中')
        info=await self.application_info()
        config=self.store.setting('discord',{})
        # Team-owned applications need a separate operator enrolment, never guess.
        if info.team is not None:
            self.store.set_setting('discord_connection','個人所有の専用Botを作成してください。')
            return
        config['owner']=str(info.owner.id)
        config['application']=str(info.id)
        self.store.set_setting('discord',config)
        available=[]
        for g in self.guilds:
            try:
                member=await g.fetch_member(info.owner.id)
                if g.owner_id==info.owner.id or member.guild_permissions.manage_guild:
                    available.append({'id':str(g.id),'name':g.name})
            except discord.HTTPException:
                continue
        self.store.set_setting('discord_guilds',available)
        if not config.get('guild'):
            self.store.set_setting('discord_connection','PCで管理サーバーを選択してください。')
            return
        guild=self.get_guild(int(config['guild']))
        if not guild:
            self.store.set_setting('discord_connection','選択したサーバーにBotを導入してください。')
            return
        if str(guild.id) not in {g['id'] for g in available}:
            self.store.set_setting('discord_connection','本人に管理権限のあるサーバーを選択してください。')
            return
        config.setdefault('installation',uuid.uuid4().hex)
        self.store.set_setting('discord',config)
        marker='note-stock:'+str(info.id)+':'+config['installation']
        channel=guild.get_channel(int(config.get('channel') or 0))
        if not channel:
            channel=next((c for c in guild.text_channels if c.topic==marker),None)
        owner=await guild.fetch_member(int(config['owner']))
        overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),
                    owner:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),
                    guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,embed_links=True,attach_files=True)}
        category=guild.get_channel(int(config.get('category') or 0))
        if category is None and channel is not None:
            category=channel.category
        if category is None:
            category=await guild.create_category('アプリ管理',overwrites=overwrites,reason='本人専用のアプリ管理')
            config['category']=str(category.id)
            self.store.set_setting('discord',config)
        if not channel:
            channel=await guild.create_text_channel('note管理',category=category,overwrites=overwrites,topic=marker,reason='note投稿管理の本人専用チャンネル')
        else:
            # Reapplying identical overwrites requires Manage Roles even though
            # creating this private channel only needs Manage Channels. Reconnect
            # must reuse the verified channel without requesting broader access.
            actual={target.id:value.pair() for target,value in channel.overwrites.items()}
            expected={target.id:value.pair() for target,value in overwrites.items()}
            changes={}
            if channel.category_id!=category.id: changes['category']=category
            if actual!=expected: changes['overwrites']=overwrites
            if changes:
                await channel.edit(**changes,reason='管理チャンネルの本人専用権限を確認')
        config['channel']=str(channel.id)
        self.store.set_setting('discord',config)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        if not config.get('menu'):
            message=None
            async for old in channel.history(limit=30):
                if old.author.id==self.user.id and old.content=='note管理' and old.components:
                    message=old
                    break
            if message is None:
                message=await channel.send(MENU_TEXT,view=Menu(self,root=True))
            config['menu']=str(message.id)
            self.store.set_setting('discord',config)
        self.store.set_setting('discord_connection','管理メニュー準備完了')
        await NoteClient.disable_older_menus(self,channel)

    @tasks.loop(seconds=3)
    async def notifications(self):
        if not self.is_ready(): return
        config=self.store.setting('discord',{})
        channel=self.get_channel(int(config.get('channel') or 0))
        if not channel: return
        for nid,body in self.store.pending_notifications():
            # Stable nonce avoids duplicates when retrying after an ambiguous send.
            import hashlib
            try:
                # discord.py's public send omits enforce_nonce; use its pinned HTTP
                # transport with Discord's documented nonce deduplication field.
                from discord.http import handle_message_parameters
                # The persistent Menu registered in setup_hook handles these stable
                # component IDs, including after restarting the app.
                sent_ids=self.store.notification_message_ids(nid)
                if not sent_ids['result_id']:
                    nonce=int(hashlib.sha256((nid+':result').encode()).hexdigest()[:15],16)
                    with handle_message_parameters(nonce=nonce,
                            embeds=[operation_embed(self.store,nid,body)],
                            allowed_mentions=discord.AllowedMentions.none()) as params:
                        params.payload['enforce_nonce']=True
                        sent=await self.http.send_message(channel.id,params=params)
                    self.store.record_notification_message(nid,'result_id',sent['id'])
                    sent_ids['result_id']=str(sent['id'])
                if not sent_ids['menu_id']:
                    nonce=int(hashlib.sha256((nid+':menu').encode()).hexdigest()[:15],16)
                    with handle_message_parameters(content=MENU_TEXT,nonce=nonce,view=Menu(self,root=True),
                            allowed_mentions=discord.AllowedMentions.none()) as params:
                        params.payload['enforce_nonce']=True
                        # A reply starts a distinct visual group on Discord mobile.
                        # Two ordinary consecutive Bot messages otherwise look joined.
                        params.payload['message_reference']={'message_id':sent_ids['result_id'],
                            'channel_id':str(channel.id),'fail_if_not_exists':False}
                        params.payload['allowed_mentions']['replied_user']=False
                        sent=await self.http.send_message(channel.id,params=params)
                    self.store.record_notification_message(nid,'menu_id',sent['id'])
                    sent_ids['menu_id']=str(sent['id'])
                await self.activate_menu(sent_ids['menu_id'])
                self.store.delivered(nid)
            except discord.HTTPException:
                break

    async def on_disconnect(self):
        self.store.set_setting('discord_connection','切断・再接続待ち')

    async def on_error(self,event_method,*args,**kwargs):
        # Never allow library tracebacks to dump interaction contents or credentials.
        import sys
        error=sys.exc_info()[1]
        detail=type(error).__name__
        if isinstance(error,discord.HTTPException):
            detail+=f' / HTTP {error.status} / code {error.code}'
        self.store.set_setting('discord_connection','Discord処理エラー（'+detail+'）。接続とBot権限を確認してください。')


class BotThread(threading.Thread):
    def __init__(self,store):
        super().__init__(daemon=True,name='note-discord')
        self.store=store
        self.client=None
        self.loop=None
    def run(self):
        for name in ('discord','aiohttp'):
            logger=logging.getLogger(name)
            logger.handlers=[logging.NullHandler()]
            logger.propagate=False
        try:
            token=unprotect(self.store.setting('discord_token',''))
            self.loop=asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.client=NoteClient(self.store)
            self.loop.run_until_complete(self.client.start(token,reconnect=True))
        except Exception as exc:
            self.store.set_setting('discord_connection','接続できません（'+type(exc).__name__+'）。Bot設定を確認してください。')
        finally:
            if self.client and self.loop and not self.loop.is_closed():
                self.loop.run_until_complete(self.client.close())
                self.loop.close()
    def stop(self):
        if self.client and self.loop and self.loop.is_running():
            return asyncio.run_coroutine_threadsafe(self.client.close(),self.loop)
