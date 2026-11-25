"""Single owner of Discord screen generations and message writes.

Constructing a view never activates it. Only a successful render commits a
screen. Obsolete inputs acknowledge silently and cannot edit the current UI.
"""
import asyncio
import json
import uuid
import discord
from .loading_indicator import loading_text


def authorized(store,user,guild,channel):
    c=store.setting('discord',{})
    return bool(c.get('owner') and c.get('guild') and c.get('channel') and
        str(user)==str(c['owner']) and str(guild)==str(c['guild']) and str(channel)==str(c['channel']))


def flow(client):
    if not hasattr(client,'ui'): client.ui=DiscordUI(client)
    return client.ui


class DiscordUI:
    def __init__(self,client):
        self.client=client;self.store=client.store;self.lock=asyncio.Lock()
        self.screen_message=None
        self.animations={}
        if self.store.setting('discord_active_ui') is None:
            self.store.set_setting('discord_active_ui',uuid.uuid4().hex)

    def current(self): return self.store.setting('discord_active_ui','')

    def state(self):
        state=self.store.setting('discord_ui_state',{})
        if state.get('generation')!=self.current():
            return {**state,'generation':self.current(),'phase':'busy'}
        return state

    def capture(self,i):
        if not hasattr(i,'extras'): i.extras={}
        i.extras.setdefault('note_expected_ui',self.current())
        return i.extras['note_expected_ui']

    def valid(self,i,session=None):
        expected=session if session is not None else getattr(i,'extras',{}).get('note_expected_ui')
        return expected is None or expected==self.current()

    @staticmethod
    def responded(i):
        if getattr(i,'extras',{}).get('note_deferred'): return True
        check=getattr(i.response,'is_done',None)
        return bool(check()) if check else False

    async def defer(self,i,*,thinking=False,show_loading=True):
        if not hasattr(i,'extras'): i.extras={}
        i.extras['note_thinking']=thinking
        if not self.responded(i): await i.response.defer(ephemeral=True,thinking=thinking)
        i.extras['note_deferred']=True
        if show_loading and self.valid(i):
            await i.edit_original_response(content=loading_text(self.client),
                                           view=None,embeds=[],attachments=[])

    async def obsolete(self,i):
        # A modal may refer to the SAME message now containing a newer menu.
        # Never delete/edit that source, and never append a stale-input notice.
        if not self.responded(i):
            await self.defer(i,thinking=getattr(i,'message',None) is None,show_loading=False)
        if getattr(i,'extras',{}).get('note_thinking'):
            delete=getattr(i,'delete_original_response',None)
            if delete:
                try: await delete()
                except discord.HTTPException: pass
        return False

    async def admit(self,i,*,session=None,view=None):
        if not authorized(self.store,i.user.id,i.guild_id,i.channel_id):
            await i.response.send_message('この操作は登録した本人専用です。',ephemeral=True)
            return False
        if view is not None:
            source_id=str(getattr(getattr(i,'message',None),'id',''))
            state=self.state()
            if view.root:
                menu=str(self.store.setting('discord',{}).get('menu',''))
                allowed=source_id==menu and bool(menu) and (not state.get('kind') or state.get('kind')=='Menu')
            else:
                ids=state.get('message_ids',[state.get('message_id','')])
                allowed=view.session==self.current() and (not state.get('message_id') or source_id in ids)
            if state.get('phase') in {'building','activity'}: return await self.obsolete(i)
            if not allowed:
                await self.obsolete(i)
                # Remove controls only on a genuinely older message. A stale
                # callback for the current message must not erase its new UI.
                if source_id not in state.get('message_ids',[str(state.get('message_id',''))]) and source_id!=str(self.store.setting('discord',{}).get('menu','')):
                    source=getattr(i,'message',None)
                    if source:
                        from .discord_bot import remove_old_controls
                        await remove_old_controls(source)
                return False
        if self.state().get('phase')=='building': return await self.obsolete(i)
        if session is not None and session!=self.current(): return await self.obsolete(i)
        self.capture(i)
        if not hasattr(i,'extras'): i.extras={}
        i.extras['note_ui_session']=self.current()
        return True

    def install(self,view,message=None,message_id=None):
        """Commit a successfully rendered screen, atomically with queue token."""
        for task in self.animations.values():task.cancel()
        self.animations.clear()
        mid=message_id if message_id is not None else getattr(message,'id',None)
        state={'generation':view.session,'message_id':str(mid or ''),'message_ids':[str(mid or '')],
               'kind':type(view).__name__,'phase':'ready'}
        config=self.store.setting('discord',{})
        config['menu']=str(mid or '') if view.root else ''
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for key,value in [('discord_active_ui',view.session),('discord_ui_state',state),('discord',config)]:
                c.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(key,json.dumps(value)))
        view.message=message;view.message_id=mid
        self.screen_message=message
        if not hasattr(self.client,'operation_views'): self.client.operation_views=set()
        self.client.operation_views.add(view)

    async def close(self):
        tasks=list(self.animations.values())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        self.animations.clear()

    def rebase(self,i):
        # Only the caller that atomically claimed a queue token uses this.
        i.extras['note_expected_ui']=self.current()

    async def render(self,i,method,content,view,**kwargs):
        expected=self.capture(i)
        async with self.lock:
            if expected!=self.current(): return await self.obsolete(i)
            source=getattr(i,'message',None)
            content=getattr(view,'list_text',None) or content
            kwargs.update(content=content,view=view if view.children else None,embeds=getattr(view,'embeds',[]))
            attachments=view.make_attachments() if hasattr(view,'make_attachments') else []
            if source is not None or method in {'deferred','edit_message'}:kwargs['attachments']=attachments
            elif attachments:kwargs['files']=attachments
            editing=False
            anchor=self.screen_message
            if source is not None and anchor is not None and str(source.id)!=str(anchor.id) and str(source.id) in self.state().get('message_ids',[]):
                # Article rows belong to one screen. Always edit its anchor,
                # then retire every row, including the one that was clicked.
                # This also avoids replies pointing at a deleted source.
                await self.defer(i)
                kwargs.pop('ephemeral',None);kwargs.pop('wait',None)
                message=await anchor.edit(**kwargs);source=anchor;editing=True
            elif method=='deferred' or (method=='followup' and source is not None):
                kwargs.pop('ephemeral',None);kwargs.pop('wait',None)
                message=await i.edit_original_response(**kwargs)
                editing=not getattr(i,'extras',{}).get('note_thinking') and source is not None
            elif source is not None or method=='edit_message':
                kwargs.pop('ephemeral',None);kwargs.pop('wait',None)
                await i.response.edit_message(**kwargs);message=source;editing=True
            elif method=='followup':
                message=await i.followup.send(**kwargs,wait=True)
            else:
                await i.response.send_message(**kwargs)
                message=await i.original_response() if hasattr(i,'original_response') else None
            # Another owner action can arrive during Discord's network await.
            # It must not be overwritten by this late response.
            if expected!=self.current():
                view.stop()
                if message is not None and not editing:
                    try: await message.delete()
                    except discord.HTTPException: pass
                return None
            old_menu=self.store.setting('discord',{}).get('menu')
            mid=getattr(source,'id',None) if editing else getattr(message,'id',None)
            self.install(view,message,mid)
            parts=getattr(view,'parts',[])
            if parts:
                state=self.state();state['phase']='building';self.store.set_setting('discord_ui_state',state)
            # Subsequent work in this interaction belongs to the screen it rendered.
            i.extras['note_expected_ui']=view.session
            # Keep one authoritative active message, including root menus.
            await self.client.retire_views(view)
            if old_menu and str(old_menu)!=str(mid): await self.client.disable_menu(old_menu)
            if parts:
                for part in parts:
                    if view.session!=self.current(): break
                    part.session=view.session
                    extra=await i.followup.send(content=None if isinstance(part,discord.ui.LayoutView) else part.text,view=part,wait=True,
                        ephemeral=bool(getattr(getattr(message,'flags',None),'ephemeral',kwargs.get('ephemeral',False))),
                        allowed_mentions=discord.AllowedMentions.none())
                    if view.session!=self.current():
                        part.stop()
                        try: await extra.delete()
                        except discord.HTTPException: pass
                        break
                    part.message=extra;part.message_id=extra.id
                    self.client.operation_views.add(part)
                    state=self.state();state['message_ids'].append(str(extra.id))
                    self.store.set_setting('discord_ui_state',state)
                if view.session==self.current():
                    state=self.state();state['phase']='ready';self.store.set_setting('discord_ui_state',state)
            return message

    def expire(self,view):
        if view.session!=self.current(): return False
        self.store.set_setting('discord_active_ui','expired:'+uuid.uuid4().hex)
        return True

    async def form(self,i,modal):
        if not self.valid(i): return await self.obsolete(i)
        # Closing a native Discord form sends no event; the source is kept
        # usable, with its generation unchanged, until a submission is accepted.
        modal.session=self.current()
        await i.response.send_modal(modal)

    async def activity(self,i,pid):
        """Reservation entry points share launch, ownership and control retirement."""
        from .schedule_activity import service
        async with self.lock:
            if not self.valid(i):
                await self.obsolete(i)
                return True
            api=service(self.store);lid=None
            try:
                lid=api.prepare(pid,i.user.id,self.current())
                response=await i.response.launch_activity()
                instance=getattr(response,'activity_id',None) or getattr(getattr(response,'resource',None),'id',None)
                api.bind(lid,instance)
            except Exception as exc:
                if lid:
                    with self.store.connect() as c:c.execute("UPDATE activity_reservations SET state='cancelled' WHERE id=?",(lid,))
                if not self.responded(i):
                    return False
                self.store.notification(False,str(exc) if isinstance(exc,ValueError) else '予約画面を開けませんでした。Discord接続を確認してください。',key='activity-launch:'+str(i.id))
                return True
            state=self.state();state['phase']='activity';self.store.set_setting('discord_ui_state',state)
            for mid in state.get('message_ids',[state.get('message_id')]):
                if mid:await self.client.disable_menu(mid)
            return True

    async def progress(self,i,text):
        from .discord_bot import GuardView
        view=GuardView(self.client,timeout=None)
        if not self.responded(i): await self.defer(i,thinking=getattr(i,'message',None) is None)
        return await self.render(i,'deferred',loading_text(self.client,text),view,
                                 allowed_mentions=discord.AllowedMentions.none())

    async def finish(self,i,message,ok=False):
        if not self.valid(i): return await self.obsolete(i)
        self.store.notification(ok,message,key='interaction:'+str(i.id))
        await self.progress(i,'処理結果を管理チャンネルへ通知します。')


class ManagedModal(discord.ui.Modal):
    """All forms pass through the same gate and failure/finalization path."""
    def __init_subclass__(cls,**kwargs):
        super().__init_subclass__(**kwargs)
        if {'on_submit','on_error'} & cls.__dict__.keys():
            raise TypeError('Form lifecycle belongs to ManagedModal; implement handle_submit')
    def __init__(self,client,**kwargs):
        super().__init__(**kwargs);self.client=client;self.session=flow(client).current()
    async def on_submit(self,i):
        ui=flow(self.client)
        if not await ui.admit(i,session=self.session): return
        try: await self.handle_submit(i)
        except Exception as exc:
            message=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else '操作を完了できませんでした（'+type(exc).__name__+'）。'
            await ui.finish(i,message)
    async def on_error(self,i,error):
        await flow(self.client).finish(i,'入力の処理を完了できませんでした（'+type(error).__name__+'）。')
