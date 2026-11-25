"""Qt-thread dispatcher shared by Discord, GUI and CLI job submissions."""
import json
from PySide6.QtCore import QObject, QTimer
from .store import DEFAULT_ARTICLE_SYNC_MINUTES, MIN_ARTICLE_SYNC_MINUTES, now_ts, tags_from_text
from .worker import BrowserWorker

SUCCESS={'saved','deleted','synced','published','prepared','connected','cancelled'}
ACTIONS={'sync_articles','save_note_draft','refresh_article','preview_article','schedule','cancel_schedule','publish_note','delete_note','tags','pause','resume','relogin_note'}


def request_selected_article(store,pid,*,background=False):
    """Read only an explicitly selected, unread note; coalesce repeated clicks."""
    post=store.get(pid)
    if not post['draft_key'] or post['native_ready'] or post['status'] in {'posting','editing','unknown'}:
        return None
    with store.connect() as c:
        pending=c.execute("SELECT id FROM jobs WHERE post_id=? AND action IN ('refresh_article','schedule') AND state IN ('waiting','running') LIMIT 1",(pid,)).fetchone()
    if pending:
        return pending['id']
    return submit(store,'refresh_article',pid,{'selection_read':True,'background_read':background})


def submit(store,action,pid=None,data=None,key=None,ui_session=None):
    if action not in ACTIONS:
        raise ValueError('未対応の操作です。')
    data=dict(data or {})
    if action=='relogin_note': raise ValueError('遠隔再ログインは本人専用の復旧メニューから開始してください。')
    if action not in {'pause','resume','sync_articles'}:
        if action!='cancel_schedule' and not store.setting('account_verified',False):
            raise ValueError('PCの設定からnoteへのログインを確認してください。')
        post=store.get(pid)
        if action in {'preview_article','refresh_article','tags','publish_note'} and not post['draft_key']:
            raise ValueError('先に記事をnoteへ保存してください。')
        if action=='schedule':
            store.require_schedulable(pid)
            if not post['draft_key']:
                raise ValueError('先にnoteへ下書きを保存してください。')
        if post['status'] in {'posting','editing','unknown'}:
            raise ValueError('処理中または結果未確認です。')
        if action in {'delete_note','publish_note'} or (action=='schedule' and 'confirmed_updated' in data):
            if data.get('confirmed_updated')!=post['updated']:
                raise ValueError('記事が変更されました。最新の内容で確認し直してください。')
        if action=='publish_note' and not post['native_ready']:
            raise ValueError('先に「本文・公開条件を読み込む」でnoteの記事を確認してください。')
        if action=='publish_note' and post['status']=='review':
            check=store.job(data['publish_check']) if data.get('publish_check') else None
            if not (check and check['post_id']==pid and check['state']=='done' and
                    check['request_key'].startswith('publish-preflight:') and check['updated']>=post['updated']):
                raise ValueError('note側の変更があります。「今すぐ投稿・更新」から公開条件を確認してください。')
        if action=='tags':
            data['tags']=tags_from_text(data.get('tags',''))
        if action=='schedule' or (action=='save_note_draft' and data.get('due') is not None):
            import math
            due=data.get('due')
            if type(due) not in (int,float) or not math.isfinite(due) or due<=now_ts():
                raise ValueError('未来の日時を指定してください。')
    return store.enqueue(action,pid,data,key,ui_session=ui_session)


