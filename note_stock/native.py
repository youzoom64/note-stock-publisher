"""Keep note's entire authoring surface and its native publish payload unchanged."""
import json
import re
import time
from urllib.parse import urlparse

from .publisher import NoteBrowser, VerificationFailed, plain_text
from .native_gate import install as install_gate
from .public_page import PublicArticle,load_public_html
from .native_document import RESTORE_DOCUMENT,document_fingerprint


EDITOR_FEATURES=(
    'AIアシスタント','画像','音声','埋め込み','ファイル','コミック','目次','大見出し','小見出し',
    '箇条書きリスト','番号付きリスト','引用','コード','区切り線','有料エリア指定',
    '太字','文字装飾','配置','リンク','選択範囲の編集・削除',
)


def publication_request(request):
    parsed=urlparse(request.url)
    return (parsed.scheme=='https' and parsed.hostname=='note.com' and request.method=='PUT'
            and re.fullmatch(r'/api/v1/text_notes/\d+',parsed.path) is not None)


def body_fingerprint(document):
    return document_fingerprint(document)


def content_snapshot(page):
    title=page.get_by_placeholder('記事タイトル',exact=True).input_value()
    body=page.locator('.ProseMirror').first
    document=body.evaluate("""e=>e.pmViewDesc?.node?.toJSON ?
        {kind:'prosemirror',value:e.pmViewDesc.node.toJSON()} :
        {kind:'html',value:e.innerHTML}""")
    return {'title':title,'text':plain_text(body),'html':body.inner_html(),
            'document':document,'fingerprint':body_fingerprint(document)}


