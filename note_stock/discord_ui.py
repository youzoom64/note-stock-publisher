"""Discord onboarding and status controls, separate from article editing."""
import re
import sqlite3
from pathlib import Path

from PySide6.QtCore import QTimer,Qt,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
    QDialog,QRadioButton,QButtonGroup,QLineEdit,QStackedWidget,QCheckBox,QTableWidget,QTableWidgetItem,QHeaderView,QMessageBox,
    QTextBrowser)
from .discord_commands import COMMANDS,command_names


class DiscordCommandDialog(QDialog):
    def __init__(self,parent=None,values=None):
        super().__init__(parent)
        self.setWindowTitle('Discordコマンド名の設定')
        self.resize(650,620)
        layout=QVBoxLayout(self)
        text=QLabel('Discordで / の後に入力する名前を変更できます。日本語も使えます。\n'
                    '管理チャンネルに何か送信するだけでも、操作メニューを呼び出せます。')
        text.setWordWrap(True); layout.addWidget(text)
        self.table=QTableWidget(len(COMMANDS),2)
        self.table.setHorizontalHeaderLabels(['機能','コマンド名（/ は不要）'])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setAccessibleName('Discordコマンド名')
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)
        self.keys=list(COMMANDS)
        names=command_names(values)
        for row,key in enumerate(self.keys):
            label=QTableWidgetItem(COMMANDS[key][0]); label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row,0,label)
            self.table.setItem(row,1,QTableWidgetItem(names[key]))
        self.error=QLabel(''); self.error.setWordWrap(True); layout.addWidget(self.error)
        row=QHBoxLayout(); layout.addLayout(row)
        reset=QPushButton('初期名に戻す'); reset.clicked.connect(self.reset_names); row.addWidget(reset)
        cancel=QPushButton('キャンセル'); cancel.clicked.connect(self.reject); row.addWidget(cancel)
        self.save=QPushButton('保存して反映'); self.save.clicked.connect(self.validate_and_accept); row.addWidget(self.save)
    def reset_names(self):
        for row,key in enumerate(self.keys): self.table.item(row,1).setText(COMMANDS[key][1])
        self.error.clear()
    def selection(self):
        return command_names({key:self.table.item(row,1).text() for row,key in enumerate(self.keys)})
    def validate_and_accept(self):
        try: self.selection()
        except ValueError as exc:
            self.error.setText(str(exc)); return
        self.accept()


