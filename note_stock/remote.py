"""Qt-thread dispatcher shared by Discord, GUI and CLI job submissions."""
import json
from PySide6.QtCore import QObject, QTimer
from .store import now_ts, tags_from_text
from .worker import BrowserWorker

SUCCESS={'saved','deleted','synced','published','prepared','connected'}
ACTIONS={'sync_articles','save_note_draft','refresh_article','schedule','cancel_schedule','publish_note','delete_note','tags','pause','resume'}


def submit(store,action,pid=None,data=None,key=None,ui_session=None):
    if action not in ACTIONS:
        raise ValueError('未対応の操作です。')
    data=dict(data or {})
    if action not in {'pause','resume','sync_articles'}:
        if action!='cancel_schedule' and not store.setting('account_verified',False):
            raise ValueError('PCの設定からnoteへのログインを確認してください。')
        post=store.get(pid)
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
        self.last_sync=now_ts()
        self.store.recover_jobs()
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)

    def tick(self):
        if self.window.quitting or self.window.busy() or self.window.dirty or self.current:
            return
        if self.store.setting('account_verified',False) and now_ts()-self.last_sync>=60:
            self.store.enqueue('sync_articles',request_key='sync:'+str(int(now_ts()//60)))
            self.last_sync=now_ts()
        job=self.store.next_job(browser_allowed=self.store.setting('account_verified',False))
        if not job:
            return
        self.current=job
        from .diagnostics import Diagnostics
        warning=Diagnostics(self.store).record('remote_start',job_id=job['id'],action=job['action'],post_id=job['post_id'])
        if warning: self.window.notify(warning,True)
        try:
            action=job['action']
            pid=job['post_id']
            data=job['data']
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
                action='prepare_schedule'
            elif action=='publish_note':
                action='prepare_publish'
            elif action=='tags':
                action='prepare_tags'
            elif action=='refresh_article':
                action='prepare_article'
            self.worker=BrowserWorker(self.store,action,post,parent=self)
            self.worker.items=['queue'] if data.get('queue') else data.get('tags',[])
            self.worker.import_due=data.get('due')
            self.window.worker=self.worker
            self.worker.progress.connect(lambda msg:self.window.statusBar().showMessage(msg))
            self.worker.outcome.connect(self.finish)
            self.worker.finished.connect(self.worker_done)
            self.worker.start()
            self.window.refresh()
        except Exception as exc:
            self.finish('failed',str(exc),'')

    def finish(self,status,message,url):
        if not self.current:
            return
        automatic=self.current['request_key'].startswith('sync:') and status in SUCCESS
        self.store.complete_job(self.current['id'],status in SUCCESS,message,url,notify=not automatic)
        from .diagnostics import Diagnostics
        warning=Diagnostics(self.store).record('remote_finish',job_id=self.current['id'],status=status,database=str(self.store.db),table='jobs')
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
        self.window.refresh()

    def worker_done(self):
        self.window.refresh()
        if self.window.quitting:
            self.window.finish_quit()
