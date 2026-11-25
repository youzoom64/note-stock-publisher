"""Discord Gateway client. Only bot tokens; no user-token API or public listener."""
import asyncio
import concurrent.futures
import calendar
from datetime import date, datetime
from io import BytesIO
import json
import logging
import threading
import uuid
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks
from .remote import submit,request_selected_article
from .secrets import unprotect
from .store import JST, STATUSES, article_display_title, article_timestamp, now_ts
from .discord_commands import command_names,command_help
from .discord_flow import flow,ManagedModal
from .diagnostics import Diagnostics
from .loading_indicator import prepare_loading_emoji,body_loading_text,loading_text

ACTION_NAMES={'sync_articles':'note同期','save_note_draft':'下書き保存','refresh_article':'本文・公開条件の読み込み',
    'schedule':'予約・日時変更','cancel_schedule':'予約取消','publish_note':'投稿・更新','delete_note':'記事削除',
    'tags':'タグ変更','pause':'自動投稿の停止','resume':'自動投稿の再開','relogin_note':'noteへの再ログイン',
    'relogin_disconnect':'再ログイン画面の切断'}
RECORD_FOOTER='note操作記録'
MENU_TEXT='## 操作メニュー\n次の操作を選んでください。'
OLD_RESERVATION_PROGRESS='noteから本文と公開条件を確認しています。完了すると予約カレンダーが開きます。'


def is_execution_record(content='',embeds=()):
    content=content or ''
    if content.startswith(('完了:','要確認:')): return True
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
    action=ACTION_NAMES.get(job['action'],'記事操作') if job else ACTION_NAMES.get(body.get('action'),'操作結果')
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


def session_for(view,interaction):
    return getattr(interaction,'extras',{}).get('note_ui_session',view.session)


async def show_view(i,method,*args,**kwargs):
    view=kwargs.pop('view',None)
    if view is None:
        raise RuntimeError('Screen responses require a managed view')
    content=args[0] if args else kwargs.pop('content','')
    return await flow(view.client).render(i,method,content,view,**kwargs)



def article_text(store,post,*,publish_checked=False):
    remote=store.catalog_state(post['id']).get('remote_status','未保存')
    summary=post.get('native_summary',{})
    due=datetime.fromtimestamp(post['due'],JST).strftime('%Y-%m-%d %H:%M') if post.get('due') else 'なし'
    ready=bool(post.get('native_ready')) and (post.get('status')!='review' or publish_checked)
    price=('無料' if summary['price']==0 else str(summary['price'])+'円') if ready and 'price' in summary else '未確認'
    preparation=''
    reason=('\n理由: '+post['error'][:600]) if post.get('error') else ''
    if not ready:
        preparation=('\n本文・公開条件は操作時にnoteから読み込みます。' if post['draft_key']
                     else '\nnoteの下書きに保存すると予約できるようになります。')
    tags=('\nタグ: '+' '.join('#'+t for t in post['tags'])) if post['tags'] else ''
    return (f"{article_display_title(post)}\nnote: {remote}\n管理: {STATUSES.get(post['status'],post['status'])}"
            f"{reason}"
            f"\n予約: {due}（日本時間）\n価格: {price}"
            f"{tags}{preparation}\n\n{post['body'][:900]}")[:1800]


def article_body_files(post):
    body=post['body']
    return [discord.File(BytesIO(body.encode('utf-8')),filename='article.txt')] if len(body)>900 else []


async def display_article(i,client,pid,listing=('all',0,''),notice='',method='edit_message',read_selected=False):
    job_id=None
    try:
        post=client.store.get(pid)
        if read_selected:
            try:
                job_id=request_selected_article(client.store,pid)
            except ValueError as exc:
                notice=str(exc)
        if job_id:
            notice=body_loading_text(client)
        text=article_text(client.store,post)
        view=ArticleView(client,post,listing,loading=bool(job_id))
    except ValueError:
        view=Articles(client,*listing)
        text=f'この記事は見つかりません。記事 {view.total}件 / {view.page+1}ページ目'
    message=await show_view(i,method,((notice+'\n\n' if notice else '')+text)[:2000],view=view,
                           allowed_mentions=discord.AllowedMentions.none(),
                           **({'ephemeral':True} if method=='send_message' else {}))
    if job_id and message is not None:
        await show_article_read_result(client,message,view.session,pid,job_id)


async def show_article_read_result(client,message,session,pid,job_id):
    ui=flow(client)
    loop=asyncio.get_running_loop()
    deadline=loop.time()+120
    while ui.current()==session:
        job=client.store.job(job_id)
        if job['state'] in {'done','failed'}:
            notice='' if job['state']=='done' else job['result'].get('message') or '本文を取得できませんでした。'
            break
        if loop.time()>=deadline:
            notice='本文の取得がまだ完了していません。完了後に記事を開き直すと本文を表示できます。'
            break
        await asyncio.sleep(.5)
    else:
        return
    async with ui.lock:
        if ui.current()!=session:
            return
        try:
            post=client.store.get(pid)
            content=((notice+'\n\n' if notice else '')+article_text(client.store,post))[:2000]
            await message.edit(content=content,attachments=article_body_files(post),embeds=[],
                               allowed_mentions=discord.AllowedMentions.none())
        except (ValueError,discord.NotFound):
            return


class ScreenLifecycle:
    def __init_subclass__(cls,**kwargs):
        super().__init_subclass__(**kwargs)
        if 'interaction_check' in cls.__dict__:
            raise TypeError('Screen admission belongs to GuardView')
    def initialize_screen(self,client,root=False):
        self.client=client
        self.root=root
        self.session=uuid.uuid4().hex
        self.message=None
        self.message_id=None
        if not hasattr(client,'operation_views'): client.operation_views=set()

    async def on_error(self,interaction,error,item):
        await self.client.finish_action(interaction,'操作を完了できませんでした（'+type(error).__name__+'）。',
                                        deferred=interaction.response.is_done())

    async def on_timeout(self):
        if self.root: return
        active=flow(self.client).expire(self)
        self.client.operation_views.discard(self)
        for item in self.walk_children():
            if hasattr(item,'disabled'):item.disabled=True
        current_id=flow(self.client).state().get('message_id')
        if self.message is not None and (active or str(self.message.id)!=str(current_id)):
            try: await remove_old_controls(self.message)
            except discord.HTTPException:
                self.client.store.set_setting('discord_connection','期限切れ画面の表示を更新できませんでした。実行は拒否しています。')
        if active: await self.client.send_menu()

    async def interaction_check(self,interaction):
        return await flow(self.client).admit(interaction,view=self)