class DiscordSetupDialog(QDialog):
    """No external work starts until the owner explicitly finishes both choices."""
    def __init__(self,parent=None,state=None):
        super().__init__(parent)
        state=state or {}
        self.manual_guide=None
        self.setWindowTitle('Discord連携の設定')
        self.setMinimumWidth(480)
        layout=QVBoxLayout(self)
        self.pages=QStackedWidget()
        layout.addWidget(self.pages)
        first=QWidget(); form=QVBoxLayout(first)
        title=QLabel('Discord連携しますか？')
        title.setObjectName('section')
        form.addWidget(title)
        explanation=QLabel('連携を使うと、iPhoneやスマホを使用して外出先から操作できます。\n'
                           'Discordから記事の確認や予約投稿を操作できます。操作を受けるPCは起動したままにしてください。\n'
                           '連携しなくても、PCで編集・投稿管理を使えます。後から設定もできます。')
        explanation.setWordWrap(True); form.addWidget(explanation)
        instruction=QLabel('Discordのログイン・本人認証はご自身で')
        instruction.setStyleSheet('font-size:22px; font-weight:700;')
        instruction.setWordWrap(True)
        form.addWidget(instruction)
        overview=QLabel('自動を選ぶと、Discordのログインと本人認証を済ませた後、'
                         'Botの作成・導入・管理メニューの準備はすべてnoteストックが行います。\n\n'
                         '手動を押すと、操作方法をまとめたHTMLがすぐに開きます。'
                         '手順を見ながらBotを作成・導入してください。')
        overview.setWordWrap(True)
        overview.setObjectName('notice')
        form.addWidget(overview)
        self.use=QRadioButton('自動で設定'); self.skip=QRadioButton('今は使わない')
        self.integration=QButtonGroup(self)
        for radio in (self.use,self.skip):
            self.integration.addButton(radio)
        form.addWidget(self.use)
        self.manual=QPushButton('手動で設定（HTML手順を開く）')
        self.manual.clicked.connect(self.begin_manual)
        form.addWidget(self.manual)
        form.addWidget(self.skip)
        self.pages.addWidget(first)
        second=QWidget(); form=QVBoxLayout(second)
        form.addWidget(QLabel('Botを使うサーバーを選んでください'))
        advice=QLabel('よく分からない場合は、新規作成を選択してください。')
        advice.setWordWrap(True); form.addWidget(advice)
        self.new=QRadioButton('専用サーバーを新規作成する')
        self.existing=QRadioButton('既存のサーバーを選ぶ')
        self.destinations=QButtonGroup(self)
        for radio in (self.new,self.existing):
            self.destinations.addButton(radio); form.addWidget(radio)
        self.name=QLineEdit(state.get('server_name','note管理'))
        self.name.setMaxLength(100); self.name.setAccessibleName('新しいサーバー名')
        form.addWidget(QLabel('新しいサーバー名')); form.addWidget(self.name)
        self.name.setEnabled(False)
        self.new.toggled.connect(self.name.setEnabled)
        self.guidelines=QCheckBox('Discordコミュニティガイドラインに同意してサーバーを作成する')
        self.guidelines.setVisible(False)
        form.addWidget(self.guidelines)
        self.guidelines_link=QLabel('<a href="https://discord.com/guidelines">Discordコミュニティガイドラインを確認</a>')
        self.guidelines_link.setOpenExternalLinks(True)
        self.guidelines_link.setVisible(False); form.addWidget(self.guidelines_link)
        self.new.toggled.connect(self.guidelines.setVisible)
        self.new.toggled.connect(self.guidelines_link.setVisible)
        self.guidelines.toggled.connect(self.update_controls)
        self.login_guide=QLabel(
            '次に、ブラウザーでDiscordの「Developer Portal（開発者ポータル）」が開きます。\n'
            'Discordのログインと本人認証（パスワード・二段階認証・CAPTCHA）を済ませてください。'
            'その後のBotの作成・導入・管理メニューの準備はnoteストックが行います。\n'
            '既存サーバーを使う場合は、導入画面でサーバーを選んでください。')
        self.login_guide.setWordWrap(True)
        self.login_guide.setObjectName('notice')
        form.addWidget(self.login_guide)
        self.developer_terms=QCheckBox('開発者向け利用規約・開発者ポリシーに同意し、専用Botを自動作成・導入する')
        self.developer_terms.setVisible(not state.get('has_application',False))
        self.needs_bot=not state.get('has_application',False)
        form.addWidget(self.developer_terms)
        terms=QLabel('<a href="https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service">開発者向け利用規約</a> ／ '
                     '<a href="https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy">開発者ポリシー</a>')
        terms.setOpenExternalLinks(True); terms.setVisible(self.needs_bot); form.addWidget(terms)
        self.developer_terms.toggled.connect(self.update_controls)
        self.pages.addWidget(second)
        manual_page=QWidget()
        form=QVBoxLayout(manual_page)
        instructions=QLabel('HTMLの手順に沿ってBotをサーバーに追加し、接続情報を入力してください。')
        instructions.setWordWrap(True)
        form.addWidget(instructions)
        guide=QPushButton('HTML手順をもう一度開く')
        guide.clicked.connect(self.open_manual_guide)
        form.addWidget(guide)
        form.addWidget(QLabel('Botトークン'))
        self.manual_token=QLineEdit()
        self.manual_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.manual_token.setAccessibleName('Botトークン')
        form.addWidget(self.manual_token)
        form.addWidget(QLabel('導入先のサーバーID'))
        self.manual_guild=QLineEdit()
        self.manual_guild.setMaxLength(22)
        self.manual_guild.setAccessibleName('導入先のサーバーID')
        form.addWidget(self.manual_guild)
        form.addStretch()
        self.pages.addWidget(manual_page)
        row=QHBoxLayout(); layout.addLayout(row)
        self.back=QPushButton('戻る'); self.back.clicked.connect(lambda:self.pages.setCurrentIndex(0))
        cancel=QPushButton('キャンセル'); cancel.clicked.connect(self.reject)
        self.next=QPushButton('次へ'); self.next.clicked.connect(self.advance)
        for button in (self.back,cancel,self.next): row.addWidget(button)
        for radio in (self.use,self.skip,self.new,self.existing): radio.toggled.connect(self.update_controls)
        self.name.textChanged.connect(self.update_controls)
        self.manual_token.textChanged.connect(self.update_controls)
        self.manual_guild.textChanged.connect(self.update_controls)
        self.pages.currentChanged.connect(self.update_controls)
        self.update_controls()
    def update_controls(self,*args):
        page=self.pages.currentIndex()
        self.back.setVisible(page!=0)
        if page==2:
            self.next.setText('保存して接続')
            self.next.setEnabled(bool(self.manual_token.text().strip() and
                                      re.fullmatch(r'[0-9]{16,22}',self.manual_guild.text().strip())))
            return
        server=page==1
        self.next.setText('設定を開始' if server else ('完了' if self.skip.isChecked() else '次へ'))
        self.next.setEnabled(((not self.needs_bot or self.developer_terms.isChecked()) and
                             (self.existing.isChecked() or (self.new.isChecked() and len(self.name.text().strip())>=2 and self.guidelines.isChecked())))
                             if server else (self.use.isChecked() or self.skip.isChecked()))
    def advance(self):
        if self.pages.currentIndex()==0 and self.use.isChecked(): self.pages.setCurrentIndex(1)
        else: self.accept()
    def selection(self):
        if self.pages.currentIndex()==2:
            return {'enabled':True,'mode':'manual'}
        return {'enabled':self.use.isChecked(),'destination':'new' if self.new.isChecked() else 'existing',
                'server_name':self.name.text().strip(),'guidelines_accepted':self.new.isChecked() and self.guidelines.isChecked(),
                'developer_terms_accepted':self.developer_terms.isChecked(),'mode':'automatic'}
    def begin_manual(self):
        self.pages.setCurrentIndex(2)
        self.open_manual_guide()
    def open_manual_guide(self):
        if self.manual_guide is None:
            try:
                html=Path(__file__).with_name('discord-manual-setup.html').read_text(encoding='utf-8')
            except OSError:
                QMessageBox.warning(self,'Discordの手動設定','HTML手順を読み込めませんでした。')
                return
            self.manual_guide=QDialog(self)
            self.manual_guide.setWindowTitle('Discordを手動で設定する手順')
            self.manual_guide.resize(720,600)
            layout=QVBoxLayout(self.manual_guide)
            browser=QTextBrowser()
            browser.setOpenLinks(False)
            browser.setHtml(html)
            browser.anchorClicked.connect(self.manual_guide_link)
            layout.addWidget(browser)
            close=QPushButton('閉じる')
            close.clicked.connect(self.manual_guide.close)
            layout.addWidget(close)
        self.manual_guide.show()
        self.manual_guide.raise_()
        self.manual_guide.activateWindow()
    def manual_guide_link(self,url):
        if url==QUrl('note-stock:automatic'):
            self.manual_guide.close()
            self.manual_token.clear()
            self.use.setChecked(True)
            self.pages.setCurrentIndex(1)
            self.activateWindow()
        elif url.scheme()=='https' and url.host() in {'discord.com','docs.discord.com','support-dev.discord.com'}:
            if not QDesktopServices.openUrl(url):
                QMessageBox.warning(self.manual_guide,'Discordの手動設定','Discordのページを開けませんでした。')


