"""Visible Developer Portal setup; authentication is always performed by the owner."""
import re
import threading
import time
import json
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from .qt import QThread, Signal
from .secrets import protect,unprotect
from .diagnostics import Diagnostics

PORTAL='https://discord.com/developers/applications'


class ManualSetup(QThread):
    """Verify the manually installed Bot before replacing any local settings."""
    outcome=Signal(bool,str)

    def __init__(self,store,token,guild,parent=None):
        super().__init__(parent)
        self.store,self.token,self.guild=store,token,guild
        self.cancel=threading.Event()
        self.settings=None

    def discord_get(self,path):
        request=Request('https://discord.com/api/v10/'+path,
                        headers={'Authorization':'Bot '+self.token,'User-Agent':'NoteStockPublisher/2.0'})
        with urlopen(request,timeout=8) as response:
            return json.load(response)

    def run(self):
        try:
            if not re.fullmatch(r'[a-zA-Z0-9._-]{20,256}',self.token):
                raise ValueError('BotトークンをDiscordの「Bot」画面からコピーし直してください。')
            if not re.fullmatch(r'[0-9]{16,22}',self.guild):
                raise ValueError('Discordの導入先サーバーのIDを入力してください。')
            try:
                app=self.discord_get('oauth2/applications/@me')
            except HTTPError as exc:
                if exc.code not in {401,403}:
                    raise
                raise ValueError('Botトークンを確認できません。Discordの「Bot」画面からコピーし直してください。') from None
            application=str(app.get('id',''))
            if app.get('team') is not None or not re.fullmatch(r'[0-9]{16,22}',application):
                raise ValueError('自分のアカウントが個人で所有するBotを使ってください。')
            try:
                guild=self.discord_get('guilds/'+self.guild)
            except HTTPError as exc:
                if exc.code not in {403,404}:
                    raise
                raise ValueError('指定のサーバーにBotが入り、サーバーIDが合っていることを確認してください。') from None
            if str(guild.get('id',''))!=self.guild:
                raise ValueError('指定のサーバーにBotが導入されていることを確認してください。')
            if not self.cancel.is_set():
                self.settings=application,self.guild,protect(self.token)
                self.outcome.emit(True,'Botの導入を確認しました。管理メニューを準備しています。')
        except ValueError as exc:
            Diagnostics(self.store).error('discord_manual_setup_error',exc)
            self.outcome.emit(False,str(exc))
        except Exception as exc:
            Diagnostics(self.store).error('discord_manual_setup_error',exc)
            self.outcome.emit(False,'Discordへの接続確認に失敗しました。接続状況を確認してやり直してください。')
        finally:
            self.token=''


def save_manual_setup(store,application,guild,encrypted_token):
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        def read(key,default):
            row=connection.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else default

        config=read('discord',{})
        same_application=str(config.get('application'))==application
        same_guild=str(config.get('guild'))==guild
        values={}
        if not same_application:
            config={'application':application,'guild':guild}
            values={'discord_destinations':{},'discord_activity_oauth_ready':False}
            activity=read('remote_relogin',{})
            activity.update(schedule_verified=False,activity_confirmed=False)
            values['remote_relogin']=activity
        elif not same_guild:
            destinations=read('discord_destinations',{})
            set_destination(config,destinations,guild)
            values['discord_destinations']=destinations
        values.update(discord=config,discord_setup={'application':application,'stage':'installed','selected_guild':guild},
                      discord_token=encrypted_token,discord_enabled=True,
                      discord_onboarding={'enabled':True,'mode':'manual'})
        connection.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)',
                               [(key,json.dumps(value,ensure_ascii=False)) for key,value in values.items()])


def interrupted_browser_operation(exc):
    from playwright.sync_api import TimeoutError
    return isinstance(exc, TimeoutError) or type(exc).__name__=='TargetClosedError' or (
        type(exc).__module__.startswith('playwright.') and
        any(message in str(exc) for message in (
            'Execution context was destroyed', 'Cannot find context with specified id',
            'Target page, context or browser has been closed', 'Frame was detached')))