class GuardView(ScreenLifecycle,discord.ui.View):
    def __init__(self,client,timeout=600,root=False):
        super().__init__(timeout=timeout)
        self.initialize_screen(client,root)


class GuardLayoutView(ScreenLifecycle,discord.ui.LayoutView):
    def __init__(self,client,timeout=600):
        super().__init__(timeout=timeout)
        self.initialize_screen(client)


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
        states={'waiting':loading_text(client,'待機中'),'running':loading_text(client,'処理中'),
                'done':'完了','failed':'失敗'}
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


class Confirm(GuardView):
    def __init__(self,client,post,action,listing=('all',0,''),*,due=None,publish_check=None):
        super().__init__(client,180)
        self.pid,self.action,self.updated=post['id'],action,post['updated']
        self.listing=listing
        self.due=due
        self.publish_check=publish_check
        if action=='publish_note':
            self.yes.label='今すぐ更新する' if post.get('url') or post['status']=='published' else '今すぐ投稿する'
        elif action=='schedule':
            self.yes.label='この日時で予約する'

    @discord.ui.button(label='確認して実行',style=discord.ButtonStyle.danger)
    async def yes(self,i,button):
        data={'confirmed_updated':self.updated}
        if self.due is not None:
            data['due']=self.due
        if self.publish_check:
            data['publish_check']=self.publish_check
        await self.client.accept(i,self.action,self.pid,data,ui_session=self.session,listing=self.listing)
        self.stop()

    @discord.ui.button(label='やめる')
    async def no(self,i,button):
        await self.client.finish_action(i,'操作を取り消しました。',ok=True)
        self.stop()


class TagsModal(ManagedModal,title='ハッシュタグ'):
    tags=discord.ui.TextInput(label='タグを空白またはカンマで区切る',required=False,max_length=1000)
    def __init__(self,client,pid,listing=('all',0,'')):
        super().__init__(client)
        self.client,self.pid=client,pid
        self.tags.default=' '.join(client.store.get(pid)['tags'])
        self.session=client.store.setting('discord_active_ui','')
        self.listing=listing
    async def handle_submit(self,i):
        await self.client.accept(i,'tags',self.pid,{'tags':str(self.tags)},ui_session=self.session,listing=self.listing)


class NewModal(ManagedModal,title='noteへ下書き保存'):
    name=discord.ui.TextInput(label='記事タイトル',max_length=200)
    body=discord.ui.TextInput(label='本文（長文は /note_new のテキスト添付）',style=discord.TextStyle.paragraph,max_length=4000)
    tags=discord.ui.TextInput(label='ハッシュタグ',required=False,max_length=1000)
    def __init__(self,client):
        super().__init__(client)
        self.client=client
        self.session=client.store.setting('discord_active_ui','')
    async def handle_submit(self,i):
        await flow(self.client).defer(i,thinking=getattr(i,'message',None) is None)
        from .store import tags_from_text
        try:
            if self.client.store.setting('discord_active_ui','')!=self.session:
                return await flow(self.client).obsolete(i)
            if not str(self.name).strip() or not str(self.body).strip():
                raise ValueError('タイトルと本文を入力してください。')
            pid=self.client.store.incoming_draft(i.id,{'title':str(self.name),'body':str(self.body),'tags':tags_from_text(str(self.tags))})
            await self.client.accept(i,'save_note_draft',pid,already_deferred=True,ui_session=self.session)
        except Exception as exc:
            message=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else '受付できませんでした（'+type(exc).__name__+'）。'
            if self.client.store.setting('discord_active_ui','')==self.session:
                await self.client.finish_action(i,message,deferred=True)
            else:
                await flow(self.client).obsolete(i)


class ReservationTime(GuardView):
    def __init__(self,view,day,hour=None,minute=None):
        super().__init__(view.client)
        self.view,self.day=view,day
        initial=datetime.fromtimestamp(view.post['due'],JST) if view.post.get('due') else datetime.now(JST)
        self.hour=initial.hour if hour is None else hour
        self.minute=initial.minute if minute is None else minute
        self.text=f'**{view.year}年{view.month}月{day}日 {self.hour:02d}:{self.minute:02d}（日本時間）**\n時と分を選んでください。分はボタンで1分ずつ調整できます。'
        self.hours=discord.ui.Select(placeholder='時を選択',row=0,options=[
            discord.SelectOption(label=f'{h:02d}時',value=str(h),default=h==self.hour) for h in range(24)])
        minutes=sorted(set(range(0,60,5))|{self.minute})
        self.minutes=discord.ui.Select(placeholder='分を選択',row=1,options=[
            discord.SelectOption(label=f'{m:02d}分',value=str(m),default=m==self.minute) for m in minutes])
        self.hours.callback=self.select_hour
        self.minutes.callback=self.select_minute
        self.add_item(self.hours)
        self.add_item(self.minutes)

    async def render_time(self,i,hour,minute,notice=''):
        view=ReservationTime(self.view,self.day,hour,minute)
        await show_view(i,'edit_message',(notice+'\n' if notice else '')+view.text,view=view)

    async def select_hour(self,i):
        await self.render_time(i,int(self.hours.values[0]),self.minute)

    async def select_minute(self,i):
        await self.render_time(i,self.hour,int(self.minutes.values[0]))

    @discord.ui.button(label='−1分',row=2)
    async def earlier(self,i,b):
        hour,minute=divmod((self.hour*60+self.minute-1)%1440,60)
        await self.render_time(i,hour,minute)

    @discord.ui.button(label='＋1分',row=2)
    async def later(self,i,b):
        hour,minute=divmod((self.hour*60+self.minute+1)%1440,60)
        await self.render_time(i,hour,minute)

    @discord.ui.button(label='この時刻で確認',style=discord.ButtonStyle.primary,row=3)
    async def confirm(self,i,b):
        view=self.view
        due=datetime(view.year,view.month,self.day,self.hour,self.minute,tzinfo=JST)
        if due.timestamp()<=now_ts():
            return await self.render_time(i,self.hour,self.minute,'未来の時刻を選んでください。')
        post=view.client.store.get(view.post['id'])
        await show_view(i,'edit_message',article_text(view.client.store,post)+
                        f'\n**{due:%Y/%m/%d %H:%M}（日本時間）** に予約しますか？',
                        view=Confirm(view.client,post,'schedule',view.listing,due=due.timestamp()))

    @discord.ui.button(label='日付へ戻る',row=3)
    async def back(self,i,b):
        view=ReservationCalendar(self.client,self.view.post,self.view.listing,self.view.year,self.view.month)
        await show_view(i,'edit_message',view.text,view=view)