class NativeNoteBrowser(NoteBrowser):
    def open(self, playwright):
        from .managed_browser import connect,startup_blank_page
        background=getattr(self,'background',False)
        self.browser,self.context=connect(playwright,self.store.root,self.store.setting('browser','auto'),activate=not background)
        self.context.set_default_timeout(20000)
        # Reuse the browser's startup tab instead of leaving an empty tab beside
        # the editor. Never navigate a tab that already contains a document.
        self.page=startup_blank_page(self.context)
        if self.page is None:
            self.page=self.new_page()

    def new_page(self):
        from .managed_browser import startup_blank_page
        blank=startup_blank_page(self.context)
        if blank is not None:
            return blank
        if getattr(self,'background',False):
            from .managed_browser import background_page
            return background_page(self.browser,self.context)
        return self.context.new_page()

    def disconnect(self):
        # The with sync_playwright() owner disconnects its driver. The app-owned
        # Chromium keeps its profile and the user's native editing tabs alive.
        pass

    def read_article_preview(self,post):
        try:
            key=post['draft_key']
            self.page.goto(self.EDITOR+f'/notes/{key}/edit/',wait_until='domcontentloaded')
            _,body=self.editor_fields()
            if self.draft_key(self.page.url)!=key:
                raise VerificationFailed('選んだ記事の本文を確認できませんでした。')
            self.store.save_article_preview(post['id'],key,plain_text(body),body.inner_html())
        finally:
            self.page.close()

    def drafts(self):
        from .drafts import read_drafts
        return read_drafts(self)

    def sync_articles(self):
        from .drafts import read_catalog
        catalog=read_catalog(self)
        results=self.store.merge_catalog(catalog['items'],catalog['complete'])
        return {'catalog':catalog,'results':results}

    def save_draft(self,post):
        self.check_account()
        # Native publication requests remain blocked throughout draft saving.
        install_gate(self.page,lambda source,request:None)
        key=post['draft_key']
        if key:
            self.page.goto(self.EDITOR+f'/notes/{key}/edit/',wait_until='domcontentloaded')
            self.editor_fields()
            snapshot=content_snapshot(self.page)
            if post['native_ready'] and (snapshot['fingerprint']!=post['native_fingerprint'] or snapshot['title']!=post['title']):
                raise VerificationFailed('note側の記事が変更されています。先に最新の内容を取り込んでください。')
        else:
            self.page.goto(self.EDITOR+'/new',wait_until='domcontentloaded')
            self.editor_fields()
            # Persist the remote identity before typing/uploading can fail.
            key=self.wait_draft(post['id'])
        if not post['native_ready']:
            title,body=self.editor_fields()
            current=content_snapshot(self.page)
            if post['draft_key'] and (current['title'] not in ('',post['title']) or current['text'].strip() not in ('',post['body'].strip())):
                raise VerificationFailed('保存待ちの間にnote側の記事が変更されています。上書きせず停止しました。内容を読み直してください。')
            title.fill(post['title'])
            document=json.loads(post.get('native_document') or '{}')
            if document.get('kind')=='prosemirror':
                body.evaluate(RESTORE_DOCUMENT,document['value'])
            else:
                body.fill(post['body'])
            if post['image']:
                self.add_image(self.store.image_path(post['image']))
        expected=content_snapshot(self.page)
        key=self.wait_draft(post['id'])
        save=self.first_visible([self.button('下書き保存'),self.button('一時保存')],'下書き保存')
        with self.page.expect_response(lambda r:'/api/v1/text_notes/draft_save' in r.url and r.request.method=='POST',timeout=30000) as receipt:
            save.click()
        if not receipt.value.ok:
            raise VerificationFailed('noteの下書き保存応答が失敗しました。')
        inspector=self.new_page()
        try:
            inspector.goto(self.EDITOR+f'/notes/{key}/edit/',wait_until='domcontentloaded')
            inspector.locator('.ProseMirror').first.wait_for(state='visible')
            saved=content_snapshot(inspector)
            if expected['title']!=saved['title'] or expected['fingerprint']!=saved['fingerprint']:
                raise VerificationFailed('noteへ保存した本文を再読込して確認できません。')
        finally:
            inspector.close()
            if not self.page.is_closed():
                self.page.close()
        self.store.merge_catalog([{'key':key,'title':saved['title'],'status':'published' if post['url'] else 'draft',
                                   'owner':self.store.setting('account',''),'updated':''}],False)
        self.page=self.new_page()
        prepared=self.store.begin_native_edit(post['id'])
        self.edit(prepared,automatic=True)
        # Desired tags are applied to the captured, full native payload.
        self.update_tags(post['id'],post['tags'],use_current=True)
        return key

    def update_tags(self,pid,tags,*,use_current=False):
        from .secrets import protect,unprotect
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT tags,native_payload FROM posts WHERE id=?',(pid,)).fetchone()
            template=unprotect(current['native_payload'])
            if use_current:
                tags=json.loads(current['tags'])
            template['payload']['hashtags']=['#'+tag for tag in tags]
            c.execute('UPDATE posts SET tags=?,native_payload=?,tags_overridden=1 WHERE id=?',
                      (json.dumps(tags,ensure_ascii=False),protect(template),pid))

    def delete_article(self,post):
        from playwright.sync_api import TimeoutError as BrowserTimeout
        self._delete_stage='アカウント確認'
        try:
            return self._delete_article(post)
        except BrowserTimeout:
            raise VerificationFailed('記事削除の「'+self._delete_stage+'」で画面の応答を確認できませんでした。削除結果を確認してから再操作してください。') from None

    def _delete_article(self,post):
        self.check_account()
        key=post['draft_key']
        if not re.fullmatch(r'n[a-z0-9]{12}',key):
            raise VerificationFailed('削除対象の記事IDを確認できません。')
        # Use the site's own article-specific menu and delete confirmation.
        self._delete_stage='記事管理を開く'
        self.page.goto(self.CORE+'/notes',wait_until='domcontentloaded')
        link=self.page.locator('a[href*="'+key+'"]')
        self._delete_stage='対象記事のリンク確認'
        link.first.wait_for(state='visible',timeout=20000)
        row=link.first.locator('xpath=ancestor::li[1]')
        menu=row.locator('button[aria-haspopup="true"]')
        if menu.count()!=1:
            raise VerificationFailed('対象記事の操作メニューを一意に確認できません。削除しませんでした。')
        self._delete_stage='対象記事のメニューを開く'
        menu.click()
        names=('削除','記事を削除','削除する')
        accepted={'value':False}
        def confirm_dialog(dialog):
            if '削除' in dialog.message:
                accepted['value']=True
                dialog.accept()
            else:
                dialog.dismiss()
        self.page.on('dialog',confirm_dialog)
        self._delete_stage='削除メニューを選ぶ'
        self.first_visible([self.page.get_by_role(role,name=name,exact=True) for role in ('menuitem','button') for name in names],'記事削除').click()
        self.page.wait_for_timeout(400)
        for dialog in self.page.get_by_role('dialog').all():
            if dialog.is_visible() and '削除' in dialog.inner_text():
                self._delete_stage='削除確認'
                self.first_visible([dialog.get_by_role('button',name=name,exact=True) for name in names],'削除の確認').click()
                accepted['value']=True
                break
        if not accepted['value']:
            raise VerificationFailed('削除の確認操作を確認できません。結果を確認してください。')
        from .public_page import load_public_html
        from urllib.error import HTTPError
        for _ in range(15):
            try:
                status,_,_=load_public_html(self.CORE+f'/{self.store.setting("account","")}/n/{key}')
            except HTTPError as exc:
                status=exc.code
            if status in (404,410): break
            self.page.wait_for_timeout(500)
        if status not in (404,410):
            raise VerificationFailed('記事削除後の非公開を確認できません。結果を確認してください。')
        self._delete_stage='削除後の記事一覧を再確認'
        self.page.reload(wait_until='domcontentloaded')
        self.page.wait_for_timeout(1200)
        if self.page.locator('a[href*="'+key+'"]').count():
            raise VerificationFailed('記事管理に削除対象が残っています。結果を確認してください。')
        with self.store.connect() as c:
            c.execute('DELETE FROM catalog WHERE post_id=?',(post['id'],))
        self.store.delete(post['id'])
        self.page.close()

    def edit(self, post, automatic=False):
        account=self.check_account()
        key=post['draft_key']
        captured={}
        error={}
        def capture(source,request):
            try:
                payload=json.loads(request['body'])
                if not isinstance(payload,dict) or payload.get('status') not in {'published','reserved','private','frozen'}:
                    raise ValueError('公開処理の形式を確認できません。')
                if not key or self.draft_key(self.page.url)!=key:
                    raise ValueError('編集中の記事を確認できません。公開を停止しました。')
                captured.update(method=request['method'],url=request['url'],payload=payload,key=key,account=account)
            except Exception as exc:
                error['message']=str(exc)
            return None
        existing=next((p for p in self.context.pages if key and not p.is_closed() and self.draft_key(p.url)==key),None)
        if existing:
            control=self.page
            self.page=existing
            if control!=existing:
                control.close()
        # Install the fail-closed gate before navigation, loading, or document restore.
        install_gate(self.page,capture)
        if not existing:
            self.page.goto(self.EDITOR+(f'/notes/{key}/edit/' if key else '/new'),wait_until='domcontentloaded')
        if getattr(self,'browser',None) and not automatic:
            session=self.context.new_cdp_session(self.page)
            try:
                window=session.send('Browser.getWindowForTarget')
                session.send('Browser.setWindowBounds',{'windowId':window['windowId'],'bounds':{'windowState':'maximized'}})
            finally:
                session.detach()
            self.page.bring_to_front()
        if post.get('_catalog_import'):
            if self.draft_key(self.page.url)!=key:
                raise VerificationFailed('取り込み対象の記事を確認できませんでした。')
        else:
            key=self.wait_draft(post['id'])
        in_editor='/edit' in urlparse(self.page.url).path
        title,body=self.editor_fields() if in_editor else (None,None)
        if not post['draft_key']:
            if post['title']:
                title.fill(post['title'])
            if post['body']:
                if not post['native_html']:
                    body.fill(post['body'])
            document=json.loads(post.get('native_document') or '{}')
            if document.get('kind')=='prosemirror':
                body.evaluate(RESTORE_DOCUMENT,document['value'])
            elif post['native_html']:
                body.evaluate("""(e,html)=>{const data=new DataTransfer();data.setData('text/html',html);
                    e.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));}""",post['native_html'])
            if post['image']:
                self.add_image(self.store.image_path(post['image']))
        self.page.on('dialog',lambda dialog:dialog.dismiss() if captured or error else None)
        # Identify the managed window without covering any native toolbar or menu.
        self.page.evaluate("""()=>{const prefix='【ストック編集・公開しません】';
            const update=()=>{if(!document.title.startsWith(prefix))document.title=prefix+document.title;};
            update();new MutationObserver(update).observe(document.querySelector('title')||document.head,{childList:true,subtree:true});}""")
        if not automatic:
            getattr(self,'action_required',self.progress)('note本来の編集画面で編集してください。公開設定の最後の投稿操作で本文を取り込みます。')
        keep_open=False
        try:
            if automatic:
                if '/edit' in self.page.url:
                    if not title.input_value().strip():
                        raise VerificationFailed('記事タイトルが未入力です。「noteで編集」でタイトルを入力して下書き保存し、もう一度「本文・公開条件を読み込む」を押してください。まだ公開していません。')
                    self.first_visible([self.button('公開に進む'),self.button('公開設定')],'公開設定').click()
                for _ in range(5):
                    if captured or error:
                        break
                    final=self.first_visible([self.button(name) for name in
                        ('投稿する','更新する','公開する','有料エリア設定','試し読みエリアを設定','限定エリア設定','予約投稿')],
                        '公開設定の取り込み',timeout=10)
                    final.click()
                    for _ in range(15):
                        if captured or error:
                            break
                        self.page.wait_for_timeout(100)
                if not captured:
                    raise VerificationFailed('自動で公開設定を取り込めませんでした。noteで編集を開き、設定を確認してください。公開はしていません。')
            while not captured:
                if error:
                    raise VerificationFailed(error['message'])
                if self.cancelled():
                    save=self.button('下書き保存')
                    if not save.count():
                        save=self.button('一時保存')
                    if save.count() and save.first.is_visible():
                        try:
                            with self.page.expect_response(lambda r:'/api/v1/text_notes/draft_save' in r.url and r.request.method=='POST',timeout=20000) as receipt:
                                save.first.click()
                            if not receipt.value.ok:
                                raise RuntimeError('保存応答が失敗しました。')
                        except Exception:
                            keep_open=True
                            raise RuntimeError('最新の変更を保存できたか確認できません。編集画面を残しました。note画面で下書き保存してください。')
                    raise RuntimeError('編集を中止しました。本文はnoteの下書きに保存し、取り込み前の公開設定は破棄しました。')
                if self.page.is_closed():
                    raise RuntimeError('取り込み前に編集画面が閉じられました。note側の下書きから編集を再開できます。')
                self.page.wait_for_timeout(200)
            if not automatic:
                self.progress('noteから本文と公開設定を取り込んでいます。')
            # Final native payload already contains the complete free/pay HTML and
            # every chosen publication option. Never turn either body into plain text.
            inspector=self.new_page()
            try:
                inspector.goto(self.EDITOR+f'/notes/{key}/edit/',wait_until='domcontentloaded')
                inspector.locator('.ProseMirror').first.wait_for(state='visible')
                snapshot=content_snapshot(inspector)
            finally:
                inspector.close()
            self.store.capture_native(post['id'],captured,snapshot,
                                      edit_state={**post,'_preserve_app_tags':automatic})
            self.progress('装飾・本文素材・公開設定を取り込みました。まだ公開していません。')
            return key
        finally:
            if not keep_open and not self.page.is_closed():
                self.page.close()

    def publish(self, post):
        template=self.store.native_template(post['id'])
        if not post['native_ready'] or not template:
            raise VerificationFailed('noteで編集し、公開設定をアプリへ取り込んでください。')
        baseline=self.store.catalog_state(post['id'])
        if baseline:
            self.sync_articles()
            latest=self.store.catalog_state(post['id'])
            if latest.get('remote_status')=='published' and baseline.get('remote_status')!='published':
                raise VerificationFailed('この記事はnote側ですでに公開されています。重複投稿を停止しました。')
            if baseline.get('remote_updated') and latest.get('remote_updated')!=baseline['remote_updated']:
                raise VerificationFailed('予約後にnote側の記事が更新されています。内容と公開条件を確認して再予約してください。')
            self.page=self.new_page()
        self.check_account()
        key=post['draft_key']
        self.page.goto(self.EDITOR+f'/notes/{key}/edit/',wait_until='domcontentloaded')
        self.editor_fields()
        current=content_snapshot(self.page)
        expected=body_fingerprint(json.loads(post['native_document']))
        if current['fingerprint']!=expected or current['title']!=post['title']:
            raise VerificationFailed('note側の下書きが取り込み後に変更されています。編集内容と公開設定を取り込み直してください。')
        if template['key']!=key or not publication_request(type('Request',(),{'url':template['url'],'method':template['method']})()):
            raise VerificationFailed('保存した公開処理が原稿と一致しません。')
        # Let the current native frontend generate fresh authentication and anti-abuse
        # requests. Only substitute the article payload captured from that same editor.
        sent={}
        def authorize(source,request):
            if request['url']!=template['url'] or request['method']!=template['method'] or sent:
                raise VerificationFailed('保存した公開処理と一致しません。送信を停止しました。')
            self.store.checkpoint(post['id'],key=key,submitted=True)
            sent['value']=True
            return json.dumps(template['payload'],ensure_ascii=False)
        try:
            self.first_visible([self.button('公開に進む'),self.button('公開設定')],'公開設定').click()
            install_gate(self.page,authorize)
            # The transport UI must reach a native final submit. The captured payload,
            # including paid settings, is retained verbatim at the request boundary.
            free=self.page.get_by_role('radio',name='無料',exact=True)
            if free.count() and free.is_visible():
                free.check()
            with self.page.expect_response(lambda response:response.url==template['url'] and response.request.method=='PUT',timeout=45000) as receipt:
                self.first_visible([self.button('投稿する'),self.button('更新する'),self.button('公開する')],'投稿').click()
            response=receipt.value
            self.progress('noteの公開応答を受信しました。保存された本文と公開ページを確認します。')
            if not sent:
                raise VerificationFailed('noteの公開処理を開始できません。認証や公開設定を確認してください。')
            try:
                accepted=response.json()
            except Exception:
                raise VerificationFailed('公開処理の応答を確認できません。結果未確認として停止します。')
            if not response.ok or accepted.get('error') or not isinstance(accepted.get('data'),dict):
                raise VerificationFailed('noteが公開処理を受け付けませんでした。認証や設定をnote画面で確認してください。')
            self.page.wait_for_timeout(1800)
            status=template['payload'].get('status','published')
            if status=='reserved':
                self.verify_saved_content(post)
                return {'status':'native_reserved','url':self.CORE+f'/{post["native_owner"]}/n/{key}'}
            if status in {'private','frozen'}:
                self.verify_saved_content(post)
                return {'status':'native_private','url':self.CORE+f'/{post["native_owner"]}/n/{key}'}
            return self.verify(post,account=post['native_owner'],key=key)
        finally:
            if not self.page.is_closed():
                self.page.close()

    def verify_saved_content(self,post):
        inspector=self.new_page()
        try:
            inspector.goto(self.EDITOR+f'/notes/{post["draft_key"]}/edit/',wait_until='domcontentloaded')
            inspector.locator('.ProseMirror').first.wait_for(state='visible',timeout=20000)
            saved=content_snapshot(inspector)
            if saved['fingerprint']!=body_fingerprint(json.loads(post['native_document'])):
                raise VerificationFailed('noteに保存された本文構造が取り込み内容と一致しません。結果の確認が必要です。')
        finally:
            inspector.close()

    def verify(self, post, account=None, key=None):
        # Publication success is checked against the native saved body, not a
        # stripped text surrogate. Paid content remains private to logged-in readers.
        account=account or post['native_owner'] or self.check_account()
        key=key or post['draft_key']
        url=self.CORE+f'/{account}/n/{key}'
        self.progress('noteに保存された本文構造を照合しています。')
        self.verify_saved_content(post)
        self.progress('ログイン情報を使わず、公開ページとハッシュタグを照合しています。')
        try:
            status,final_url,html=load_public_html(url)
        except Exception as exc:
            raise VerificationFailed('公開ページの取得に失敗しました。結果未確認として停止します（'+type(exc).__name__+'）。') from exc
        if status!=200 or final_url.split('?')[0].rstrip('/')!=url:
            raise VerificationFailed('公開ページと公開先URLを確認できません。結果未確認として停止します。')
        article=PublicArticle(html)
        if post['title'] not in article.headings or any(tag not in article.tags for tag in post['tags']):
            raise VerificationFailed('公開ページのタイトルまたはハッシュタグが一致しません。')
        self.progress('本文構造・公開URL・タイトル・ハッシュタグの照合が完了しました。')
        return url