class DiscordPanel(QWidget):
    def __init__(self,window,connect_on_start=True):
        super().__init__(window)
        self.window,self.store=window,window.store
        self.setup_worker=None
        self.manual_pending=None
        self.bot=None
        self.connection_action=None
        layout=QVBoxLayout(self)
        title=QLabel('スマホのDiscordからnoteを管理')
        title.setObjectName('section')
        layout.addWidget(title)
        text=QLabel('Discord連携は自動設定と手動設定を選べます。\n'
                    '以後はPCを起動したまま、Discordの「note管理」から操作できます。PC停止・スリープ中は動作しません。')
        text.setWordWrap(True)
        layout.addWidget(text)
        self.enabled=QCheckBox('Discord連携')
        self.enabled.setChecked(self.store.setting('discord_enabled',bool(self.store.setting('discord_token',''))))
        self.enabled.clicked.connect(self.toggle_integration)
        layout.addWidget(self.enabled)
        row=QHBoxLayout()
        setup_button=QPushButton('Discord連携を設定・変更')
        setup_button.clicked.connect(self.setup)
        self.connect_button=QPushButton('接続・再接続')
        self.connect_button.clicked.connect(self.connect_bot)
        self.disconnect_button=QPushButton('Discord接続を停止')
        self.disconnect_button.clicked.connect(self.request_disconnect)
        for button in (setup_button,self.connect_button,self.disconnect_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.open_application=QPushButton('Discordアプリページを開く')
        self.open_application.clicked.connect(self.open_discord_application)
        layout.addWidget(self.open_application)
        commands=QPushButton('コマンド名の設定')
        commands.clicked.connect(self.configure_commands)
        layout.addWidget(commands)
        self.status=QLabel('未設定')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        reservation=QPushButton('追加設定：予約用Web画面の接続')
        reservation.clicked.connect(self.configure_reservation)
        layout.addWidget(reservation)
        layout.addWidget(QLabel('管理サーバー（Bot接続後に選択）'))
        self.guilds=QComboBox()
        self.guilds.setProperty('cursorHelp','管理メニューを作成するDiscordサーバーを選びます。')
        self.guilds.setPlaceholderText('Botを導入して接続すると表示されます')
        layout.addWidget(self.guilds)
        choose=QPushButton('このサーバーに管理メニューを作成')
        choose.clicked.connect(self.choose_guild)
        layout.addWidget(choose)
        layout.addStretch()
        self.poll=QTimer(self)
        self.poll.timeout.connect(self.refresh)
        self.poll.start(2000)
        self.connection_poll=QTimer(self)
        self.connection_poll.setInterval(150)
        self.connection_poll.timeout.connect(self.update_connection_action)
        self.guild_snapshot=None
        self.refresh()
        if connect_on_start and self.store.setting('discord_enabled',True) and self.store.setting('discord_token',''):
            QTimer.singleShot(0,self.connect_bot)
    def refresh(self):
        self.open_application.setVisible(bool(self.discord_application_id()))
        if self.manual_pending is None and not (self.setup_worker and self.setup_worker.isRunning()):
            self.status.setText(self.store.setting('discord_connection','未接続'))
        guilds=self.store.setting('discord_guilds',[])
        if guilds!=self.guild_snapshot:
            selected=self.guilds.currentData()
            self.guilds.clear()
            for guild in guilds:
                self.guilds.addItem(guild['name'],guild['id'])
            configured=self.store.setting('discord',{}).get('guild')
            self.guilds.setCurrentIndex(self.guilds.findData(selected or configured))
            self.guild_snapshot=guilds
    def discord_application_id(self):
        installed=self.store.setting('discord',{}).get('application')
        state=self.store.setting('discord_setup',{})
        return next((str(value) for value in (installed,state.get('application'))
                     if value!=state.get('missing_application') and re.fullmatch(r'[0-9]{16,22}',str(value))),None)
    def open_discord_application(self):
        from .discord_setup import PORTAL
        app=self.discord_application_id()
        if app and not QDesktopServices.openUrl(QUrl(f'{PORTAL}/{app}/information')):
            self.window.notify('Discordアプリページをブラウザーで開けませんでした。',True)
    def configure_commands(self):
        dialog=DiscordCommandDialog(self,self.store.setting('discord_commands',{}))
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        self.store.set_setting('discord_commands',dialog.selection())
        if self.store.setting('discord_enabled',False) and self.store.setting('discord_token',''):
            self.connect_bot()
        else:
            self.window.notify('コマンド名を保存しました。次回のDiscord接続時に反映します。')
    def configure_reservation(self):
        from .schedule_activity_ui import ActivitySettings
        ActivitySettings(self.store,self).exec()
    def setup(self):
        if self.manual_pending is not None or (self.setup_worker and self.setup_worker.isRunning()): return
        from .discord_setup import PortalSetup, replace_missing_application
        setup_state=self.store.setting('discord_setup',{})
        missing=setup_state.get('missing_application')
        state=dict(self.store.setting('discord_onboarding',{}))
        state['has_application']=bool(setup_state.get('application')) and not missing
        dialog=DiscordSetupDialog(self,state)
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            self.enabled.setChecked(self.store.setting('discord_enabled',bool(self.store.setting('discord_token',''))))
            return
        choice=dialog.selection()
        if choice.get('mode')=='manual':
            self.start_manual_setup(dialog.manual_token.text().strip(),dialog.manual_guild.text().strip())
            dialog.manual_token.clear()
            return
        replace=False
        if choice['enabled'] and missing and missing==setup_state.get('application'):
            prompt=QMessageBox(self)
            prompt.setWindowTitle('Discordアプリが見つかりません')
            prompt.setText('Discord側でBotを削除した場合は、新しいBotで設定し直してください。前のサーバー設定は残ります。'
                           '\n別のDiscordアカウントで開いていた場合は、同じBotを再確認できます。')
            create=prompt.addButton('新しいBotで設定',QMessageBox.ButtonRole.AcceptRole)
            retry=prompt.addButton('同じBotを再確認',QMessageBox.ButtonRole.ActionRole)
            cancel=prompt.addButton(QMessageBox.StandardButton.Cancel)
            prompt.setDefaultButton(cancel)
            prompt.exec()
            replace=prompt.clickedButton() is create
            if not replace and prompt.clickedButton() is not retry:
                self.enabled.setChecked(self.store.setting('discord_enabled',bool(self.store.setting('discord_token',''))))
                return
        if replace and choice['enabled']:
            try:
                self.stop_bot()
                replace_missing_application(self.store,missing)
            except (ValueError,OSError) as exc:
                self.window.notify(str(exc),True)
                return
        self.store.set_setting('discord_onboarding',choice)
        self.store.set_setting('discord_enabled',choice['enabled'])
        self.enabled.setChecked(choice['enabled'])
        if not choice['enabled']:
            self.stop_bot()
            self.store.set_setting('discord_connection','Discord連携は使わない設定です。後から設定できます。')
            self.refresh()
            return
        self.stop_bot()
        self.setup_worker=PortalSetup(self.store,self)
        self.setup_worker.progress.connect(self.status.setText)
        self.setup_worker.action_required.connect(self.show_required_action)
        self.setup_worker.outcome.connect(self.setup_done)
        self.setup_worker.finished.connect(self.offer_missing_application_recovery)
        self.setup_worker.start()

    def show_required_action(self,message):
        status_bar=getattr(self.window,'statusBar',None)
        if status_bar:
            status_bar().wait_for_action('discord',message)

    def start_manual_setup(self,token,guild):
        from .discord_setup import ManualSetup
        self.status.setText('Botのトークンとサーバーへの導入を確認しています…')
        self.setup_worker=ManualSetup(self.store,token,guild,self)
        self.setup_worker.outcome.connect(self.manual_setup_done)
        self.setup_worker.start()

    def manual_setup_done(self,ok,message):
        worker=self.sender()
        if worker is not self.setup_worker or getattr(self.window,'quitting',False) or worker.cancel.is_set():
            return
        if ok:
            self.manual_pending=(worker.settings,message)
            if self.bot and self.bot.is_alive():
                self.stop_bot()
                self.status.setText('前のDiscord接続を終了しています…')
            self.finish_manual_setup()
            return
        self.manual_setup_error(message)

    def finish_manual_setup(self):
        if self.manual_pending is None:
            return
        if self.bot and self.bot.is_alive():
            QTimer.singleShot(150,self.finish_manual_setup)
            return
        settings,message=self.manual_pending
        self.manual_pending=None
        from .discord_setup import save_manual_setup
        try:
            save_manual_setup(self.store,*settings)
        except (RuntimeError,OSError,ValueError,sqlite3.Error) as exc:
            self.manual_setup_error('Discordの接続設定を保存できませんでした。'+str(exc))
            return
        self.enabled.setChecked(True)
        self.setup_done(True,message)

    def manual_setup_error(self,message):
        self.enabled.setChecked(self.store.setting('discord_enabled',bool(self.store.setting('discord_token',''))))
        self.status.setText(message)
        self.window.notify(message,True)

    def offer_missing_application_recovery(self):
        if not getattr(self.window,'quitting',False) and self.store.setting('discord_setup',{}).get('missing_application'):
            self.setup()
    def toggle_integration(self,enabled):
        self.enabled.setChecked(enabled)
        if not enabled:
            self.manual_pending=None
            if self.setup_worker and self.setup_worker.isRunning(): self.setup_worker.cancel.set()
            self.store.set_setting('discord_enabled',False)
            self.stop_bot()
            self.store.set_setting('discord_connection','Discord連携は停止中です。設定は保存されています。')
            self.refresh()
            return
        config=self.store.setting('discord',{})
        if not self.store.setting('discord_token','') or not config.get('guild'):
            self.setup()
            return
        self.store.set_setting('discord_enabled',True)
        self.connect_bot()
    def setup_done(self,ok,message):
        self.store.set_setting('discord_connection',message)
        self.status.setText(message)
        if ok and self.store.setting('discord_enabled',True):
            self.connect_bot()
    def connect_bot(self):
        if self.manual_pending is not None:
            return
        if not self.store.setting('discord_enabled',True):
            self.window.notify('Discord連携は使わない設定です。セットアップから変更できます。',True)
            return
        if not self.store.setting('discord_token',''):
            self.window.notify('先に「Discord連携を設定・変更」を実行してください。',True)
            return
        if self.bot and self.bot.is_alive():
            self.bot.stop()
            self.store.set_setting('discord_connection','接続を終了しています。')
            self.show_connection_action('reconnecting')
            return
        self.start_connection()

    def start_connection(self):
        from .discord_bot import BotThread
        from .schedule_activity import ensure_host
        self.show_connection_action('connecting')
        try:
            ensure_host(self.store)
        except (RuntimeError,ValueError) as exc:
            self.window.notify(str(exc),True)
        self.store.set_setting('discord_connection','接続中…')
        self.bot=BotThread(self.store)
        self.bot.start()

    def show_connection_action(self,action):
        self.connection_action=action
        self.connect_button.setText({'connecting':'接続中…','reconnecting':'再接続中…'}.get(action,'接続・再接続'))
        self.disconnect_button.setText('切断中…' if action=='stopping' else 'Discord接続を停止')
        self.connect_button.setEnabled(action is None)
        self.disconnect_button.setEnabled(action!='stopping')
        if action:
            self.connection_poll.start()
        else:
            self.connection_poll.stop()

    def request_disconnect(self):
        self.show_connection_action('stopping')
        self.store.set_setting('discord_connection','接続を終了しています。')
        if self.bot:
            self.bot.stop()

    def update_connection_action(self):
        action=self.connection_action
        alive=bool(self.bot and self.bot.is_alive())
        if action=='reconnecting' and not alive:
            self.start_connection()
        elif action=='stopping' and not alive:
            self.stop_bot()
            self.refresh()
        elif action=='connecting':
            status=self.store.setting('discord_connection','')
            if not alive or status not in {'接続中','接続中…','切断・再接続待ち'}:
                self.show_connection_action(None)
                self.refresh()

    def stop_bot(self):
        from .schedule_activity import stop_host
        stop_host(self.store)
        if self.bot: self.bot.stop()
        self.store.set_setting('discord_connection','停止')
        self.show_connection_action(None)
    def choose_guild(self):
        gid=self.guilds.currentData()
        if not gid:
            self.window.notify('Botのサーバー導入を承認してから接続してください。',True)
            return
        config=self.store.setting('discord',{})
        from .discord_setup import select_destination
        select_destination(self.store,gid)
        self.connect_bot()
    def shutdown(self):
        self.manual_pending=None
        self.stop_bot()
        if self.setup_worker and self.setup_worker.isRunning():
            self.setup_worker.cancel.set()
            return False
        return True
