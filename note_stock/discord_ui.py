"""Discord onboarding and status controls, separate from article editing."""
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
    QDialog,QRadioButton,QButtonGroup,QLineEdit,QStackedWidget,QCheckBox,QTableWidget,QTableWidgetItem,QHeaderView)
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
        self.setWindowTitle('Discord連携の設定')
        self.setMinimumWidth(480)
        layout=QVBoxLayout(self)
        self.pages=QStackedWidget()
        layout.addWidget(self.pages)
        first=QWidget(); form=QVBoxLayout(first)
        form.addWidget(QLabel('Discord連携しますか？'))
        explanation=QLabel('連携を使うと、iPhoneやスマホを使用して外出先から操作できます。\n'
                           'Discordから記事の確認や予約投稿を操作できます。操作を受けるPCは起動したままにしてください。\n'
                           '連携しなくても、PCで編集・投稿管理を使えます。後から設定もできます。')
        explanation.setWordWrap(True); form.addWidget(explanation)
        self.use=QRadioButton('使う'); self.skip=QRadioButton('今は使わない')
        self.integration=QButtonGroup(self)
        for radio in (self.use,self.skip):
            self.integration.addButton(radio); form.addWidget(radio)
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
        self.developer_terms=QCheckBox('開発者向け利用規約・開発者ポリシーに同意し、専用Botを自動作成・導入する')
        self.developer_terms.setVisible(not state.get('has_application',False))
        self.needs_bot=not state.get('has_application',False)
        form.addWidget(self.developer_terms)
        terms=QLabel('<a href="https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service">開発者向け利用規約</a> ／ '
                     '<a href="https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy">開発者ポリシー</a>')
        terms.setOpenExternalLinks(True); terms.setVisible(self.needs_bot); form.addWidget(terms)
        self.developer_terms.toggled.connect(self.update_controls)
        self.pages.addWidget(second)
        row=QHBoxLayout(); layout.addLayout(row)
        self.back=QPushButton('戻る'); self.back.clicked.connect(lambda:self.pages.setCurrentIndex(0))
        cancel=QPushButton('キャンセル'); cancel.clicked.connect(self.reject)
        self.next=QPushButton('次へ'); self.next.clicked.connect(self.advance)
        for button in (self.back,cancel,self.next): row.addWidget(button)
        for radio in (self.use,self.skip,self.new,self.existing): radio.toggled.connect(self.update_controls)
        self.name.textChanged.connect(self.update_controls)
        self.pages.currentChanged.connect(self.update_controls)
        self.update_controls()
    def update_controls(self,*args):
        server=self.pages.currentIndex()==1
        self.back.setVisible(server)
        self.next.setText('設定を開始' if server else ('完了' if self.skip.isChecked() else '次へ'))
        self.next.setEnabled(((not self.needs_bot or self.developer_terms.isChecked()) and
                             (self.existing.isChecked() or (self.new.isChecked() and len(self.name.text().strip())>=2 and self.guidelines.isChecked())))
                             if server else (self.use.isChecked() or self.skip.isChecked()))
    def advance(self):
        if self.pages.currentIndex()==0 and self.use.isChecked(): self.pages.setCurrentIndex(1)
        else: self.accept()
    def selection(self):
        return {'enabled':self.use.isChecked(),'destination':'new' if self.new.isChecked() else 'existing',
                'server_name':self.name.text().strip(),'guidelines_accepted':self.new.isChecked() and self.guidelines.isChecked(),
                'developer_terms_accepted':self.developer_terms.isChecked()}