class ReservationCalendar(GuardView):
    def __init__(self,client,post,listing,year,month):
        super().__init__(client)
        self.post,self.listing,self.year,self.month=post,listing,year,month
        from .calendar_image import reservation_counts
        counts,_=reservation_counts(client.store.posts(),year,month)
        today=datetime.now(JST).date()
        booked='、'.join(f'{day}日 {count}件' for day,count in sorted(counts.items()))
        title=discord.utils.escape_markdown((post['title'] or '無題').replace('\n',' ')[:80])
        self.text=(f'**{year}年{month}月の予約カレンダー（日本時間）**\n'
                   f'{title}\n日付を押すと時刻を選べます。'
                   +(f'\n予約済み　{booked}' if booked else ''))
        self.previous.disabled=(year,month)<=(today.year,today.month)
        self.following.disabled=(year,month)==(9999,12)
        self.parts=[ReservationDays(self,today)]

    async def turn_month(self,i,step):
        year,month=divmod(self.year*12+self.month-1+step,12)
        view=ReservationCalendar(self.client,self.post,self.listing,year,month+1)
        await show_view(i,'edit_message',view.text,view=view)

    @discord.ui.button(label='前月',row=2)
    async def previous(self,i,b):
        await self.turn_month(i,-1)

    @discord.ui.button(label='翌月',row=2)
    async def following(self,i,b):
        await self.turn_month(i,1)

    @discord.ui.button(label='記事へ戻る',row=2)
    async def back(self,i,b):
        await display_article(i,self.client,self.post['id'],self.listing)


class ReservationDays(GuardLayoutView):
    def __init__(self,month,today):
        super().__init__(month.client)
        last=calendar.monthrange(month.year,month.month)[1]
        for start in range(1,last+1,5):
            row=discord.ui.ActionRow()
            for day in range(start,min(start+5,last+1)):
                target=date(month.year,month.month,day)
                weekday='月火水木金土日'[target.weekday()]
                button=discord.ui.Button(label=f'{day}日 ({weekday})',disabled=target<today,
                    style=discord.ButtonStyle.primary if target==today else discord.ButtonStyle.secondary)

                async def choose(i,target=target):
                    ui=flow(month.client)
                    current=datetime.now(JST).date()
                    if target<current:
                        refreshed=ReservationCalendar(month.client,month.post,month.listing,current.year,current.month)
                        return await show_view(i,'edit_message',refreshed.text,view=refreshed)
                    view=ReservationTime(month,target.day)
                    await show_view(i,'edit_message',view.text,view=view)

                button.callback=choose
                row.add_item(button)
            self.add_item(row)


async def open_reservation(i,client,post,listing=('all',0,'')):
    store=client.store
    ui=flow(client)
    pid=post['id']
    try:
        store.require_schedulable(pid)
        if not post['draft_key']:
            raise ValueError('先にnoteへ下書きを保存してください。')
        if post['native_ready'] and post['status']!='review' and store.setting('remote_relogin',{}).get('schedule_verified'):
            if await ui.activity(i,pid):
                return
    except (ValueError,RuntimeError) as exc:
        if not ui.valid(i):
            return await ui.obsolete(i)
        method='deferred' if ui.responded(i) else 'send_message'
        return await display_article(i,client,pid,listing,notice=str(exc),method=method)
    initial=datetime.fromtimestamp(max(post.get('due') or 0,now_ts()),JST)
    view=ReservationCalendar(client,post,listing,initial.year,initial.month)
    method='deferred' if ui.responded(i) else 'send_message'
    await show_view(i,method,view.text,view=view,ephemeral=True)


class PublishCheckView(GuardView):
    def __init__(self,client,pid,listing):
        super().__init__(client)
        self.pid,self.listing=pid,listing

    @discord.ui.button(label='記事へ戻る')
    async def back(self,i,b):
        await display_article(i,self.client,self.pid,self.listing)


NOTE_ACTION_BLOCKED={'editing','posting','unknown','native_reserved'}


async def open_tags(i,client,pid,listing=('all',0,'')):
    try:
        post=client.store.get(pid)
        if not post['draft_key'] or post['status'] in NOTE_ACTION_BLOCKED:
            raise ValueError('noteに保存済みで、処理中でない記事を選んでください。')
    except ValueError as exc:
        method='edit_message' if getattr(i,'message',None) is not None else 'send_message'
        return await display_article(i,client,pid,listing,notice=str(exc),method=method)
    await flow(client).form(i,TagsModal(client,pid,listing))


