from contextlib import ExitStack
import threading
import time

from PySide6.QtCore import QThread, Signal

from .diagnostics import Diagnostics
from .publisher import LoginRequired
from .native import NativeNoteBrowser as NoteBrowser

HEADLESS_WRITES = frozenset({'save_note_draft', 'prepare_publish', 'publish'})
VISIBLE_OPERATIONS = frozenset({'preview_article','prepare_article','prepare_publish_check','prepare_schedule'})


class BrowserWorker(QThread):
    progress = Signal(str)
    action_required = Signal(str)
    outcome = Signal(str, str, str)  # status, message, URL
    data = Signal(object)

    def __init__(self, store, action, post=None, expected="", parent=None):
        super().__init__(parent)
        self.store, self.action, self.post, self.expected = store, action, post, expected
        self.cancel = threading.Event()
        self.items=[]
        self.import_due=None
        self.recovery_id=''
        self.headless_sync=False
        self.catalog_import=False
        self.background_read=False
        self.append_to_queue=False
        self.interrupt_day=None

    def run(self):
        capture_catalog_draft = (self.action=='save_note_draft' and self.post
                                 and self.post['draft_key'] and self.post['editor_kind']=='native'
                                 and not self.post['native_ready'])
        if self.action=='prepare_schedule':
            try:
                self.store.require_schedulable(self.post['id'])
                if self.post['native_ready'] and self.post['status']!='review':
                    # The selected note has already been captured. Reuse that
                    # complete payload; publishing still verifies it against note.
                    self.store.native_template(self.post['id'])
                    self.store.set_schedule(self.post['id'],self.import_due)
                    from datetime import datetime
                    from .store import JST
                    self.outcome.emit('saved','予約しました: '+datetime.fromtimestamp(self.import_due,JST).strftime('%Y-%m-%d %H:%M')+'（日本時間）','')
                    return
            except ValueError as exc:
                self.outcome.emit('failed',str(exc),'')
                return
        adapter = NoteBrowser(self.store, self.progress.emit, self.cancel.is_set)
        adapter.action_required=self.action_required.emit
        visible_read=(self.action in VISIBLE_OPERATIONS or capture_catalog_draft) and not self.background_read
        adapter.background=not visible_read and self.action not in {'login','relogin','edit'}
        if self.action=='sync_articles' and self.headless_sync:
            self.run_headless_sync(adapter)
            return
        log = Diagnostics(self.store)
        try:
            from playwright.sync_api import sync_playwright
            error = '' if self.catalog_import else log.record("browser_start", action=self.action, post_id=(self.post or {}).get("id", ""))
            if error:
                self.progress.emit(error)
            with sync_playwright() as playwright, ExitStack() as browser_scope:
                google_login=self.action=='relogin' or (self.action=='login' and self.store.setting('note_login_method','password')=='google')
                if google_login:
                    from .managed_browser import interactive_login,shutdown
                    if self.action=='relogin':
                        from .managed_browser import reset_note_login
                        self.progress.emit('専用ブラウザーでnoteのログイン画面を準備しています。')
                        reset_note_login(playwright,self.store.root,self.store.setting('browser','auto'))
                        child=interactive_login(playwright,self.store.root,self.store.setting('browser','auto'),app_mode=True)
                    else:
                        child=interactive_login(playwright,self.store.root,self.store.setting('browser','auto'))
                    if self.action=='relogin':
                        import psutil
                        process=psutil.Process(child.pid)
                        self.store.set_setting('remote_relogin_window',{'recovery_id':self.recovery_id,'ready':True,
                            'pid':process.pid,'created':process.create_time()})
                    self.action_required.emit('専用ブラウザーでGoogleログインを済ませ、ウィンドウを閉じてください。')
                    end=time.monotonic()+900
                    while child.poll() is None:
                        ticket=self.store.setting('remote_relogin_ticket',{}) if self.action=='relogin' else None
                        expired=ticket is not None and (ticket.get('id')!=self.recovery_id or ticket.get('expires',0)<=time.time())
                        if self.cancel.is_set() or time.monotonic()>end or expired:
                            shutdown(self.store.root)
                            if ticket is not None:
                                from .relogin import RecoveryStopped
                                raise RecoveryStopped('再ログインを中止しました。' if ticket.get('cancelled') else '再ログインの接続期限が切れました。もう一度開始できます。')
                            raise RuntimeError('Googleログインの待機を終了しました。設定から再開できます。')
                        time.sleep(0.25)
                if (self.action in HEADLESS_WRITES or self.background_read) and not visible_read:
                    from .headless_sync import open_note_session
                    browser_scope.enter_context(open_note_session(adapter, playwright))
                else:
                    adapter.open(playwright)
                    if visible_read:
                        adapter.page.bring_to_front()
                try:
                    if self.action in {"login",'relogin'}:
                        if google_login:
                            self.progress.emit('noteのログイン先を確認しています。')
                            from .publisher import account_id
                            actual=adapter.identity()
                            if actual!=account_id(self.expected):
                                raise LoginRequired('Googleでログインしたnoteアカウントが設定と異なります。')
                        else:
                            actual = adapter.login(self.expected)
                        self.store.set_setting("account", actual)
                        self.store.set_setting("account_verified", True)
                        from .headless_sync import remember_session
                        remember_session(self.store,adapter.context)
                        status, message, url = "connected", f"{actual} に接続しました。", ""
                    elif self.action=='drafts':
                        items=adapter.drafts()
                        self.data.emit({'action':'drafts','items':items})
                        status,message,url='listed',f'noteの下書き {len(items)}件を取得しました。',''
                    elif self.action=='sync_articles':
                        result=adapter.sync_articles()
                        from .headless_sync import remember_session
                        remember_session(self.store,adapter.context)
                        self.data.emit({'action':'sync_articles',**result})
                        complete=self.store.setting('catalog_sync',{}).get('complete',False)
                        status,message,url='synced','記事を同期しました。'+('全件取得済み。' if complete else '全件取得の確認は未完了です。取得済み分を表示します。'),''
                    elif self.action=='save_note_draft':
                        if capture_catalog_draft:
                            # Catalog entries contain a title, not an editable body.
                            # Capture the existing note before applying PC posting settings;
                            # never compare its body with the empty catalog placeholder.
                            adapter.edit({**self.post,'_catalog_import':True,
                                          '_previous_status':self.post['status'],
                                          '_previous_due':self.post['due']},automatic=True)
                        else:
                            adapter.save_draft(self.post)
                        if self.import_due:
                            self.store.set_schedule(self.post['id'],self.import_due)
                        elif self.items==['queue']:
                            current=self.store.get(self.post['id'])
                            self.store.save(current['id'],current['title'],current['body'],current['tags'],current['image'],'queued',
                                            append_to_queue=self.append_to_queue,interrupt_day=self.interrupt_day)
                        status,message,url='saved','noteへの下書き保存と再読込を確認しました。',''
                        if self.items==['queue']:
                            message='下書きの保存と再読込を確認し、定期投稿のキューへ追加しました。'
                            if self.interrupt_day:
                                message=self.interrupt_day+'の投稿に割り込み、後続を翌日以降へ送りました。'
                    elif self.action=='preview_article':
                        adapter.read_article_preview(self.post)
                        status,message,url='saved','本文プレビューを読み込みました。',''
                    elif self.action in {'prepare_schedule','prepare_publish','prepare_tags','prepare_article','prepare_publish_check'}:
                        original=self.post
                        remote_before=self.store.catalog_state(original['id']).get('remote_status')
                        compare_existing=self.action in {'prepare_schedule','prepare_publish','prepare_tags'} and original['native_ready']
                        previous_payload=self.store.native_template(original['id'])['payload'] if compare_existing else None
                        if self.catalog_import:
                            begun={**original,'_catalog_import':True,'_previous_status':original['status'],'_previous_due':original['due']}
                        else:
                            begun=self.store.begin_native_edit(original['id'])
                        adapter.edit(begun,automatic=True)
                        refreshed=self.store.get(original['id'])
                        if self.action in {'prepare_schedule','prepare_publish','prepare_tags'} and original['native_ready'] and any(
                            original[key]!=refreshed[key] for key in ('title','native_fingerprint','native_summary')):
                            raise ValueError('noteの内容・公開条件が変更されています。読み直した内容を確認してから再操作してください。')
                        if self.action in {'prepare_schedule','prepare_publish','prepare_tags'} and previous_payload is not None:
                            latest_payload=self.store.native_template(original['id'])['payload']
                            # note regenerates this transport token on each capture;
                            # keep the fresh token in the payload, but do not treat
                            # its rotation as an author changing publication options.
                            omit={'hashtags','name','free_body','pay_body','line_add_friend_access_token'}
                            if {k:v for k,v in previous_payload.items() if k not in omit}!={k:v for k,v in latest_payload.items() if k not in omit}:
                                raise ValueError('noteの公開条件が変更されています。最新の公開条件を確認して再操作してください。')
                        if self.action=='prepare_tags':
                            adapter.update_tags(original['id'],self.items)
                            adapter.page=adapter.new_page()
                            adapter.sync_articles()
                            if original['status']=='scheduled':
                                self.store.set_schedule(original['id'],original['due'])
                            elif original['status']=='queued':
                                with self.store.connect() as c:
                                    c.execute("UPDATE posts SET status='queued' WHERE id=?",(original['id'],))
                        elif self.action=='prepare_schedule':
                            adapter.update_tags(original['id'],original['tags'])
                            adapter.page=adapter.new_page()
                            adapter.sync_articles()
                            self.store.require_schedulable(original['id'])
                            self.store.set_schedule(original['id'],self.import_due)
                        elif self.action=='prepare_publish':
                            adapter.update_tags(original['id'],original['tags'])
                            adapter.page=adapter.new_page()
                            adapter.sync_articles()
                            if remote_before!='published' and self.store.catalog_state(original['id']).get('remote_status')=='published':
                                raise ValueError('note側ですでに公開されています。二重の投稿・更新を停止しました。')
                            with self.store.connect() as c:
                                c.execute("UPDATE posts SET status='draft' WHERE id=?",(original['id'],))
                            claimed=self.store.claim(__import__('time').time(),post_id=original['id'])
                            adapter.page=adapter.new_page()
                            result=adapter.publish(claimed)
                            url=result['url'] if isinstance(result,dict) else result
                            self.store.finish(original['id'],result['status'] if isinstance(result,dict) else 'published',url=url)
                        if self.action!='prepare_publish':
                            url=''
                        if self.action=='prepare_publish':
                            status,message='published','投稿・更新を確認しました。'
                        elif self.action=='prepare_schedule':
                            from datetime import datetime
                            from .store import JST
                            status,message='saved','予約しました: '+datetime.fromtimestamp(self.import_due,JST).strftime('%Y-%m-%d %H:%M')+'（日本時間）'
                        elif self.action in {'prepare_article','prepare_publish_check'}:
                            if self.action=='prepare_article' and original['status'] in {'scheduled','queued'}:
                                with self.store.connect() as c:
                                    c.execute("UPDATE posts SET status='review',due=? WHERE id=?",(original['due'],original['id']))
                            status,message='saved','noteの最新本文・公開条件を取り込みました。記事一覧から開き直して確認できます。'
                        else:
                            status,message='saved','投稿時のハッシュタグを保存しました。'
                    elif self.action=='delete_note':
                        adapter.delete_article(self.post)
                        status,message,url='deleted','noteの記事削除と公開URLの非公開を確認しました。',''
                    elif self.action=='import':
                        catalog=adapter.drafts()
                        keys=set(self.items)
                        selected=[item for item in catalog if item['key'] in keys]
                        results=self.store.link_note_drafts(selected)
                        for key in keys-{item['key'] for item in selected}:
                            results.append({'ok':False,'key':key,'error':'現在の下書き一覧にありません。公開済み・予約済みの記事は取り込みません。'})
                        for item in results:
                            if not item['ok']:
                                continue
                            begun=False
                            prepared_ok=False
                            try:
                                self.progress.emit('下書きを取り込み中: '+item['title'])
                                adapter.page=adapter.new_page()
                                post=self.store.begin_native_edit(item['id'])
                                begun=True
                                adapter.edit(post,automatic=True)
                                prepared_ok=True
                                prepared=self.store.get(item['id'])
                                if self.import_due:
                                    self.store.save(item['id'],prepared['title'],prepared['body'],prepared['tags'],'','scheduled',self.import_due)
                                item['prepared']=True
                            except Exception as exc:
                                item['ok']=False
                                item['error']=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
                                item['prepared']=prepared_ok
                                if begun and not prepared_ok:
                                    self.store.native_edit_failed(item['id'],item['error'])
                        self.data.emit({'action':'import','results':results})
                        failures=sum(not item['ok'] for item in results)
                        status='imported' if not failures else 'partial'
                        message=f"下書きの取り込み: 成功 {len(results)-failures}件 / 要確認 {failures}件。まだ公開していません。"
                        url=''
                    elif self.action=='edit':
                        adapter.edit(self.post)
                        status,message,url='prepared','noteの本文・素材・公開設定を取り込みました。まだ公開していません。',''
                    else:
                        result = adapter.publish(self.post) if self.action == "publish" else adapter.verify(self.post)
                        if isinstance(result,dict):
                            status,url=result['status'],result['url']
                            message='note側で指定した状態への反映を確認しました。'
                        else:
                            status,url='published',result
                            message='投稿済み記事の内容と公開URLを確認しました。'
                        self.store.finish(self.post['id'],status,url=url)
                finally:
                    if hasattr(adapter,'disconnect'):
                        adapter.disconnect()
                    else:
                        adapter.context.close()
        except Exception as exc:
            from .relogin import RecoveryStopped
            if not isinstance(exc,RecoveryStopped):
                log.error('browser_operation_error',exc,action=self.action)
            if isinstance(exc,LoginRequired):
                self.store.set_setting('account_verified',False)
            url = ""
            # Playwright errors may embed entered text and cookies. Expose the safe stage,
            # not its verbose call log. Our own validation exceptions are safe to show.
            message = (str(exc) if isinstance(exc, (ValueError, RuntimeError)) and not type(exc).__module__.startswith("playwright")
                       else "note画面の操作に失敗しました（" + type(exc).__name__ + "）。ログイン・接続状況を確認してください。")
            status = 'cancelled' if isinstance(exc,RecoveryStopped) else 'failed'
            if self.post:
                try:
                    if self.action=='prepare_publish_check' and not self.catalog_import:
                        self.store.restore_interrupted_publish_check(self.post)
                    elif self.action in {'edit','save_note_draft','prepare_schedule','prepare_publish','prepare_tags','prepare_article'} and self.store.get(self.post['id'])['status']=='editing':
                        self.store.native_edit_failed(self.post['id'],message)
                    elif self.action=='prepare_schedule' and self.store.catalog_state(self.post['id']).get('remote_status')=='published':
                        # A reservation rejection must never rewrite a public article as failed.
                        pass
                    elif not self.catalog_import and self.action!='preview_article':
                        current = self.store.get(self.post["id"])
                        status = "unknown" if current["submitted"] or self.action == "verify" else ("held" if isinstance(exc, LoginRequired) else "failed")
                        self.store.finish(self.post["id"], status, message)
                except Exception as save_error:
                    log.error('browser_result_save_error',save_error,action=self.action)
                    status = "unknown"
                    message += " 結果の保存にも失敗しました。次回起動時に公開結果を確認してください。"
            if self.action not in {'preview_article','prepare_publish_check'} or isinstance(exc,LoginRequired):
                try:
                    self.store.set_setting("paused", True)
                except Exception as save_error:
                    log.error('pause_state_save_error',save_error,action=self.action)
                    message += " 一時停止設定も保存できませんでした。アプリを終了してください。"
        warning = '' if self.catalog_import else log.record("browser_finish", action=self.action, status=status)
        if warning:
            message += " " + warning
        self.outcome.emit(status, message, url)

    def run_headless_sync(self,adapter):
        from playwright.sync_api import sync_playwright
        from .headless_sync import sync_catalog
        try:
            with sync_playwright() as playwright:
                result=sync_catalog(adapter,playwright)
            self.data.emit({'action':'sync_articles',**result})
            complete=self.store.setting('catalog_sync',{}).get('complete',False)
            message='記事を同期しました。'+('全件取得済み。' if complete else '取得できた記事だけを反映しました。')
            self.outcome.emit('synced',message,'')
        except LoginRequired as exc:
            Diagnostics(self.store).error('catalog_sync_login_error',exc)
            self.outcome.emit('failed',str(exc),'')
        except Exception as exc:
            Diagnostics(self.store).error('catalog_sync_error',exc)
            self.outcome.emit('failed','バックグラウンド同期に失敗しました（'+type(exc).__name__+'）。','')