class RemoteController(QObject):
    def __init__(self,window,*,catalog_import_enabled=True):
        super().__init__(window)
        self.window=window
        self.store=window.store
        self.current=None
        self.worker=None
        self.background_sync=False
        self.catalog_import_enabled=catalog_import_enabled
        self.catalog_import_post_id=None
        self.next_catalog_import_at=0
        self.last_sync=now_ts()
        self.store.recover_jobs()
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)

    def tick(self):
        if self.window.busy() and getattr(self.worker,'catalog_import',False):
            # Reading the catalog must yield to an explicit save/reservation.
            with self.store.connect() as c:
                pending=c.execute("SELECT 1 FROM jobs WHERE state='waiting' AND request_key NOT LIKE 'sync:%' LIMIT 1").fetchone()
            if pending:
                self.worker.cancel.set()
        if (self.window.quitting or self.window.busy() or self.current
                or getattr(self.window,'pending_edit_id',None)):
            return
        stamp=now_ts()
        connected=self.store.setting('account_verified',False)
        elapsed=stamp-self.last_sync
        if connected and elapsed>=MIN_ARTICLE_SYNC_MINUTES*60:
            interval=self.store.setting('article_sync_minutes',DEFAULT_ARTICLE_SYNC_MINUTES)*60
            if elapsed>=interval:
                self.store.enqueue('sync_articles',request_key=f'sync:interval:{int(stamp)}')
                self.last_sync=stamp
        if self.window.dirty:
            job=self.store.next_job(browser_allowed=connected,editing_locally=True)
        else:
            job=self.store.next_job(browser_allowed=connected)
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
            publish_check=action=='refresh_article' and job['request_key'].startswith('publish-preflight:')
            if action=='relogin_note':
                from .relogin import available
                ticket=self.store.setting('remote_relogin_ticket',{})
                if not available(self.store) or data.get('recovery_id')!=ticket.get('id') or ticket.get('expires',0)<=now_ts():
                    raise ValueError('遠隔再ログインの接続が失効しました。Discordからやり直してください。')
                self.store.set_setting('paused',True)
                self.worker=BrowserWorker(self.store,'relogin',expected=self.store.setting('account',''),parent=self)
                self.worker.recovery_id=ticket['id']
                self.window.worker=self.worker
                self.worker.progress.connect(self.window.note_progress)
                self.worker.action_required.connect(self.window.note_action_required)
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
            if data.get('queue_only'):
                if post['status']!='queued':
                    raise ValueError('この記事はキューから外れています。')
                self.store.set_recurring_override(pid,data['interrupt_day'])
                self.finish('saved','指定日の投稿に割り込み、後続を翌日以降へ送りました。','')
                return
            if data.get('draft_queue'):
                if post['status']!='draft':
                    raise ValueError('受付後に下書きの状態が変わりました。記事を確認してください。')
                self.store.require_schedulable(pid)
            if publish_check or action in {'schedule','publish_note','tags'} or data.get('draft_queue'):
                if not connected:
                    raise ValueError('PCの設定でnoteへのログインを確認してから、やり直してください。')
                if self.window.dirty and pid==self.window.current_id:
                    raise ValueError('PCで同じ記事を編集中です。編集を保存してから、やり直してください。')
            if (action in {'publish_note','delete_note'} or (action=='schedule' and 'confirmed_updated' in data)) and data.get('confirmed_updated')!=post['updated']:
                raise ValueError('受付後に記事が変更されました。もう一度確認してください。')
            if action=='save_note_draft' and not post['draft_key']:
                # A failed first save keeps this durable buffer, never creates a second one on retry.
                if not post['title'].strip() or not post['body'].strip():
                    raise ValueError('タイトルと本文を入力してください。')
            if action=='save_note_draft' and data.get('due') is not None and data['due']<=now_ts():
                raise ValueError('予約を処理する前に指定日時を過ぎました。日時を選び直してください。')
            if action=='preview_article':
                self.window.statusBar().showMessage('noteの本文を読み込んでいます。')
            if action=='schedule':
                self.store.require_schedulable(pid)
                if data['due']<=now_ts():
                    raise ValueError('予約を処理する前に指定日時を過ぎました。日時を選び直してください。')
                action='prepare_schedule'
            elif action=='publish_note':
                action='prepare_publish'
            elif action=='tags':
                action='prepare_tags'
            elif action=='refresh_article':
                if publish_check and post['native_ready'] and post['status']!='review':
                    self.finish('saved','取り込み済みの公開条件を確認しました。','')
                    return
                action='prepare_publish_check' if publish_check or data.get('selection_read') else 'prepare_article'
            self.worker=BrowserWorker(self.store,action,post,parent=self)
            self.worker.background_read=bool(data.get('background_read'))
            self.worker.headless_sync=self.background_sync
            self.worker.items=['queue'] if data.get('queue') else data.get('tags',[])
            self.worker.append_to_queue=bool(data.get('draft_queue'))
            self.worker.interrupt_day=data.get('interrupt_day')
            self.worker.import_due=data.get('due')
            self.window.worker=self.worker
            if not self.background_sync:
                self.worker.progress.connect(self.window.note_progress)
                self.worker.action_required.connect(self.window.note_action_required)
            self.worker.outcome.connect(self.finish)
            self.worker.finished.connect(self.worker_done)
            self.worker.start()
            if not self.background_sync:
                self.window.refresh()
        except Exception as exc:
            Diagnostics(self.store).error('remote_dispatch_error',exc,action=job['action'])
            self.finish('failed',str(exc),'')

    def start_catalog_import(self,stamp):
        if stamp<self.next_catalog_import_at:
            return
        self.next_catalog_import_at=stamp+2
        post=self.store.next_catalog_import(self.window.current_id)
        if not post:
            return
        self.background_sync=True
        self.catalog_import_post_id=post['id']
        self.worker=BrowserWorker(self.store,'prepare_publish_check',post,parent=self)
        self.worker.headless_sync=True
        self.worker.catalog_import=True
        self.window.worker=self.worker
        self.worker.outcome.connect(self.catalog_import_finished)
        self.worker.finished.connect(self.worker_done)
        self.worker.start()
        self.window.refresh_controls()

    def catalog_import_finished(self,status,message,url):
        post=self.worker.post
        self.catalog_import_post_id=None
        if status in SUCCESS:
            self.store.finish_catalog_import(post)
            if post['id']==self.window.current_id and not self.window.dirty:
                self.window.load(post['id'])
        elif message!=self.store.setting('catalog_import_error',''):
            self.store.set_setting('catalog_import_error',message)
            self.window.statusBar().showMessage('記事の自動取り込み: '+message)
        self.window.refresh_controls()

    def finish(self,status,message,url):
        if not self.current:
            return
        automatic=self.background_sync
        batch=self.current['data'].get('queue_batch')
        selection_read=(self.current['action']=='refresh_article' and self.current['data'].get('selection_read'))
        if automatic:
            previous=self.store.setting('background_sync_error','')
            error='' if status in SUCCESS else message
            self.store.set_setting('background_sync_error',error)
            if error and error!=previous:
                self.window.statusBar().showMessage(error)
        summary=self.store.complete_job(self.current['id'],status in SUCCESS,message,url,
                                notify=not automatic and not selection_read)
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
            self.window.note_action_required('')
            if not batch:
                self.window.notify(message+('\n公開URL: '+url if status=='published' and url else ''),status not in SUCCESS)
            elif summary:
                self.window.notify(summary['message'],not summary['ok'])
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
        if getattr(self.worker,'catalog_import',False):
            self.window.refresh_controls()
        elif not self.background_sync:
            self.window.refresh()
        if self.window.quitting:
            self.window.finish_quit()