def select_destination(store,guild):
    """Keep per-server resources when the owner explicitly switches destination."""
    if not re.fullmatch(r'\d{16,22}',str(guild)):
        raise RuntimeError('導入先サーバーを確認できません。')
    config=store.setting('discord',{})
    destinations=store.setting('discord_destinations',{})
    set_destination(config,destinations,guild)
    store.set_setting('discord_destinations',destinations)
    store.set_setting('discord',config)


def set_destination(config,destinations,guild):
    if config.get('guild'):
        destinations[str(config['guild'])]={key:config[key] for key in ('channel','menu','category','installation') if key in config}
    if str(config.get('guild',''))!=str(guild):
        for key in ('channel','menu','category','installation'): config.pop(key,None)
        config.update(destinations.get(str(guild),{}))
    config['guild']=str(guild)


def replace_missing_application(store, application):
    """Forget the confirmed missing Bot while retaining its server and channels."""
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')

        def read(key, default):
            row=connection.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else default

        state=read('discord_setup',{})
        config=read('discord',{})
        if state.get('missing_application')!=application or state.get('application')!=application:
            raise ValueError('保存済みのDiscordアプリが変更されました。設定画面を開き直してください。')
        if config.get('application') and str(config['application'])!=application:
            raise ValueError('別のDiscordアプリが設定されています。')
        for key in ('application','missing_application','authentication_pending','selected_guild'):
            state.pop(key,None)
        state.update(stage='named',name='note管理')
        for key in ('application','owner','menu','installation'):
            config.pop(key,None)
        config['replaced_application']=application
        destinations=read('discord_destinations',{})
        for destination in destinations.values():
            destination.pop('menu',None)
            destination.pop('installation',None)
        activity=read('remote_relogin',{})
        activity.update(schedule_verified=False,activity_confirmed=False)
        for key,value in {'discord_setup':state,'discord':config,'discord_token':'',
                          'discord_destinations':destinations,'remote_relogin':activity,
                          'discord_activity_oauth_ready':False}.items():
            connection.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))