class DiscordPanel(QWidget):
    def __init__(self,window,connect_on_start=True):
        super().__init__(window)
        self.window,self.store=window,window.store
        self.setup_worker=None
        self.bot=None
        layout=QVBoxLayout(self)
        title=QLabel('スマホのDiscordからnoteを管理')
        title.setObjectName('section')
        layout.addWidget(title)
        text=QLabel('初回はBotの作成から自動で設定します。表示されるブラウザーでDiscordにログインし、導入先を選んでください。\n'
                    '以後はPCを起動したまま、Discordの「note管理」から操作できます。PC停止・スリープ中は動作しません。')
        text.setWordWrap(True)
        layout.addWidget(text)
        self.enabled=QCheckBox('Discord連携')
        self.enabled.setChecked(self.store.setting('discord_enabled',bool(self.store.setting('discord_token',''))))
        self.enabled.clicked.connect(self.toggle_integration)
        layout.addWidget(self.enabled)
        row=QHBoxLayout()
        for caption,handler in [('Discord連携を設定・変更',self.setup),('接続・再接続',self.connect_bot),('Discord接続を停止',self.stop_bot)]:
            button=QPushButton(caption)
            button.clicked.connect(handler)
            row.addWidget(button)
        layout.addLayout(row)
        commands=QPushButton('コマンド名の設定')
        commands.clicked.connect(self.configure_commands)
        layout.addWidget(commands)
        self.status=QLabel('未設定')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(QLabel('管理サーバー（Bot接続後に選択）'))
        self.guilds=QComboBox()
        self.guilds.setPlaceholderText('Botを導入して接続すると表示されます')
        layout.addWidget(self.guilds)
        choose=QPushButton('このサーバーに管理メニューを作成')
        choose.clicked.connect(self.choose_guild)
        layout.addWidget(choose)
        layout.addStretch()
        self.poll=QTimer(self)
        self.poll.timeout.connect(self.refresh)
        self.poll.start(2000)
        self.guild_snapshot=None
        self.refresh()
        if connect_on_start and self.store.setting('discord_enabled',True) and self.store.setting('discord_token',''):
            QTimer.singleShot(0,self.connect_bot)
    def refresh(self):
        if not (self.setup_worker and self.setup_worker.isRunning()):
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
    def configure_commands(self):
        dialog=DiscordCommandDialog(self,self.store.setting('discord_commands',{}))
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        self.store.set_setting('discord_commands',dialog.selection())
        if self.store.setting('discord_enabled',False) and self.store.setting('discord_token',''):
            self.connect_bot()
        else:
            self.window.notify('コマンド名を保存しました。次回のDiscord接続時に反映します。')
    def setup(self):
        if self.setup_worker and self.setup_worker.isRunning(): return
        state=dict(self.store.setting('discord_onboarding',{}))
        state['has_application']=bool(self.store.setting('discord_setup',{}).get('application'))
        dialog=DiscordSetupDialog(self,state)
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            self.enabled.setChecked(self.store.setting('discord_enabled',bool(self.store.setting('discord_token',''))))
            return
        choice=dialog.selection()
        self.store.set_setting('discord_onboarding',choice)
        self.store.set_setting('discord_enabled',choice['enabled'])
        self.enabled.setChecked(choice['enabled'])
        if not choice['enabled']:
            self.stop_bot()
            self.store.set_setting('discord_connection','Discord連携は使わない設定です。後から設定できます。')
            self.refresh()
            return
        self.stop_bot()
        from .discord_setup import PortalSetup
        self.setup_worker=PortalSetup(self.store,self)
        self.setup_worker.progress.connect(self.status.setText)
        self.setup_worker.outcome.connect(self.setup_done)
        self.setup_worker.start()
    def toggle_integration(self,enabled):
        self.enabled.setChecked(enabled)
        if not enabled:
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
        self.window.notify(message,not ok)
        if ok and self.store.setting('discord_enabled',True): self.connect_bot()
    def connect_bot(self):
        if not self.store.setting('discord_enabled',True):
            self.window.notify('Discord連携は使わない設定です。セットアップから変更できます。',True)
            return
        if not self.store.setting('discord_token',''):
            self.window.notify('先に「Discord連携を設定・変更」を実行してください。',True)
            return
        if self.bot and self.bot.is_alive():
            self.bot.stop()
            self.store.set_setting('discord_connection','接続を終了しています。')
            QTimer.singleShot(1000,self.connect_bot)
            return
        from .discord_bot import BotThread
        self.store.set_setting('discord_connection','接続中…')
        self.bot=BotThread(self.store)
        self.bot.start()
    def stop_bot(self):
        if self.bot: self.bot.stop()
        self.store.set_setting('discord_connection','停止')
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
        self.stop_bot()
        if self.setup_worker and self.setup_worker.isRunning():
            self.setup_worker.cancel.set()
            return False
        return True
