"""Visible Developer Portal setup; authentication is always performed by the owner."""
import re
import threading
import time
import uuid
import json
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from PySide6.QtCore import QThread, Signal
from .secrets import protect,unprotect

PORTAL='https://discord.com/developers/applications'


def select_destination(store,guild):
    """Keep per-server resources when the owner explicitly switches destination."""
    if not re.fullmatch(r'\d{16,22}',str(guild)):
        raise RuntimeError('導入先サーバーを確認できません。')
    config=store.setting('discord',{})
    destinations=store.setting('discord_destinations',{})
    if config.get('guild'):
        destinations[str(config['guild'])]={key:config[key] for key in ('channel','menu','category','installation') if key in config}
    if str(config.get('guild',''))!=str(guild):
        for key in ('channel','menu','category','installation'): config.pop(key,None)
        config.update(destinations.get(str(guild),{}))
    config['guild']=str(guild)
    store.set_setting('discord_destinations',destinations)
    store.set_setting('discord',config)


class PortalSetup(QThread):
    progress=Signal(str)
    outcome=Signal(bool,str)
    def __init__(self,store,parent=None):
        super().__init__(parent)
        self.store=store
        self.cancel=threading.Event()
    def checkpoint(self,**values):
        state=self.store.setting('discord_setup',{})
        state.update(values)
        self.store.set_setting('discord_setup',state)
        return state
    def until(self,condition,message,seconds=600):
        self.progress.emit(message)
        end=time.monotonic()+seconds
        notice=''
        while time.monotonic()<end:
            if self.cancel.is_set(): raise RuntimeError('セットアップを中断しました。同じBotから再開できます。')
            if condition(): return
            auth=self.page.get_by_role('heading',name=re.compile('^多要素認証$|^Multi.Factor Authentication$',re.I))
            if self.visible(auth):
                failed=self.page.get_by_text('パスワードが一致しません',exact=True)
                current=('Discordの本人確認でパスワードが一致しません。Discord画面で現在の認証情報を確認してください。'
                         if self.visible(failed) else 'Discordが接続情報発行の本人確認を求めています。表示中のDiscord画面で認証してください。')
                if current!=notice:
                    self.checkpoint(stage='owner_authentication')
                    self.progress.emit(current)
                    notice=current
            self.page.wait_for_timeout(300)
        raise RuntimeError(message+' 時間切れになりました。再開してください。')
    def visible(self,locator):
        return locator.count()>0 and locator.first.is_visible()
    def button(self,pattern):
        return self.page.get_by_role('button',name=re.compile(pattern,re.I)).first
    def configure_installation(self,app):
        self.page.goto(PORTAL+'/'+app+'/installation',wait_until='domcontentloaded')
        user=self.page.get_by_role('checkbox',name=re.compile('^User Install$|^ユーザーインストール$',re.I))
        user.wait_for(state='visible',timeout=30000)
        if user.is_checked(): user.press('Space')
        guild=self.page.get_by_role('checkbox',name=re.compile('^Guild Install$|^ギルドのインストール$',re.I))
        if not guild.is_checked(): guild.press('Space')
        link=self.page.get_by_role('combobox',name=re.compile('^Install Link$|^インストールリンク$',re.I))
        link.press('ArrowDown')
        self.page.get_by_role('option',name=re.compile('^None$|^なし$',re.I)).click()
        save=self.button('Save Changes|変更を保存')
        if self.visible(save):
            save.click()
            save.wait_for(state='hidden',timeout=30000)
        self.checkpoint(stage='installation_configured')
    def create_server(self):
        state=self.store.setting('discord_setup',{})
        if state.get('created_guild'): return state['created_guild']
        if state.get('server_creation_pending'):
            raise RuntimeError('前回のサーバー作成結果が未確認です。重複作成を避けるため、Discordで確認して「既存のサーバーを選ぶ」から再開してください。')
        choice=self.store.setting('discord_onboarding',{})
        name=choice.get('server_name','').strip()
        if not 2<=len(name)<=100: raise RuntimeError('新しいサーバー名を2〜100文字で入力してください。')
        if not choice.get('guidelines_accepted'):
            raise RuntimeError('セットアップ画面でDiscordコミュニティガイドラインを確認し、同意してください。')
        self.page.goto('https://discord.com/channels/@me',wait_until='domcontentloaded')
        # Discord's server sidebar exposes Add Server as a tree item, not a button.
        add=self.page.get_by_role('treeitem',name=re.compile('^Add a Server$|^サーバーを追加$',re.I))
        self.until(lambda:self.visible(add),'Discordにログインし、サーバー一覧が表示されるまでお待ちください。')
        add.press('Enter')
        self.button('^Create My Own$|^オリジナルの作成$|^自分で作成$').click()
        skip=self.page.get_by_text(re.compile('skip this question|この質問をスキップ',re.I))
        self.until(lambda:self.visible(skip) or self.visible(self.page.get_by_role('textbox',name=re.compile('Server Name|サーバー名',re.I))),
                   '新しいサーバーの作成画面を準備しています。')
        if self.visible(skip): skip.first.click()
        field=self.page.get_by_role('textbox',name=re.compile('Server Name|サーバー名',re.I))
        field.fill(name)
        def creating(request):
            if request.method=='POST' and re.search(r'/api/v\d+/guilds(?:\?|$)',request.url):
                self.checkpoint(server_creation_pending=True)
        # Consent is explicit in the setup GUI, before the automatic create click.
        self.page.on('request',creating)
        try:
            with self.page.expect_response(lambda r:r.request.method=='POST' and
                    re.search(r'/api/v\d+/guilds(?:\?|$)',r.url),timeout=600000) as response:
                self.button('^Create$|^作成$|^新規作成$').click()
                self.until(response.is_done,'専用サーバーを自動作成しています。本人認証が表示された場合は、その画面で完了してください。')
        finally:
            self.page.remove_listener('request',creating)
        result=response.value
        if not result.ok: raise RuntimeError('Discordでサーバーを作成できませんでした。画面で作成結果を確認してください。')
        guild=str(result.json().get('id',''))
        if not re.fullmatch(r'\d{16,22}',guild): raise RuntimeError('作成したサーバーIDを確認できません。')
        self.checkpoint(created_guild=guild,server_creation_pending=False)
        return guild
    def install_server(self,app):
        choice=self.store.setting('discord_onboarding',{})
        if not choice.get('enabled') or choice.get('destination') not in ('new','existing'):
            raise RuntimeError('先にDiscord連携とサーバーの利用方法を選択してください。')
        guild=self.create_server() if choice['destination']=='new' else None
        permissions=16|1024|2048|16384|32768|65536
        invite=f'https://discord.com/oauth2/authorize?client_id={app}&permissions={permissions}&scope=bot%20applications.commands'
        if guild: invite+=f'&guild_id={guild}&disable_guild_select=true'
        approved={}
        candidate={'guild':guild}
        last_check=[0.0]
        clicked=set()
        def authorization(response):
            if not (response.request.method=='POST' and
                    re.search(r'/api/v\d+/oauth2/authorize(?:\?|$)',response.url)):
                return
            body=response.request.post_data_json or {}
            selected=str(body.get('guild_id',''))
            if re.fullmatch(r'\d{16,22}',selected): candidate['guild']=selected
            if not response.ok: return
            result=response.json()
            if not isinstance(result,dict) or not re.match(r'^https://discord\.com/oauth2/authorized(?:[/?#]|$)',str(result.get('location',''))):
                return
            if re.fullmatch(r'\d{16,22}',selected): approved['guild']=selected
        self.page.on('response',authorization)
        try:
            self.page.goto(invite,wait_until='domcontentloaded')
            self.checkpoint(stage='invite_opened')
            def continue_installation():
                if approved: return True
                if candidate['guild'] and time.monotonic()-last_check[0]>=5:
                    last_check[0]=time.monotonic()
                    if self.guild_joined(candidate['guild']):
                        approved['guild']=candidate['guild']
                        return True
                for pattern in ('^Continue$|^続行$','^Authorize$|^認証$'):
                    if pattern in clicked: continue
                    button=self.button(pattern)
                    if self.visible(button) and button.is_enabled():
                        button.click()
                        clicked.add(pattern)
                        break
                return bool(approved)
            self.until(continue_installation,'Botを自動導入しています。既存サーバーの場合は導入先だけ選んでください。本人認証が表示された場合は完了してください。')
            if guild and approved['guild']!=guild: raise RuntimeError('作成したサーバーと導入先が一致しません。')
            select_destination(self.store,approved['guild'])
            self.checkpoint(stage='installed',selected_guild=approved['guild'])
        finally:
            self.page.remove_listener('response',authorization)
    def guild_joined(self,guild):
        """Observe only our Bot's membership; owner can finish CAPTCHA on another device."""
        encrypted=self.store.setting('discord_token','')
        if not encrypted: return False
        token=unprotect(encrypted)
        request=Request('https://discord.com/api/v10/guilds/'+str(guild),
                        headers={'Authorization':'Bot '+token,'User-Agent':'NoteStockPublisher/2.0'})
        try:
            with urlopen(request,timeout=5) as response:
                return str(json.load(response).get('id',''))==str(guild)
        except (HTTPError,URLError,TimeoutError):
            return False
    def run(self):
        phase='ブラウザー起動'
        try:
            choice=self.store.setting('discord_onboarding',{})
            if not choice.get('enabled') or choice.get('destination') not in ('new','existing'):
                raise RuntimeError('先にDiscord連携とサーバーの利用方法を選択してください。')
            from playwright.sync_api import sync_playwright
            from .managed_browser import connect
            with sync_playwright() as pw:
                browser,context=connect(pw,self.store.root/'discord-browser',self.store.setting('browser','auto'))
                self.page=context.new_page()
                self.page.goto(PORTAL,wait_until='domcontentloaded')
                self.page.bring_to_front()
                state=self.store.setting('discord_setup',{})
                phase='Discordログイン'
                login=self.button('^Log In$|^ログイン$')
                if self.visible(login):
                    login.click()
                self.until(lambda:'/login' not in self.page.url and self.visible(self.button('New Application|新しいアプリ|新規アプリ')),
                          'Discordへログインしてください。二段階認証などはこのブラウザーで完了してください。')
                if not state.get('application'):
                    phase='専用アプリ作成'
                    if not state.get('name'):
                        state=self.checkpoint(name='note管理 '+uuid.uuid4().hex[:10],stage='named')
                    name=state['name']
                    # Reconcile a prior click whose navigation/checkpoint was interrupted.
                    existing=self.page.get_by_text(name,exact=True)
                    if self.visible(existing):
                        existing.first.click()
                    else:
                        if state.get('stage')=='creating':
                            raise RuntimeError('前回の作成結果を一覧で確認できません。ページを再読込して再開してください。Botの重複作成を防ぐため停止しました。')
                        self.button('New Application|新しいアプリ|新規アプリ').click()
                        welcome=self.page.get_by_role('dialog')
                        if self.visible(welcome) and welcome.get_by_text('Welcome to the Developer Portal',exact=True).count():
                            login=welcome.get_by_role('button',name=re.compile('Log In|ログイン',re.I))
                            if login.count(): login.first.click()
                            raise RuntimeError('Discordへのログインが必要です。ログイン後にセットアップを再開してください。')
                        # Current Portal exposes this modal as a container, not role=dialog.
                        inputs=self.page.get_by_role('textbox',name=re.compile('^Name|^名前',re.I))
                        inputs.first.wait_for(state='visible',timeout=30000)
                        if inputs.count()!=1:
                            raise RuntimeError('アプリ名の入力欄を一意に確認できません。')
                        inputs.fill(name)
                        checks=self.page.get_by_role('checkbox')
                        if checks.count():
                            if not choice.get('developer_terms_accepted'):
                                raise RuntimeError('セットアップ画面でDiscord開発者規約への同意を確認してください。')
                            for n in range(checks.count()):
                                if not checks.nth(n).is_checked(): checks.nth(n).check()
                        self.checkpoint(stage='creating')
                        self.button('^Create$|^作成$').click()
                    self.until(lambda:re.search(r'/applications/(\d+)',self.page.url),
                               '専用アプリを作成しています。Discordの本人認証が出た場合は、その画面で完了してください。')
                    app=re.search(r'/applications/(\d+)',self.page.url)[1]
                    state=self.checkpoint(application=app,stage='created')
                app=state['application']
                if not re.fullmatch(r'\d{16,22}',str(app)):
                    raise RuntimeError('作成済みアプリIDが不正です。')
                phase='専用インストール設定'
                self.configure_installation(app)
                phase='Bot設定とトークン保存'
                self.page.goto(PORTAL+'/'+app+'/bot',wait_until='domcontentloaded')
                public=self.page.get_by_role('switch',name=re.compile('Public Bot|公開.*Bot|公開アプリは誰でも|Public applications',re.I))
                public.first.wait_for(state='visible',timeout=30000)
                if public.count()!=1:
                    raise RuntimeError('専用Botの公開設定を一意に確認できません。')
                checked=public.get_attribute('aria-checked')
                enabled=(checked=='true') if checked is not None else public.is_checked()
                if enabled:
                    public.press('Space')
                    save=self.button('Save Changes|変更を保存')
                    if self.visible(save):
                        save.click()
                        save.wait_for(state='hidden',timeout=30000)
                if not self.store.setting('discord_token',''):
                    add=self.button('^Add Bot$|Botを追加')
                    if self.visible(add):
                        add.click()
                        confirm=self.button('Yes, do it|はい.*追加')
                        if self.visible(confirm): confirm.click()
                    # Tokens are generated only for this installation's journaled app.
                    reset=self.button('Reset Token|トークンをリセット')
                    reset.wait_for(state='visible',timeout=30000)
                    captured={}
                    def token_ready():
                        # Read only a visible token field. No clipboard, screenshots or logs.
                        fields=self.page.locator('input[readonly]')
                        for index in range(fields.count()):
                            field=fields.nth(index)
                            if field.is_visible():
                                value=field.input_value()
                                if re.fullmatch(r'[A-Za-z\d_-]{20,}\.[A-Za-z\d_-]{5,}\.[A-Za-z\d_-]{20,}',value):
                                    captured['token']=value
                                    return True
                        # The live Portal now displays newly generated tokens as
                        # visible text, rather than a readonly input.
                        visible_tokens=self.page.get_by_text(re.compile(r'^[A-Za-z\d_-]{20,}\.[A-Za-z\d_-]{5,}\.[A-Za-z\d_-]{20,}$'))
                        for index in range(visible_tokens.count()):
                            field=visible_tokens.nth(index)
                            if field.is_visible():
                                value=field.inner_text().strip()
                                if re.fullmatch(r'[A-Za-z\d_-]{20,}\.[A-Za-z\d_-]{5,}\.[A-Za-z\d_-]{20,}',value):
                                    captured['token']=value
                                    return True
                        return False
                    reset.click()
                    confirm=self.button('^Yes, do it!?$|^実行します[！!]?$',)
                    self.until(lambda:self.visible(confirm) or token_ready(),
                               '接続トークンの発行を準備しています。',60)
                    if self.visible(confirm): confirm.click()
                    self.until(token_ready,'Discordが本人確認を求めた場合は、その画面で認証してください。パスワードはチャットへ書かないでください。')
                    self.store.set_setting('discord_token',protect(captured.pop('token')))
                    self.checkpoint(stage='token_saved')
                    # Navigate away immediately so the token is no longer displayed.
                    self.page.goto(PORTAL+'/'+app+'/information',wait_until='domcontentloaded')
                phase='サーバーへの導入'
                config=self.store.setting('discord',{})
                config['application']=str(app)
                self.store.set_setting('discord',config)
                self.install_server(app)
                self.outcome.emit(True,'サーバーへのBot導入を確認しました。管理メニューを準備しています。')
        except Exception as exc:
            safe=str(exc) if type(exc) is RuntimeError else type(exc).__name__
            self.outcome.emit(False,phase+'で停止しました: '+safe+'。設定画面から再開できます。')