class PortalSetup(QThread):
    progress=Signal(str)
    action_required=Signal(str)
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
    def until(self,condition,message,seconds=600,*,after_auth=None,find_page=None,user_action=None):
        try:
            return self.wait_for_portal(condition,message,seconds,after_auth,find_page,user_action)
        finally:
            self.action_required.emit('')

    def wait_for_portal(self,condition,message,seconds,after_auth,find_page,user_action):
        self.progress.emit(message)
        end=time.monotonic()+seconds
        notice=''
        was_authenticating=False
        auth_dialog_closed=False
        reconnect_notice=False
        pending_action=''
        while time.monotonic()<end:
            if self.cancel.is_set(): raise RuntimeError('セットアップを中断しました。同じBotから再開できます。')
            try:
                page=find_page() if find_page else self.page
                if page is not None:
                    self.page=page
                    if condition(): return
                    auth=page.get_by_role('heading',name=re.compile('^多要素認証$|^Multi.Factor Authentication$',re.I))
                    if self.visible(auth):
                        was_authenticating=True
                        auth_dialog_closed=False
                        failed=page.get_by_text('パスワードが一致しません',exact=True)
                        current=('Discordの本人確認でパスワードが一致しません。Discord画面で現在の認証情報を確認してください。'
                                 if self.visible(failed) else 'Discordが本人確認を求めています。表示中のDiscord画面で認証してください。')
                        if current!=notice:
                            self.checkpoint(authentication_pending=True)
                            self.progress.emit(current)
                            notice=current
                    else:
                        captcha=page.locator('iframe[src*="hcaptcha.com"][title*="challenge" i]').first
                        current=('表示中のDiscord画面でCAPTCHAを完了してください。' if self.visible(captcha)
                                 else (user_action() if user_action else ''))
                        if was_authenticating:
                            was_authenticating=False
                            auth_dialog_closed=True
                            notice=''
                            if after_auth:
                                self.progress.emit(after_auth)
                    if current!=pending_action:
                        self.action_required.emit(current)
                        pending_action=current
                    page.wait_for_timeout(300)
                    reconnect_notice=False
                    continue
            except Exception as exc:
                if not find_page or not interrupted_browser_operation(exc):
                    raise
                if not reconnect_notice:
                    Diagnostics(self.store).error('discord_setup_connection_interrupted',exc)
            if not reconnect_notice:
                if pending_action:
                    self.action_required.emit('')
                    pending_action=''
                self.progress.emit('認証の完了後、同じBotの設定画面と接続情報が戻るのを待っています。')
                reconnect_notice=True
            self.cancel.wait(0.5)
        if auth_dialog_closed:
            raise RuntimeError('Botの接続情報の発行を確認できませんでした。Discordの画面にエラーがないか確認してください。')
        raise RuntimeError(message+' 時間切れになりました。再開してください。')
    def visible(self,locator):
        return locator.count()>0 and locator.first.is_visible()
    def button(self,pattern):
        return self.page.get_by_role('button',name=re.compile(pattern,re.I)).first
    def visible_bot_token(self,page):
        # Return only one visible credential from the verified Bot settings page.
        return page.evaluate(r'''() => {
            const pattern = /(?<![\w.-])[A-Za-z\d_-]{20,}\.[A-Za-z\d_-]{5,}\.[A-Za-z\d_-]{20,}(?![\w.-])/g;
            const visible = e => e.getClientRects().length && getComputedStyle(e).visibility === 'visible';
            const values = [];
            const text = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            for (let node = text.nextNode(); node; node = text.nextNode()) {
                const parent = node.parentElement;
                if (!parent || !visible(parent) || parent.closest('button,[role=button],script,style,template')) continue;
                for (const match of node.textContent.matchAll(pattern)) values.push(match[0]);
            }
            for (const input of document.querySelectorAll('input[readonly]')) {
                if (!visible(input)) continue;
                for (const match of input.value.matchAll(pattern)) {
                    if (match[0] === input.value) values.push(match[0]);
                }
            }
            const unique = [...new Set(values)];
            return unique.length === 1 ? unique[0] : null;
        }''')

    def confirm_creation_terms(self, form, submit, accepted):
        if not accepted:
            raise RuntimeError('noteストックの連携設定画面でDiscord開発者規約を確認し、同意してください。')
        # Discord can render a styled or redundant checkbox even when the form
        # already allows submission. In that case there is nothing to toggle.
        if submit.is_enabled(timeout=5000):
            return
        checks=form.get_by_role('checkbox')
        if checks.count()!=1:
            raise RuntimeError('Discordの作成画面で規約のチェック欄を一意に確認できませんでした。')
        if not checks.is_checked(timeout=5000):
            # The actual input can be covered by Discord's decorative control.
            checks.press('Space',timeout=5000)
        if not checks.is_checked(timeout=5000) or not submit.is_enabled(timeout=5000):
            raise RuntimeError('Discordの作成画面で規約の同意を反映できませんでした。')

    def configure_installation(self,app):
        self.page.goto(PORTAL+'/'+app+'/installation',wait_until='domcontentloaded')
        user=self.page.get_by_role('checkbox',name=re.compile('^User Install$|^ユーザーインストール$',re.I))
        missing=self.page.get_by_text(re.compile(
            rf'(?:ID\s*{re.escape(app)}\s*のアプリケーションが見つかりませんでした|'
            rf'Could not find (?:an |the )?application with (?:the )?ID\s*{re.escape(app)}\b)',re.I))
        user.or_(missing).filter(visible=True).first.wait_for(state='visible',timeout=30000)
        if self.visible(missing):
            state=self.store.setting('discord_setup',{})
            config=self.store.setting('discord',{})
            if state.get('application')==app and not (self.store.setting('discord_token','') or config.get('guild')):
                for key in ('application','authentication_pending'):
                    state.pop(key,None)
                state.update(stage='named',name='note管理')
                self.store.set_setting('discord_setup',state)
                raise RuntimeError('Discordで保存済みのアプリが見つかりません。未完了の作成記録をリセットしました。次回は新規作成から始めます。')
            if state.get('application')==app:
                self.checkpoint(missing_application=app)
            raise RuntimeError('保存済みのDiscordアプリが見つかりません。削除済みの場合は新しいBotで設定し直せます')
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
        self.checkpoint(stage='installation_configured',missing_application=None)
    def configure_activity_redirect(self,app):
        """PKCE needs no Client Secret or credential reset."""
        self.oauth_stage='OAuth2画面を開く'
        self.page.goto(PORTAL+'/'+str(app)+'/oauth2',wait_until='domcontentloaded')
        add=self.button('Redirect.*追加|Add Redirect|他を追加|Add Another')
        self.oauth_stage='Redirect追加ボタンを確認'
        add.wait_for(state='visible',timeout=30000)
        inputs=self.page.get_by_role('textbox',name=re.compile('リダイレクトURIを選択|Select a redirect URI',re.I))
        if any(inputs.nth(n).input_value().rstrip('/')=='https://127.0.0.1' for n in range(inputs.count())):
            self.store.set_setting('discord_activity_oauth_ready',True);return
        add.click()
        self.oauth_stage='Redirectの入力欄を確認'
        field=self.page.get_by_role('textbox',name=re.compile('リダイレクトURIを選択|Select a redirect URI',re.I))
        field=field.last
        field.wait_for(state='visible',timeout=10000)
        field.fill('https://127.0.0.1')
        # The portal commits a typed redirect on blur.
        field.press('Tab')
        self.oauth_stage='Redirectを保存'
        self.save_portal_changes()
        self.page.reload(wait_until='domcontentloaded')
        self.verify_redirect(inputs)
        self.store.set_setting('discord_activity_oauth_ready',True)
    def verify_redirect(self,inputs):
        inputs.first.wait_for(state='visible',timeout=15000)
        if not any(inputs.nth(n).input_value().rstrip('/')=='https://127.0.0.1' for n in range(inputs.count())):
            raise RuntimeError('再読み込み後にOAuth2 Redirectを確認できません。')
    def save_portal_changes(self):
        save=self.button('Save Changes|変更を保存|変更内容を保存|^保存$')
        save.wait_for(state='visible',timeout=10000)
        save.click()
        save.wait_for(state='hidden',timeout=15000)
    def configure_activity(self,app,url):
        base=PORTAL+'/'+str(app)+'/embedded'
        self.oauth_stage='Activityとスマホ対応'
        self.page.goto(base+'/settings',wait_until='domcontentloaded')
        enabled=self.page.get_by_role('switch',name=re.compile('^アクティビティを有効化$|^Enable Activity$',re.I))
        enabled.wait_for(state='visible',timeout=15000)
        if not enabled.is_checked():
            enabled.press('Space')
            self.save_portal_changes()
        changed=False
        for platform in ('iOS','Android'):
            self.oauth_stage=platform+'対応の確認'
            checkbox=self.page.get_by_role('checkbox',name=platform,exact=True)
            checkbox.wait_for(state='visible',timeout=15000)
            if not checkbox.is_checked():
                checkbox.press('Space')
                changed=True
        if changed:
            self.oauth_stage='対応プラットフォームを保存'
            self.save_portal_changes()
        self.page.reload(wait_until='domcontentloaded')
        enabled.wait_for(state='visible',timeout=15000)
        if not enabled.is_checked() or any(
            not self.page.get_by_role('checkbox',name=platform,exact=True).is_checked()
            for platform in ('iOS','Android')):
            raise RuntimeError('Activityのモバイル設定を保存できませんでした。')
        self.oauth_stage='Activityの中継URL'
        self.page.goto(base+'/url-mappings',wait_until='domcontentloaded')
        target=self.page.get_by_role('textbox',name=re.compile('^ターゲット$|^Target$',re.I)).first
        target.wait_for(state='visible',timeout=15000)
        hostname=url.removeprefix('https://')
        if target.input_value().removeprefix('https://').rstrip('/')!=hostname:
            target.fill(hostname)
            target.press('Tab')
            self.save_portal_changes()
        self.page.reload(wait_until='domcontentloaded')
        target.wait_for(state='visible',timeout=15000)
        if target.input_value().removeprefix('https://').rstrip('/')!=hostname:
            raise RuntimeError('Activityの中継URLを保存できませんでした。')
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
            def select_server():
                if not guild and r'^Continue$|^続行$' not in clicked and not candidate['guild']:
                    picker=self.page.get_by_role('combobox')
                    if self.visible(picker):
                        return 'Discord画面でBotの導入先サーバーを選んでください。'
                return ''
            self.until(continue_installation,'Botを自動導入しています。',
                       user_action=select_server)
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
            from .managed_browser import connect,startup_blank_page
            with sync_playwright() as pw:
                browser,context=connect(pw,self.store.root/'discord-browser',self.store.setting('browser','auto'))
                self.page=startup_blank_page(context) or context.new_page()
                self.page.goto(PORTAL,wait_until='domcontentloaded')
                self.page.bring_to_front()
                state=self.store.setting('discord_setup',{})
                phase='Discordログイン'
                login=self.button('^Log In$|^ログイン$')
                if self.visible(login):
                    login.click()
                self.until(lambda:'/login' not in self.page.url and self.visible(self.button('New Application|新しいアプリ|新規アプリ')),
                           'Discordへのログイン状態を確認しています。',
                           user_action=lambda: '表示中のDiscord画面でログインしてください。'
                           if '/login' in self.page.url or self.visible(login) else '')
                if not state.get('application'):
                    phase='専用アプリ作成'
                    if not state.get('name'):
                        state=self.checkpoint(name='note管理',stage='named')
                    name=state['name']
                    if state.get('stage')=='named' and re.fullmatch(r'note管理 [0-9a-f]{10}',name):
                        state=self.checkpoint(name='note管理',stage='named')
                        name=state['name']
                    # Only legacy unique names can identify an interrupted creation.
                    # A normal display name must never select an unrelated app.
                    existing=self.page.get_by_text(name,exact=True)
                    if re.fullmatch(r'note管理 [0-9a-f]{10}',name) and self.visible(existing):
                        existing.first.click()
                    else:
                        if state.get('stage')=='creating':
                            raise RuntimeError('前回の作成結果を一覧で確認できません。ページを再読込して再開してください。Botの重複作成を防ぐため停止しました。')
                        self.progress.emit('Discordの「アプリを新規作成する」画面を開きます。「作成」はnoteストックが押します。そのままお待ちください。')
                        self.button('New Application|新しいアプリ|新規アプリ').click()
                        welcome=self.page.get_by_role('dialog')
                        if self.visible(welcome) and welcome.get_by_text('Welcome to the Developer Portal',exact=True).count():
                            login=welcome.get_by_role('button',name=re.compile('Log In|ログイン',re.I))
                            if login.count(): login.first.click()
                            raise RuntimeError('Discordへのログインが必要です。ログイン後にセットアップを再開してください。')
                        # Current Portal exposes this modal as a container, not role=dialog.
                        phase='専用アプリ名の入力'
                        inputs=self.page.get_by_role('textbox',name=re.compile('^Name|^名前',re.I)).filter(visible=True)
                        inputs.first.wait_for(state='visible',timeout=30000)
                        if inputs.count()!=1:
                            raise RuntimeError('アプリ名の入力欄を一意に確認できません。')
                        inputs.fill(name)
                        create_button=self.page.get_by_role('button',name=re.compile(r'^(Create|作成)$',re.I))
                        form=inputs.locator('xpath=ancestor::*').filter(has=create_button).last
                        submit=form.get_by_role('button',name=re.compile(r'^(Create|作成)$',re.I))
                        phase='作成画面での規約確認'
                        self.confirm_creation_terms(form,submit,choice.get('developer_terms_accepted',False))
                        phase='専用アプリの作成ボタン'
                        submit.click(trial=True,timeout=10000)
                        self.checkpoint(stage='creating')
                        submit.click(timeout=10000)
                    self.until(lambda:re.search(r'/applications/(\d+)',self.page.url),
                               '専用アプリを作成しています。Discordの本人認証が出た場合は、その画面で完了してください。')
                    app=re.search(r'/applications/(\d+)',self.page.url)[1]
                    state=self.checkpoint(application=app,stage='created')
                app=state['application']
                if not re.fullmatch(r'\d{16,22}',str(app)):
                    raise RuntimeError('作成済みアプリIDが不正です。')
                bot_url=PORTAL+'/'+app+'/bot'
                if (state.get('authentication_pending') or state.get('stage')=='owner_authentication') and not self.store.setting('discord_token',''):
                    for previous in context.pages:
                        if previous.is_closed() or previous.url.rstrip('/')!=bot_url:
                            continue
                        token=self.visible_bot_token(previous)
                        if token:
                            self.store.set_setting('discord_token',protect(token))
                            self.checkpoint(stage='token_saved',authentication_pending=False)
                            previous.goto(PORTAL+'/'+app+'/information',wait_until='domcontentloaded')
                            break
                phase='専用インストール設定'
                self.configure_installation(app)
                phase='Bot設定とトークン保存'
                self.page.goto(bot_url,wait_until='domcontentloaded')
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
                    def find_bot_page():
                        nonlocal browser,context
                        if not browser.is_connected():
                            from .managed_browser import BrowserNotRunning
                            try:
                                browser,context=connect(pw,self.store.root/'discord-browser',
                                    self.store.setting('browser','auto'),allow_launch=False)
                            except BrowserNotRunning:
                                return None
                        candidates=[page for page in context.pages
                                    if not page.is_closed() and page.url.rstrip('/')==bot_url]
                        if self.page in candidates:
                            return self.page
                        if len(candidates)==1:
                            return candidates[0]
                        # Keep dispatching browser events while the original tab is absent.
                        with context.expect_page(timeout=500):
                            pass
                        return None

                    def token_ready():
                        token=self.visible_bot_token(self.page)
                        if not token:
                            return False
                        captured['token']=token
                        return True
                    confirmation=r'^Yes, do it!?$|^実行します[！!]?$'
                    submitted=False
                    def issue_once():
                        nonlocal submitted
                        if token_ready(): return True
                        confirm=self.button(confirmation)
                        if not submitted and self.visible(confirm):
                            submitted=True
                            confirm.click(timeout=5000)
                        return False

                    try:
                        reset.click(timeout=5000)
                    except Exception as exc:
                        if not interrupted_browser_operation(exc): raise
                    self.until(issue_once,
                               'Discordが本人確認を求めた場合は、その画面で認証してください。',
                               after_auth='本人確認の画面が閉じました。Botの接続情報が発行されたか確認しています。',
                               find_page=find_bot_page)
                    self.store.set_setting('discord_token',protect(captured.pop('token')))
                    self.checkpoint(stage='token_saved',authentication_pending=False)
                    # Navigate away immediately so the token is no longer displayed.
                    self.page.goto(PORTAL+'/'+app+'/information',wait_until='domcontentloaded')
                phase='サーバーへの導入'
                config=self.store.setting('discord',{})
                config['application']=str(app)
                self.store.set_setting('discord',config)
                self.install_server(app)
                self.outcome.emit(True,'サーバーへのBot導入を確認しました。管理メニューを準備しています。')
        except Exception as exc:
            Diagnostics(self.store).error('discord_setup_error',exc,stage=phase)
            safe=str(exc) if type(exc) is RuntimeError else type(exc).__name__
            if safe=='TargetClosedError':
                safe='操作していたDiscordのタブ、またはブラウザーとの接続が閉じました'
            self.outcome.emit(False,phase+'で停止しました: '+safe+'。設定画面から再開できます。')

