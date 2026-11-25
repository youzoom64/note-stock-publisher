"""Reservation Activity setup shared by onboarding and the Discord settings tab."""
from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QLabel,QLineEdit,QPushButton,QSpinBox
from .relogin import public_url,manager
from .schedule_activity import ensure_host,stop_host


class ActivitySettings(QDialog):
    def __init__(self,store,parent=None):
        super().__init__(parent)
        self.store=store
        self.worker=None
        self.setWindowTitle('Discord内の予約画面')
        self.resize(560,300)
        layout=QVBoxLayout(self)
        text=QLabel('利用者自身のHTTPS中継を用意し、転送先を下のPC内部ポートに設定してください。'
                    'URLを入力すると、Discord側の登録と接続確認を自動で行います。')
        text.setWordWrap(True)
        layout.addWidget(text)
        config=store.setting('remote_relogin',{})
        form=QFormLayout()
        self.url=QLineEdit(config.get('https_url',''))
        self.url.setPlaceholderText('https://自分の中継ドメイン')
        form.addRow('HTTPS中継URL',self.url)
        self.port=QSpinBox()
        self.port.setRange(1024,65535)
        self.port.setValue(config.get('port',18764))
        form.addRow('PC内部の転送先（127.0.0.1）',self.port)
        layout.addLayout(form)
        self.status=QLabel('設定済み' if config.get('schedule_verified') else '予約画面は未設定です。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.configure=QPushButton('自動で設定して接続を確認')
        self.configure.clicked.connect(self.save)
        layout.addWidget(self.configure)
        close=QPushButton('閉じる')
        close.clicked.connect(self.reject)
        layout.addWidget(close)

    def save(self):
        previous=self.store.setting('remote_relogin',{})
        try:
            url=public_url(self.url.text())
            port=self.port.value()
            if not self.store.setting('discord_enabled',False):
                raise ValueError('先にDiscord連携を有効にしてください。')
            if not self.store.setting('discord',{}).get('application'):
                raise ValueError('先にBotのセットアップを完了してください。')
            if manager(self.store).plugin and (previous.get('https_url')!=url or previous.get('port',18764)!=port):
                raise ValueError('再ログイン拡張が使用中のため、先に拡張設定で保存先を変更してください。')
            stop_host(self.store)
            self.store.set_setting('remote_relogin',{**previous,'https_url':url,'port':port,'schedule_verified':False})
            ensure_host(self.store,configuring=True)
        except Exception as exc:
            self.status.setText(str(exc))
            return
        from .discord_setup import ActivityOAuthSetup
        self.worker=ActivityOAuthSetup(self.store,self)
        self.worker.relay_url=url
        self.worker.progress.connect(self.status.setText)
        self.worker.outcome.connect(self.completed)
        self.configure.setEnabled(False)
        self.status.setText('表示されたDiscord画面で初期設定を進めています…')
        self.worker.start()

    def completed(self,ok,message):
        config=self.store.setting('remote_relogin',{})
        if ok:
            self.store.set_setting('remote_relogin',{**config,'activity_confirmed':True,'schedule_verified':True})
        elif not config.get('activity_confirmed'):
            stop_host(self.store)
        self.status.setText(message)
        self.configure.setEnabled(True)

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.status.setText('現在の接続確認が終わるまでお待ちください。')
            return
        super().reject()
