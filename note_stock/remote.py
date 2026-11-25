"""Qt-thread dispatcher shared by Discord, GUI and CLI job submissions."""
import json
from PySide6.QtCore import QObject, QTimer
from .store import now_ts, tags_from_text
from .worker import BrowserWorker

SUCCESS={'saved','deleted','synced','published','prepared','connected','cancelled'}
ACTIONS={'sync_articles','save_note_draft','refresh_article','schedule','cancel_schedule','publish_note','delete_note','tags','pause','resume','relogin_note'}


def submit(store,action,pid=None,data=None,key=None,ui_session=None):
    if action not in ACTIONS:
        raise ValueError('未対応の操作です。')
    data=dict(data or {})
    if action=='relogin_note': raise ValueError('遠隔再ログインは本人専用の復旧メニューから開始してください。')
    if action not in {'pause','resume','sync_articles'}:
        if action!='cancel_schedule' and not store.setting('account_verified',False):
            raise ValueError('PCの設定からnoteへのログインを確認してください。')
        post=store.get(pid)
        if action=='schedule':
            store.require_schedulable(pid)
        if post['status'] in {'posting','editing','unknown'}:
            raise ValueError('処理中または結果未確認です。')
        if action in {'delete_note','publish_note'} or (action=='schedule' and 'confirmed_updated' in data):
            if data.get('confirmed_updated')!=post['updated']:
                raise ValueError('記事が変更されました。最新の内容で確認し直してください。')
        if action in {'publish_note','schedule','tags'} and not post['native_ready']:
            raise ValueError('先に「本文・公開条件を読み込む」でnoteの記事を確認してください。')
        if action in {'publish_note','schedule'} and post['status']=='review':
            raise ValueError('note側の変更があります。「内容・公開条件を読み直す」で確認してください。')
        if action=='tags':
            data['tags']=tags_from_text(data.get('tags',''))
        if action=='schedule':
            import math
            due=data.get('due')
            if type(due) not in (int,float) or not math.isfinite(due) or due<=now_ts():
                raise ValueError('未来の日時を指定してください。')
    return store.enqueue(action,pid,data,key,ui_session=ui_session)


class RemoteController(QObject):
    def __init__(self,window):
        super().__init__(window)
        self.window=window
        self.store=window.store
        self.current=None
        self.worker=None
        self.background_sync=False
        self.last_sync=now_ts()
        self.store.recover_jobs()
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)

    def tick(self):
        if (self.window.quitting or self.window.busy() or self.window.dirty or self.current
                or getattr(self.window,'pending_edit_id',None)):
            return
        if self.store.setting('account_verified',False) and now_ts()-self.last_sync>=60:
            self.store.enqueue('sync_articles',request_key='sync:'+str(int(now_ts()//60)))
            self.last_sync=now_ts()
        job=self.store.next_job(browser_allowed=self.store.setting('account_verified',False))
        if not job:
            return
        self.current=job
        self.background_sync=job['action']=='sync_articles' and job['request_key'].startswith('sync:')
        from .diagnostics import Diagnostics
        warning='' if self.background_sync else Diagnostics(self.store).record('remote_start',job_id=job['id'],action=job['action'],post_id=job['post_id'])
        if warning: self.window.notify(warning,True)
        try:
            action=job['action']
            pid=job['post_id']
            data=job['data']
            if action=='relogin_note':
                from .relogin import available
                ticket=self.store.setting('remote_relogin_ticket',{})
                if not available(self.store) or data.get('recovery_id')!=ticket.get('id') or ticket.get('expires',0)<=now_ts():
                    raise ValueError('遠隔再ログインの接続が失効しました。Discordからやり直してください。')
                self.store.set_setting('paused',True)
                self.worker=BrowserWorker(self.store,'relogin',expected=self.store.setting('account',''),parent=self)
                self.worker.recovery_id=ticket['id']
                self.window.worker=self.worker
                self.worker.progress.connect(lambda msg:self.window.statusBar().showMessage(msg))
                self.worker.outcome.connect(self.finish)
                self.worker.finished.connect(self.worker_done)
                self.worker.start(); self.window.refresh(); return
            if action in {'pause','resume'}:
                if action=='resume' and not self.store.setting('account_verified',False):
                    raise ValueError('note接続を確認してください。')
                self.store.set_setting('paused',action=='pause')
                self.window.timer.start(1000)
                self.finish('saved','自動投稿を'+('停止' if action=='pause' else '再開')+'しました。','')
                return
            if action=='cancel_schedule':
                self.store.set_schedule(pid,None)
                self.finish('saved','予約を取り消しました。noteの記事は残っています。','')
                return
            post=self.store.get(pid) if pid else None
            if (action in {'publish_note','delete_note'} or (action=='schedule' and 'confirmed_updated' in data)) and data.get('confirmed_updated')!=post['updated']:
                raise ValueError('受付後に記事が変更されました。もう一度確認してください。')
            if action=='save_note_draft' and not post['draft_key']:
                # A failed first save keeps this durable buffer, never creates a second one on retry.
                if not post['title'].strip() or not post['body'].strip():
                    raise ValueError('タイトルと本文を入力してください。')
            if action=='schedule':
                self.store.require_schedulable(pid)
                action='prepare_schedule'
            elif action=='publish_note':
                action='prepare_publish'
            elif action=='tags':
                action='prepare_tags'
            elif action=='refresh_article':
                action='prepare_article'
            self.worker=BrowserWorker(self.store,action,post,parent=self)
            self.worker.headless_sync=self.background_sync
            self.worker.items=['queue'] if data.get('queue') else data.get('tags',[])
            self.worker.import_due=data.get('due')
            self.window.worker=self.worker
            if not self.background_sync:
                self.worker.progress.connect(lambda msg:self.window.statusBar().showMessage(msg))
            self.worker.outcome.connect(self.finish)
            self.worker.finished.connect(self.worker_done)
            self.worker.start()
            if not self.background_sync:
                self.window.refresh()
        except Exception as exc:
            Diagnostics(self.store).error('remote_dispatch_error',exc,action=job['action'])
            self.finish('failed',str(exc),'')

    def finish(self,status,message,url):
        if not self.current:
            return
        automatic=self.background_sync
        if automatic:
            previous=self.store.setting('background_sync_error','')
            error='' if status in SUCCESS else message
            self.store.set_setting('background_sync_error',error)
            if error and error!=previous:
                self.window.statusBar().showMessage(error)
        self.store.complete_job(self.current['id'],status in SUCCESS,message,url,notify=not automatic)
        if self.current['action']=='relogin_note':
            self.store.set_setting('paused',True)
            from .relogin import manager
            manager(self.store).finished(self.current['data'].get('recovery_id'))
            if status=='connected': self.store.enqueue('sync_articles')
        from .diagnostics import Diagnostics
        log=Diagnostics(self.store)
        if status not in SUCCESS:
            log.error('remote_job_error',job_id=self.current['id'],action=self.current['action'],status=status)
        warning='' if automatic else log.record('remote_finish',job_id=self.current['id'],status=status)
        if warning: self.window.notify(warning,True)
        if not automatic:
            self.window.notify(message,status not in SUCCESS)
        pid=self.current['post_id']
        if pid and pid==self.window.current_id and not self.window.dirty:
            try:
                self.store.get(pid)
                self.window.load(pid)
            except ValueError:
                self.window.load(None)
        self.current=None
        if automatic:
            self.window.refresh_list()
        else:
            self.window.refresh()

    def worker_done(self):
        if not self.background_sync:
            self.window.refresh()
        if self.window.quitting:
            self.window.finish_quit()