class ActivityOAuthSetup(PortalSetup):
    """Upgrade an existing installation without changing tokens, secrets or servers."""
    def run(self):
        try:
            from playwright.sync_api import sync_playwright
            from .managed_browser import connect,startup_blank_page
            app=str(self.store.setting('discord',{}).get('application',''))
            if not app.isdigit():raise RuntimeError('先にDiscord連携を設定してください。')
            with sync_playwright() as pw:
                browser,context=connect(pw,self.store.root/'discord-browser',self.store.setting('browser','auto'))
                self.page=startup_blank_page(context) or context.new_page()
                self.page.bring_to_front()
                self.configure_activity_redirect(app)
                if getattr(self,'relay_url',None):
                    self.configure_activity(app,self.relay_url)
                    self.oauth_stage='HTTPS中継の接続確認'
                    from .schedule_activity import probe_relay
                    probe_relay(self.store,self.relay_url)
                message=('Discord内の予約画面を準備しました。' if getattr(self,'relay_url',None)
                         else 'DiscordのOAuth2 Redirectを保存し、再読み込み後に確認しました。')
                self.outcome.emit(True,message)
        except Exception as exc:
            self.outcome.emit(False,str(exc) if isinstance(exc,RuntimeError) else getattr(self,'oauth_stage','OAuth2初期設定')+'で停止しました（'+type(exc).__name__+'）。表示されたDiscord画面を確認してください。')