async def open_publish(i,client,post,listing=('all',0,'')):
    store=client.store
    ui=flow(client)
    pid=post['id']
    check=None
    try:
        if not post['draft_key']:
            raise ValueError('先にnoteへ下書きを保存してください。')
        if post['status'] in NOTE_ACTION_BLOCKED:
            raise ValueError('別の処理を完了してから、もう一度開いてください。')
        if not post['native_ready'] or post['status']=='review':
            expected=ui.capture(i)
            await ui.defer(i,thinking=getattr(i,'message',None) is None)
            check=submit(store,'refresh_article',pid,key=f'publish-preflight:{i.id}',ui_session=expected)
            ui.rebase(i)
            await show_view(i,'deferred',loading_text(client,'公開条件を確認しています。確認できたら投稿前の確認画面を表示します。'),
                            view=PublishCheckView(client,pid,listing),allowed_mentions=discord.AllowedMentions.none())
            deadline=asyncio.get_running_loop().time()+120
            while ui.valid(i):
                job=store.job(check)
                if job['state']=='done':
                    post=store.get(pid)
                    if not post['native_ready']:
                        raise ValueError('公開条件を確認できませんでした。')
                    break
                if job['state']=='failed':
                    raise ValueError(job['result'].get('message') or '公開条件を確認できませんでした。')
                if asyncio.get_running_loop().time()>=deadline:
                    raise ValueError('公開条件の確認が終わりませんでした。PC側の状態を確認して、もう一度開いてください。')
                await asyncio.sleep(.5)
            if not ui.valid(i):
                return
        method='deferred' if ui.responded(i) else 'send_message'
        await show_view(i,method,article_text(store,post,publish_checked=bool(check))+'\n今すぐ投稿・更新しますか？',
                        view=Confirm(client,post,'publish_note',listing,publish_check=check),
                        ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
    except (ValueError,RuntimeError) as exc:
        if ui.valid(i):
            await display_article(i,client,pid,listing,notice=str(exc),
                                method='deferred' if ui.responded(i) else 'send_message')
        else:
            await ui.obsolete(i)
    finally:
        if check:
            store.cancel_publish_check(check)


class ArticleView(GuardView):
    def __init__(self,client,post,listing=('all',0,''),*,loading=False):
        super().__init__(client)
        self.pid=post['id']
        self.post=post
        self.loading=loading
        self.listing=listing
        ready=bool(post.get('native_ready')) and post.get('status')!='review'
        can_use_note=bool(post['draft_key']) and post['status'] not in NOTE_ACTION_BLOCKED
        self.publish.disabled=not can_use_note
        self.schedule.disabled=not post['draft_key']
        try:
            client.store.require_schedulable(self.pid)
        except ValueError:
            self.schedule.disabled=True
        self.tags.disabled=not can_use_note
        self.refresh_article.disabled=not can_use_note
        self.cancel.disabled=not (post['status']=='queued' or (post['status'] in {'scheduled','review','overdue'} and post['due']))
        self.refresh_article.label='内容・公開条件を読み直す' if ready else '本文・公開条件を読み込む'
        if post['draft_key']:
            self.add_item(discord.ui.Button(label='noteで編集',url='https://editor.note.com/notes/'+post['draft_key']+'/edit/',row=4))
        if post['url']:
            self.add_item(discord.ui.Button(label='公開記事を開く',url=post['url'],row=4))
    def make_attachments(self):
        return article_body_files(self.post)
    @discord.ui.button(label='記事一覧へ戻る',row=0)
    async def back(self,i,b):
        view=Articles(self.client,*self.listing)
        await show_view(i,'edit_message',content=f'記事 {view.total}件 / {view.page+1}ページ目',view=view)
        self.stop()
    @discord.ui.button(label='予約・日時変更',style=discord.ButtonStyle.primary,row=1)
    async def schedule(self,i,b):
        try:
            post=self.client.store.get(self.pid)
        except ValueError:
            return await display_article(i,self.client,self.pid,self.listing)
        await open_reservation(i,self.client,post,self.listing)
    @discord.ui.button(label='内容・公開条件を読み直す',row=1)
    async def refresh_article(self,i,b):
        await self.client.accept(i,'refresh_article',self.pid,ui_session=self.session,listing=self.listing)
    @discord.ui.button(label='タグ設定',row=2)
    async def tags(self,i,b):
        await open_tags(i,self.client,self.pid,self.listing)
    @discord.ui.button(label='予約取消',row=2)
    async def cancel(self,i,b):
        await self.client.accept(i,'cancel_schedule',self.pid,ui_session=self.session,listing=self.listing)
    @discord.ui.button(label='今すぐ投稿・更新',row=3)
    async def publish(self,i,b):
        try:
            post=self.client.store.get(self.pid)
        except ValueError:
            return await display_article(i,self.client,self.pid,self.listing)
        await open_publish(i,self.client,post,self.listing)
    @discord.ui.button(label='noteから削除',style=discord.ButtonStyle.danger,row=3)
    async def delete(self,i,b):
        try: post=self.client.store.get(self.pid)
        except ValueError:
            await display_article(i,self.client,self.pid,self.listing)
            return
        await show_view(i,'send_message',article_text(self.client.store,post)+'\nnote上の記事を削除します。よろしいですか？',
                                      view=Confirm(self.client,post,'delete_note',self.listing),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


class ArticleSearchModal(ManagedModal,title='記事を検索'):
    query=discord.ui.TextInput(label='タイトル・本文のキーワード（空欄で解除）',required=False,max_length=100)
    def __init__(self,listing):
        super().__init__(listing.client);self.listing=listing;self.session=listing.session
        self.query.default=listing.query
    async def handle_submit(self,i):
        view=Articles(self.client,self.listing.kind,0,str(self.query).strip())
        await show_view(i,'send_message',view.list_text,view=view,ephemeral=True)


class ArticleRows(GuardLayoutView):
    BUTTON_STYLE={
        'draft':discord.ButtonStyle.primary,
        'editing':discord.ButtonStyle.primary,
        'published':discord.ButtonStyle.success,
    }

    def __init__(self,listing,entries,first):
        super().__init__(listing.client)
        self.listing=listing
        self.text=f'{first}～{first+len(entries)-1}件目'
        self.add_item(discord.ui.TextDisplay(self.text))
        for post,_ in entries:
            label=('📃 ' if post.get('native_ready') else '')+STATUSES.get(post['status'],post['status'])+'｜'+(post['title'] or '無題')
            # Button labels are plain text; they do not interpret Markdown.
            title=' '.join(label.split());units=0;chars=[]
            for char in title:
                cost=len(char.encode('utf-16-le'))//2
                if units+cost>77:chars.append('…');break
                chars.append(char);units+=cost
            button=discord.ui.Button(label=''.join(chars),custom_id='article:'+uuid.uuid4().hex,
                                     style=self.BUTTON_STYLE.get(post['status'],discord.ButtonStyle.secondary))
            async def open_article(i,pid=post['id']):
                await display_article(i,self.client,pid,self.listing.context,read_selected=True)
            button.callback=open_article
            self.add_item(discord.ui.ActionRow(button))


class ArticleListFooter(GuardView):
    def __init__(self,listing):
        super().__init__(listing.client);self.listing=listing;self.text='記事一覧の操作'
        self.prev.disabled=listing.page==0;self.next.disabled=listing.page+1>=listing.pages
    @discord.ui.button(label='前へ')
    async def prev(self,i,b):await self.listing.show(i,self.listing.page-1)
    @discord.ui.button(label='次へ')
    async def next(self,i,b):await self.listing.show(i,self.listing.page+1)
    @discord.ui.button(label='管理メニューへ戻る')
    async def back(self,i,b):await self.listing.back.callback(i)


class Articles(GuardView):
    PAGE_SIZE=15

    def __init__(self,client,kind='all',page=0,query=''):
        super().__init__(client)
        self.kind,self.query=kind,query
        entries=[]
        with client.store.connect() as connection:
            catalog_by_id={row['post_id']:dict(row) for row in connection.execute(
                'SELECT * FROM catalog WHERE account=?',(client.store.setting('account',''),))}
            published_at={row['post_id']:row['stamp'] for row in connection.execute(
                "SELECT post_id,MAX(updated) AS stamp FROM jobs WHERE action='publish_note' AND state='done' GROUP BY post_id")}
        for post in client.store.posts():
            catalog=catalog_by_id.get(post['id'],{});remote=catalog.get('remote_status','')
            if kind=='scheduled' and post['status'] not in {'scheduled','queued','native_reserved'}: continue
            if kind=='draft' and (remote or post['status'])!='draft': continue
            if kind=='published' and (remote or post['status'])!='published': continue
            if query.casefold() not in (post['title']+' '+post['body']).casefold(): continue
            stamp=article_timestamp(post,catalog)
            if post['status']=='published':stamp=max(stamp,published_at.get(post['id'],0))
            entries.append((post,stamp))
        entries.sort(key=lambda row:(row[1],row[0]['created'],row[0]['id']),reverse=True)
        self.total=len(entries);self.pages=max(1,(self.total+self.PAGE_SIZE-1)//self.PAGE_SIZE)
        self.page=max(0,min(page,self.pages-1));self.context=(kind,self.page,query)
        chosen=entries[self.page*self.PAGE_SIZE:(self.page+1)*self.PAGE_SIZE]
        self.selected=[post for post,_ in chosen]
        first=self.page*self.PAGE_SIZE+1;last=first+len(chosen)-1
        label={'all':'すべて','draft':'下書き','published':'公開済み','scheduled':'予約'}.get(kind,kind)
        self.list_text=f'## 記事一覧\n{self.total}件 · {label} · 新しい順 · {self.PAGE_SIZE}件表示\n'
        self.list_text+=(f'{first}～{last}件目 / {self.page+1}／{self.pages}ページ' if chosen else '該当する記事はありません。')
        if query: self.list_text+='\n検索: '+discord.utils.escape_markdown(query)
        if chosen:self.list_text+='\n記事タイトルを押すと詳細が開きます。'
        self.embeds=[]
        self.parts=[ArticleRows(self,chosen,first),ArticleListFooter(self)] if chosen else []
        self.prev.disabled=self.page==0;self.next.disabled=self.page+1>=self.pages
        self.clear_search.disabled=not query
        for button,value in [(self.all, 'all'),(self.drafts,'draft'),(self.published,'published'),(self.scheduled,'scheduled')]:
            button.style=discord.ButtonStyle.primary if kind==value else discord.ButtonStyle.secondary
    async def show(self,i,page,notice=''):
        view=Articles(self.client,self.kind,page,self.query)
        if notice:view.list_text=notice+'\n\n'+view.list_text
        await show_view(i,'send_message',view.list_text,view=view,ephemeral=True)
    async def choose_filter(self,i,kind):
        view=Articles(self.client,kind,0,self.query)
        await show_view(i,'edit_message',view.list_text,view=view)
    @discord.ui.button(label='記事検索',row=0)
    async def search(self,i,b): await flow(self.client).form(i,ArticleSearchModal(self))
    @discord.ui.button(label='検索を解除',row=0)
    async def clear_search(self,i,b):
        view=Articles(self.client,self.kind,0,'')
        await show_view(i,'edit_message',view.list_text,view=view)
    @discord.ui.button(label='すべて',row=1)
    async def all(self,i,b): await self.choose_filter(i,'all')
    @discord.ui.button(label='下書き',row=1)
    async def drafts(self,i,b): await self.choose_filter(i,'draft')
    @discord.ui.button(label='公開済み',row=1)
    async def published(self,i,b): await self.choose_filter(i,'published')
    @discord.ui.button(label='予約',row=1)
    async def scheduled(self,i,b): await self.choose_filter(i,'scheduled')
    @discord.ui.button(label='前へ',row=2)
    async def prev(self,i,b): await self.show(i,self.page-1)
    @discord.ui.button(label='次へ',row=2)
    async def next(self,i,b): await self.show(i,self.page+1)
    @discord.ui.button(label='管理メニューへ戻る',row=2)
    async def back(self,i,b):
        await show_view(i,'edit_message',content='note管理',view=Menu(self.client));self.stop()


class Menu(GuardView):
    def __init__(self,client,root=False):
        super().__init__(client,None if root else 600,root=root)
        from .relogin import available
        if available(client.store):
            recover=discord.ui.Button(label='noteへ再ログイン',custom_id='note:relogin',row=2,style=discord.ButtonStyle.primary)
            recover.callback=client.begin_relogin
            self.add_item(recover)
    @discord.ui.button(label='記事一覧',custom_id='note:articles')
    async def articles(self,i,b):
        view=Articles(self.client)
        await show_view(i,'send_message',f'記事 {view.total}件',view=view,ephemeral=True)
    @discord.ui.button(label='新規下書き',custom_id='note:new',style=discord.ButtonStyle.primary)
    async def new(self,i,b):
        await flow(self.client).form(i,NewModal(self.client))
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


class RecoveryView(GuardView):
    def __init__(self,client): super().__init__(client,600)
    @discord.ui.button(label='Discord内の操作画面を開く',style=discord.ButtonStyle.primary)
    async def launch(self,i,b):
        try:
            launched=await i.response.launch_activity()
            instance=getattr(launched,'activity_id',None) or getattr(getattr(launched,'resource',None),'id',None)
            if not instance: raise ValueError('Activityの起動結果を確認できませんでした。')
            from .relogin import manager
            await asyncio.to_thread(manager(self.client.store).activity_opened,instance)
        except discord.HTTPException:
            await self.client.finish_action(i,'Activityを開けませんでした。接続設定を確認してください。',deferred=i.response.is_done())
    @discord.ui.button(label='管理メニューへ戻る')
    async def back(self,i,b):
        await show_view(i,'edit_message',content='note管理',view=Menu(self.client))
    @discord.ui.button(label='再ログインを中止',style=discord.ButtonStyle.danger)
    async def cancel_recovery(self,i,b):
        await flow(self.client).progress(i,'再ログインの待機を中止しています…')
        from .relogin import manager
        stopped=await asyncio.to_thread(manager(self.client.store).cancel)
        if not stopped:
            await self.client.render_recovery_screen(i,MENU_TEXT,Menu(self.client))


class NoteClient(discord.Client):
    def __init__(self,store):
        super().__init__(intents=discord.Intents(guilds=True,guild_messages=True),allowed_mentions=discord.AllowedMentions.none())
        self.store=store
        self.recovery_ui_lock=asyncio.Lock()
        self.tree=app_commands.CommandTree(self)
        names=command_names(store.setting('discord_commands',{}))
        @self.tree.command(name=names['relogin'],description='Discord内の専用画面でnoteへ再ログイン')
        async def relogin(i:discord.Interaction):
            if not await self.guard(i): return
            await self.begin_relogin(i)
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
            request_session=flow(self).capture(i)
            await flow(self).defer(i,thinking=True)
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
                    return await flow(self).obsolete(i)
                pid=store.incoming_draft(i.id,{'title':title,'body':body,'image':relative})
                await self.accept(i,'save_note_draft',pid,already_deferred=True,ui_session=request_session)
            except Exception as exc:
                message=str(exc) if isinstance(exc,ValueError) else '添付の読み込みに失敗しました。'
                if store.setting('discord_active_ui','')==request_session:
                    await self.finish_action(i,message,deferred=True)
                else:
                    await flow(self).obsolete(i)

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

    async def begin_relogin(self,i):
        if not await self.guard(i): return
        source=getattr(i,'message',None)
        private=bool(getattr(getattr(source,'flags',None),'ephemeral',False))
        flow(self).capture(i)
        await flow(self).defer(i,thinking=not private)
        async with self.recovery_ui_lock:
            try:
                from .relogin import manager
                code=await asyncio.to_thread(manager(self.store).issue,str(i.id))
                await self.render_recovery_screen(i,loading_text(self,'再ログイン用の接続コード（10分間・一度限り）')+'\n`'+code+'`\n'
                    'コードをコピーし、「Discord内の操作画面を開く」から入力してください。\n'
                    '入力せず戻った場合も、再ログインを押せばこの接続待機へ戻れます。\n'
                    '専用ブラウザーのnoteからログアウトし、ログイン画面を開いています。\n'
                    'noteのパスワードはチャットに書かず、操作画面で入力します。',RecoveryView(self))
            except Exception as exc:
                message=str(exc) if isinstance(exc,ValueError) else '再ログインを開始できませんでした。PCで拡張機能の状態を確認してください。'
                await self.render_recovery_screen(i,message+'\n\n'+MENU_TEXT,Menu(self))

    async def render_recovery_screen(self,i,content,view):
        return await flow(self).render(i,'deferred',content,view,allowed_mentions=discord.AllowedMentions.none())

    async def finish_action(self,i,message,ok=False,deferred=False):
        if deferred: i.extras['note_deferred']=True
        return await flow(self).finish(i,message,ok)

    def add_simple_command(self,name,action):
        async def run(i:discord.Interaction):
            if not await self.guard(i): return
            await self.accept(i,action,ui_session=flow(self).capture(i))
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
        return [app_commands.Choice(name=article_display_title(p)[:85]+' / '+STATUSES.get(p['status'],p['status']),value=p['id'])
                for p in self.store.posts() if current.casefold() in (p['title']+' '+p['id']).casefold()][:25]

    async def article_command(self,i,action,pid):
        if not await self.guard(i): return
        try:
            post=self.store.get(pid)
            if action!='read':
                request_selected_article(self.store,pid)
        except ValueError:
            await show_view(i,'send_message','記事を候補から選び直してください。',ephemeral=True,view=Articles(self))
            return
        if action=='schedule':
            await open_reservation(i,self,post)
        elif action=='tags':
            await open_tags(i,self,pid)
        elif action=='publish':
            await open_publish(i,self,post)
        elif action=='delete':
            await show_view(i,'send_message',article_text(self.store,post)+'\nnote上の記事を削除します。よろしいですか？',
                view=Confirm(self,post,'delete_note'),ephemeral=True)
        else:
            await self.accept(i,'refresh_article' if action=='read' else 'cancel_schedule',pid,ui_session=flow(self).capture(i))

    async def on_message(self,message):
        if message.author.bot or not authorized(self.store,message.author.id,
                getattr(message.guild,'id',None),message.channel.id): return
        # Message bodies are not inspected; no privileged Message Content intent.
        await self.send_menu()

    async def send_menu(self):
        channel=self.get_channel(int(self.store.setting('discord',{}).get('channel') or 0))
        if channel is None: raise RuntimeError('Discord管理チャンネルに接続してから実行してください。')
        sent=await channel.send(MENU_TEXT,view=Menu(self,root=True),allowed_mentions=discord.AllowedMentions.none())
        await self.activate_menu(sent.id)
        return sent.id

    async def activate_menu(self,message_id):
        async with flow(self).lock:
            await self.install_menu(message_id)

    async def install_menu(self,message_id):
        config=self.store.setting('discord',{})
        latest=max(int(config.get('menu') or 0),int(flow(self).state().get('message_id') or 0))
        if int(message_id)>latest:
            old=config.get('menu')
            config['menu']=str(message_id)
            self.store.set_setting('discord',config)
            view=Menu(self,root=True)
            flow(self).install(view,message_id=message_id)
            await self.retire_views(view,keep_root=True)
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
        ui=flow(self);state=ui.state();current=ui.current()
        current_ids=set(state.get('message_ids',[state.get('message_id','')]))
        if active is not None and active.session!=current:
            candidates=[active]
        else:
            candidates=[v for v in self.operation_views if v.session!=current]
        for old in candidates:
            self.operation_views.discard(old)
            old_id=str(old.message_id or getattr(old.message,'id',''))
            if old_id not in current_ids and old.message is not None:
                try:
                    await remove_old_controls(old.message)
                except discord.HTTPException:
                    self.store.set_setting('discord_connection','古い操作画面の表示を更新できませんでした。')
            for item in old.walk_children():
                if hasattr(item,'disabled'):item.disabled=True
            if not old.root:old.stop()

    async def disable_older_menus(self,channel):
        if not hasattr(channel,'history'): return
        async for message in channel.history(limit=None):
            if message.author.id!=self.user.id or str(message.id)==str(self.store.setting('discord',{}).get('menu','')): continue
            buttons=[item for row in message.components for item in getattr(row,'children',[])]
            if buttons or message.content==OLD_RESERVATION_PROGRESS or (message.content or '').startswith(
                    ('操作メニュー（終了）','この操作画面は終了しています。','受付しました。','処理中','処理結果を管理チャンネルへ通知します。')) or (
                    '\n完了後に実行記録と操作メニューを表示します。\n受付番号: ' in (message.content or '')):
                await self.disable_menu(message.id)

    async def guard(self,i):
        return await flow(self).admit(i)

    async def accept(self,i,action,pid=None,data=None,already_deferred=False,ui_session=None,listing=('all',0,'')):
        if not authorized(self.store,i.user.id,i.guild_id,i.channel_id):
            return
        if not already_deferred:
            await flow(self).defer(i,thinking=getattr(i,'message',None) is None)
        else:
            i.extras['note_deferred']=True
        if ui_session is not None and ui_session!=flow(self).current():
            return await flow(self).obsolete(i)
        try:
            jid=submit(self.store,action,pid,data,str(i.id),ui_session=ui_session)
            receipt='処理中: '+ACTION_NAMES.get(action,'記事操作')+'\n完了後に実行記録と操作メニューを表示します。\n受付番号: '+jid[:8]
            flow(self).rebase(i)
            message=await flow(self).progress(i,receipt)
            if message is not None and getattr(message,'id',None):
                private=bool(getattr(getattr(message,'flags',None),'ephemeral',False))
                from .secrets import protect
                token=protect(i.token) if private else ''
                self.store.record_receipt(jid,message.id,i.channel_id,
                    getattr(i,'application_id','') if private else '',token)
        except (ValueError,RuntimeError) as exc:
            if ui_session is not None and self.store.setting('discord_active_ui','')!=ui_session:
                await flow(self).obsolete(i)
            else:
                await self.finish_action(i,str(exc),deferred=True)

    async def setup_hook(self):
        self.loading_emoji_error=False
        try:
            await prepare_loading_emoji(self)
        except discord.HTTPException as exc:
            self.loading_emoji_error=True
            Diagnostics(self.store).error('discord_loading_emoji_error',exc,
                                          http_status=exc.status,discord_code=exc.code)
        self.add_view(Menu(self,root=True))
        self.notifications.start()
        self.recovery_presence.start()

    async def close(self):
        self.notifications.cancel()
        self.recovery_presence.cancel()
        if hasattr(self,'ui'):await self.ui.close()
        await super().close()

    @tasks.loop(seconds=3)
    async def recovery_presence(self):
        if not self.is_ready(): return
        from .schedule_activity import service
        reservations=service(self.store)
        reservations.expire()
        await self.check_reservation_presence(reservations)
        state=self.store.setting('remote_relogin_activity',{})
        if not state.get('instance_id') or state.get('disconnected'): return
        config=self.store.setting('discord',{})
        from discord.http import Route
        try:
            instance=await self.http.request(Route('GET','/applications/{application_id}/activity-instances/{instance_id}',
                application_id=config['application'],instance_id=state['instance_id']))
            location=instance.get('location',{})
            present=(str(config['owner']) in [str(user) for user in instance.get('users',[])] and
                str(location.get('channel_id'))==str(config['channel']) and str(location.get('guild_id'))==str(config['guild']))
        except discord.NotFound: present=False
        except (discord.HTTPException,KeyError): return
        current=self.store.setting('remote_relogin_activity',{})
        if current.get('event_id')!=state.get('event_id') or current.get('disconnected'): return
        if present:
            if not state.get('seen'):
                state['seen']=True;self.store.set_setting('remote_relogin_activity',state)
            return
        if state.get('seen') or now_ts()>state['deadline']:
            from .relogin import manager
            await asyncio.to_thread(manager(self.store).disconnected,state['recovery_id'])

    async def check_reservation_presence(self,reservations):
        launch=reservations.current_launch()
        if not launch or not launch['instance'] or now_ts()<launch['expires']-580:
            self.reservation_absence=None
            return
        from discord.http import Route
        config=self.store.setting('discord',{})
        try:
            instance=await self.http.request(Route('GET','/applications/{application_id}/activity-instances/{instance_id}',
                application_id=config['application'],instance_id=launch['instance']))
            present=launch['owner'] in [str(user) for user in instance.get('users',[])]
        except discord.NotFound:
            present=False
        except (discord.HTTPException,KeyError):
            self.reservation_absence=None
            return
        if present:
            self.reservation_absence=None
        elif getattr(self,'reservation_absence',None)==launch['id']:
            self.reservation_absence=None
            reservations.closed(launch['id'])
        else:
            self.reservation_absence=launch['id']

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
        topic=getattr(channel,'topic','') or ''
        previous_bot_channel=topic.startswith('note-stock:') and topic.split(':',2)[1]!=str(info.id)
        def bot_can_view(target):
            return not hasattr(target,'permissions_for') or target.permissions_for(guild.me).view_channel

        replacing=(bool(config.get('replaced_application') or config.get('replacement_category'))
                   or previous_bot_channel or (channel is not None and not bot_can_view(channel)))
        if replacing:
            # Old private channels grant access to the previous Bot. Reuse only
            # a channel already created for this installation on a prior attempt.
            channel=next((item for item in guild.text_channels if item.topic==marker and bot_can_view(item)),None)
            config.pop('menu',None)
        if not channel:
            channel=next((c for c in guild.text_channels if c.topic==marker and bot_can_view(c)),None)
        owner=await guild.fetch_member(int(config['owner']))
        overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),
                    owner:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),
                    guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True,embed_links=True,attach_files=True)}
        category_id=config.get('replacement_category') if replacing else config.get('category')
        category=guild.get_channel(int(category_id or 0))
        if category is not None and not bot_can_view(category):
            category=None
        if category is None and channel is not None and bot_can_view(channel.category):
            category=channel.category
        async def create_private_category():
            category=await guild.create_category('アプリ管理',overwrites=overwrites,reason='本人専用のアプリ管理')
            config['replacement_category' if replacing else 'category']=str(category.id)
            self.store.set_setting('discord',config)
            return category

        if channel:
            # Reapplying identical overwrites requires Manage Roles even though
            # creating this private channel only needs Manage Channels. Reconnect
            # must reuse the verified channel without requesting broader access.
            actual={target.id:value.pair() for target,value in channel.overwrites.items()}
            expected={target.id:value.pair() for target,value in overwrites.items()}
            changes={}
            if category is not None and channel.category_id!=category.id:
                changes['category']=category
            if actual!=expected: changes['overwrites']=overwrites
            if changes:
                try:
                    await channel.edit(**changes,reason='管理チャンネルの本人専用権限を確認')
                except discord.Forbidden as exc:
                    if exc.code!=50001:
                        raise
                    Diagnostics(self.store).error('discord_saved_channel_unavailable',exc,http_status=exc.status,discord_code=exc.code)
                    replacing=True
                    channel=None
                    category=None
                    config.pop('menu',None)
        if not channel:
            if category is None:
                category=await create_private_category()
            channel=await guild.create_text_channel('note管理',category=category,overwrites=overwrites,topic=marker,reason='note投稿管理の本人専用チャンネル')
        config['channel']=str(channel.id)
        if category is not None:
            config['category']=str(category.id)
        config.pop('replaced_application',None)
        config.pop('replacement_category',None)
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
        self.store.set_setting('discord_connection','管理メニュー準備完了'+(
            '（読込アニメーションの準備に失敗。再接続してください。）'
            if getattr(self,'loading_emoji_error',False) else ''))
        flow(self).install(Menu(self,root=True),message_id=config['menu'])
        await NoteClient.disable_older_menus(self,channel)

    @tasks.loop(seconds=3)
    async def notifications(self):
        if not self.is_ready(): return
        self.store.expire_waiting_reservations()
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
        await self.cleanup_receipts()

    async def cleanup_receipts(self):
        for receipt in self.store.finished_receipts():
            try:
                if receipt['webhook_token']:
                    webhook=discord.Webhook.partial(int(receipt['application_id']),unprotect(receipt['webhook_token']),client=self)
                    message=await webhook.fetch_message(int(receipt['message_id']))
                    content=message.content;embeds=message.embeds
                else:
                    message=await self.http.get_message(int(receipt['channel_id']),int(receipt['message_id']))
                    content=message.get('content','');embeds=message.get('embeds',[])
                # A transient screen can never authorize removing a final record
                # or a newer screen that reused its message.
                prefix=ACTION_NAMES.get(receipt['action'],'記事操作')+'.'
                if (content or '').startswith(('受付しました。','処理中',prefix,
                                               '<a:note_loading:',loading_text(self,'処理中'))) and not is_execution_record(content,embeds):
                    if receipt['webhook_token']:await webhook.delete_message(int(receipt['message_id']))
                    else:await self.http.delete_message(int(receipt['channel_id']),int(receipt['message_id']))
                self.store.removed_receipt(receipt['job_id'])
            except discord.NotFound:
                self.store.removed_receipt(receipt['job_id'])
            except (discord.HTTPException,ValueError,OSError):
                self.store.set_setting('discord_connection','受付表示の片付けを再試行します。実行記録は保持しています。')

    async def on_disconnect(self):
        self.store.set_setting('discord_connection','切断・再接続待ち')

    async def on_error(self,event_method,*args,**kwargs):
        # Never allow library tracebacks to dump interaction contents or credentials.
        import sys
        error=sys.exc_info()[1]
        detail=type(error).__name__
        if isinstance(error,discord.HTTPException):
            detail+=f' / HTTP {error.status} / code {error.code}'
        Diagnostics(self.store).error('discord_callback_error',error,callback=event_method,
                                      http_status=getattr(error,'status',None), discord_code=getattr(error,'code',None))
        self.store.set_setting('discord_connection','Discord処理エラー（'+detail+'）。接続とBot権限を確認してください。')


