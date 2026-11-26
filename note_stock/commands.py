"""GUI/CLI share the same durable store and posting worker."""
import json
import os
import math
from datetime import datetime

from .publisher import account_id
from .store import now_ts, tags_from_text

COMMANDS = ('status','list','get','create','update','duplicate','delete','move','settings','pause','resume','login','edit','drafts','import','publish','verify','export',
            'sync_articles','save_note_draft','refresh_article','schedule','cancel_schedule','recurring_override','delete_note','publish_note','tags','job','discord')


class Commands:
    def __init__(self, store, window=None):
        self.store, self.window = store, window

    def execute(self, request):
        command = request.get('command')
        data = request.get('data') or {}
        pid = request.get('id')
        if command not in COMMANDS or not isinstance(data, dict):
            raise ValueError('コマンドまたは入力形式が正しくありません。')
        data=dict(data)
        if command=='discord':
            action=data.get('action','status')
            if action not in ('status','setup','enable','disable','menu'):
                raise ValueError('Discordの操作が正しくありません。')
            if action!='status':
                if not self.window:
                    raise ValueError('アプリを起動してからDiscord設定を変更してください。')
                from PySide6.QtCore import QTimer
                panel=self.window.discord_panel
                if action=='menu':
                    import asyncio
                    bot=panel.bot
                    if not bot or not bot.client or not bot.loop or not bot.client.is_ready():
                        raise ValueError('Discord接続の完了を待ってからメニューを開いてください。')
                    pending=asyncio.run_coroutine_threadsafe(bot.client.send_menu(),bot.loop)
                    def completed(future):
                        try: future.result()
                        except Exception:
                            self.store.set_setting('discord_connection','操作メニューを送信できませんでした。接続を確認してください。')
                    pending.add_done_callback(completed)
                elif action=='setup': QTimer.singleShot(0,panel.setup)
                else: QTimer.singleShot(0,lambda:panel.toggle_integration(action=='enable'))
            config=self.store.setting('discord',{})
            return {'ok':True,'result':{'enabled':self.store.setting('discord_enabled',bool(self.store.setting('discord_token',''))),
                'configured':bool(self.store.setting('discord_token','') and config.get('guild')),
                'connection':self.store.setting('discord_connection','未設定'),
                'guild':config.get('guild'),'channel':config.get('channel'),'accepted':action!='status'},'at':now_ts()}
        if command=='job':
            return {'ok':True,'result':self.store.job(pid),'at':now_ts()}
        if command=='recurring_override':
            if not pid:
                raise ValueError('記事を指定してください。')
            self.store.get(pid)
            if 'day' in data:
                if data['day'] is not None and not isinstance(data['day'],str):
                    raise ValueError('差し込み日は YYYY-MM-DD で指定してください。')
                self.store.set_recurring_override(pid,data['day'])
            return {'ok':True,'result':{'day':self.store.recurring_override(pid)},'at':now_ts()}
        if 'due' in data:
            due=data['due']
            if isinstance(due,str):
                parsed=datetime.fromisoformat(due)
                if not parsed.tzinfo:
                    raise ValueError('予約日時はタイムゾーンを含めてください。')
                due=parsed.timestamp()
            if due is not None and (type(due) not in (int,float) or not math.isfinite(due)):
                raise ValueError('予約日時は有効な日時を指定してください。')
            data['due']=due
        from .remote import ACTIONS,submit
        if command in ACTIONS-{'pause','resume'}:
            jid=submit(self.store,command,pid,data,request.get('request_key'))
            return {'ok':True,'result':{'accepted':True,'job_id':jid,'requires_app_running':True},'at':now_ts()}
        if self.window and self.window.dirty and command not in {'status','list','get','export','pause','resume'}:
            raise ValueError('GUIで編集中の原稿を保存してから実行してください。')
        if command == 'status':
            result = {'paused':self.store.setting('paused',True),'account':self.store.setting('account',''),
                      'busy':self.window.busy() if self.window else False,'pid':os.getpid(),'posts':self.store.posts()}
        elif command in {'list','export'}:
            result = self.store.posts()
        elif command == 'get':
            result = self.store.get(pid)
        elif command in {'create','update'}:
            if command == 'create':
                pid = self.store.create()
            old = self.store.get(pid)
            image = self.store.import_image(data['image_file']) if data.get('image_file') else data.get('image',old['image'])
            tags = data.get('tags')
            tags = tags_from_text(tags) if isinstance(tags,str) else tags
            due = data.get('due',old['due'])
            if isinstance(due,str):
                parsed = datetime.fromisoformat(due)
                if not parsed.tzinfo:
                    raise ValueError('予約日時は +09:00 などのタイムゾーンを含めてください。')
                due = parsed.timestamp()
            desired=data.get('status',old['status'] if old['status'] in {'draft','queued','scheduled'} else 'draft')
            if desired not in {'draft','queued','scheduled'}:
                raise ValueError('保存方法が正しくありません。')
            changes={field:data[field] for field in ('title','body') if field in data}
            if 'tags' in data: changes['tags']=tags
            if 'image' in data or data.get('image_file'): changes['image']=image
            if 'status' in data:
                changes['status']='draft' if not old['draft_key'] and old['editor_kind']=='native' else desired
            if 'due' in data or desired!='scheduled': changes['due']=due if desired=='scheduled' else None
            self.store.save_fields(pid,changes,tags_revision=data.get('tags_revision',old['tags_revision']),expected=old)
            result = self.store.get(pid)
            if not old['draft_key'] and self.store.setting('account_verified',False):
                result['job_id']=submit(self.store,'save_note_draft',pid,{'due':due if desired=='scheduled' else None,'queue':desired=='queued'})
            result['note_saved']=False
        elif command == 'duplicate':
            result = self.store.get(self.store.create(self.store.get(pid)))
            if self.store.setting('account_verified',False):
                result['job_id']=submit(self.store,'save_note_draft',result['id'])
            result['note_saved']=False
        elif command == 'delete':
            if self.store.get(pid)['draft_key']:
                raise ValueError('note記事は delete_note で対象の更新時刻を確認して削除してください。')
            self.store.delete(pid)
            result = {'id':pid,'deleted':True}
        elif command == 'move':
            direction = data.get('direction')
            if direction not in (-1,1):
                raise ValueError('direction は -1 または 1 にしてください。')
            self.store.reorder(pid,direction)
            result = self.store.posts()
        elif command == 'settings':
            from .store import DEFAULT_ARTICLE_SYNC_MINUTES, MIN_ARTICLE_SYNC_MINUTES, MAX_ARTICLE_SYNC_MINUTES
            allowed = {'account','favorite_tags','recurrence','diagnostics','browser','note_login_method','article_sync_minutes'}
            if set(data)-allowed:
                raise ValueError('未対応の設定があります。')
            if 'note_login_method' in data and data['note_login_method'] not in ('google','password'):
                raise ValueError('noteのログイン方法が正しくありません。')
            if 'article_sync_minutes' in data:
                minutes=data['article_sync_minutes']
                if type(minutes) is not int or not MIN_ARTICLE_SYNC_MINUTES<=minutes<=MAX_ARTICLE_SYNC_MINUTES:
                    raise ValueError('記事一覧の自動同期間隔は1〜1440分で指定してください。')
            if 'browser' in data:
                from .managed_browser import CHOICES
                if data['browser'] not in CHOICES:
                    raise ValueError('ブラウザーの設定が正しくありません。')
                if data['browser'] != self.store.setting('browser','auto'):
                    if self.window and self.window.busy():
                        raise ValueError('処理中はブラウザーを変更できません。')
                    data.update(paused=True,account_verified=False)
            if 'account' in data:
                data['account'] = account_id(data['account'])
                if data['account'] != self.store.setting('account',''):
                    if self.window and self.window.busy():
                        raise ValueError('処理中はアカウントを変更できません。')
                    data.update(paused=True,account_verified=False)
            if 'recurrence' in data:
                schedule=data['recurrence']
                datetime.strptime(schedule['time'],'%H:%M')
                if not schedule['days'] or any(type(day) is not int or not 0<=day<7 for day in schedule['days']):
                    raise ValueError('曜日は月曜0〜日曜6の一覧にしてください。')
            if 'favorite_tags' in data:
                raw=data['favorite_tags']
                data['favorite_tags']=tags_from_text(raw if isinstance(raw,str) else ' '.join(raw))
            if 'diagnostics' in data and type(data['diagnostics']) is not bool:
                raise ValueError('diagnostics は true / false にしてください。')
            results=self.store.settings_batch(data)
            if any(results.values()):
                raise ValueError('一部保存失敗: '+json.dumps(results,ensure_ascii=False))
            result={key:self.store.setting(key) for key in allowed}
            result['article_sync_minutes']=self.store.setting('article_sync_minutes',DEFAULT_ARTICLE_SYNC_MINUTES)
        elif command in {'pause','resume'}:
            if command=='resume' and not self.store.setting('account_verified',False):
                raise ValueError('先にログイン・接続確認を行ってください。')
            self.store.set_setting('paused',command=='pause')
            if self.window and command=='resume':
                self.window.timer.start(1000)
            result={'paused':command=='pause','requires_app_running':True}
        else:
            if self.window and self.window.busy():
                raise ValueError('別のブラウザー処理が実行中です。')
            expected=''
            post=None
            if command in {'drafts','import'}:
                if not self.store.setting('account_verified',False):
                    raise ValueError('先にログイン・接続確認を行ってください。')
            elif command=='login':
                expected=account_id(data.get('account') or self.store.setting('account',''))
            else:
                post=self.store.get(pid)
                if command=='edit':
                    if not self.store.setting('account_verified',False):
                        raise ValueError('先にログイン・接続確認を行ってください。')
                    post=self.store.begin_native_edit(pid)
                elif command=='publish':
                    if not self.store.setting('account_verified',False):
                        raise ValueError('先にログイン・接続確認を行ってください。')
                    post=self.store.claim(now_ts(),post_id=pid)
                    if not post:
                        raise ValueError('別の投稿が実行中です。')
                elif not post['draft_key']:
                    raise ValueError('保存済みのnote下書きIDがありません。')
            action='publish' if command=='publish' else command
            due=data.get('due')
            if isinstance(due,str):
                parsed=datetime.fromisoformat(due)
                if not parsed.tzinfo:
                    raise ValueError('予約日時はタイムゾーンを含めてください。')
                due=parsed.timestamp()
            try:
                if self.window:
                    self.window.start_worker(action,post,expected,items=data.get('keys',[]),due=due)
                    result={'accepted':True,'id':pid,'action':action}
                else:
                    from .worker import BrowserWorker
                    worker=BrowserWorker(self.store,action,post,expected)
                    worker.items=data.get('keys',[])
                    worker.import_due=due
                    outcomes=[]
                    payloads=[]
                    worker.data.connect(payloads.append)
                    worker.outcome.connect(lambda status,message,url:outcomes.append({'status':status,'message':message,'url':url,'id':pid}))
                    worker.run()
                    result=outcomes[-1]
                    if payloads:
                        result['data']=payloads[-1]
                    if result['status'] not in {'connected','published','prepared','native_reserved','native_private','listed','imported'}:
                        return {'ok':False,'result':result,'at':now_ts()}
            except Exception as exc:
                if post and not (self.window and self.window.busy()):
                    current=self.store.get(post['id'])
                    if current['status']=='posting':
                        self.store.finish(post['id'],'unknown' if current['submitted'] else 'failed',
                                          '処理を開始・完了できませんでした（'+type(exc).__name__+'）。')
                    elif current['status']=='editing':
                        self.store.native_edit_failed(post['id'],'編集処理を開始できませんでした。')
                    self.store.set_setting('paused',True)
                raise
        if self.window and command not in {'status','list','get','export'}:
            if command=='update' and self.window.current_id==pid:
                self.window.load(pid)
            if command=='delete' and self.window.current_id==pid:
                self.window.load(None)
            self.window.refresh()
        return {'ok':True,'result':result,'at':now_ts()}

    def handle(self, request):
        try:
            return self.execute(request)
        except Exception as exc:
            return {'ok':False,'error':str(exc),'at':now_ts()}