class BotThread(threading.Thread):
    def __init__(self,store):
        super().__init__(daemon=True,name='note-discord')
        self.store=store
        self.client=None
        self.loop=None
        self.stop_requested=threading.Event()
    def run(self):
        for name in ('discord','aiohttp'):
            logger=logging.getLogger(name)
            if not any(isinstance(handler,logging.NullHandler) for handler in logger.handlers):
                logger.addHandler(logging.NullHandler())
            logger.propagate=False
        try:
            token=unprotect(self.store.setting('discord_token',''))
            self.loop=asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.client=NoteClient(self.store)
            if not self.stop_requested.is_set():
                self.loop.run_until_complete(self.client.start(token,reconnect=True))
        except Exception as exc:
            Diagnostics(self.store).error('discord_connection_error',exc)
            self.store.set_setting('discord_connection','接続できません（'+type(exc).__name__+'）。Bot設定を確認してください。')
        finally:
            if self.client and self.loop and not self.loop.is_closed():
                self.loop.run_until_complete(self.client.close())
                self.loop.close()
    def stop(self):
        if self.stop_requested.is_set():
            return
        self.stop_requested.set()
        if self.client and self.loop and not self.loop.is_closed():
            def close_client():
                if not self.client.is_closed():
                    self.loop.create_task(self.client.close())
            try:
                self.loop.call_soon_threadsafe(close_client)
            except RuntimeError:
                pass
