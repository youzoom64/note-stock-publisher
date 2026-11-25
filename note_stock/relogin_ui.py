"""Optional extension installation and per-user relay setup."""
import subprocess
import sys
from pathlib import Path
from PySide6.QtCore import QThread,Signal,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QLabel,QLineEdit,QCheckBox,QPushButton,QFileDialog,QSpinBox
from .relogin import manager,public_url


class ProbeWorker(QThread):
    result=Signal(bool,str)
    def __init__(self,extension,parent): super().__init__(parent); self.extension=extension
    def run(self):
        try: self.extension.probe(); self.result.emit(True,'公開HTTPSから、このPCへの接続を確認しました。')
        except Exception as exc: self.result.emit(False,'接続試験に失敗しました（'+type(exc).__name__+'）。中継先・ファイアウォール・転送先ポートを確認してください。')


class ReloginDialog(QDialog):
    def __init__(self,store,parent=None):
        super().__init__(parent); self.store=store; self.manager=manager(store); self.probe_worker=None
        self.setWindowTitle('拡張機能 — Discord内で再ログイン'); self.resize(640,690)
        layout=QVBoxLayout(self)
        text=QLabel('iPhoneのDiscord内に、このアプリ専用ブラウザーの画面を表示します。\n'
                    '初回だけ、このPCで利用者本人のHTTPS中継とDiscord Activityを設定してください。\n'
                    '中継サービスのアカウント・ドメインは利用者本人が用意します。共通サーバーは使用しません。')
        text.setWordWrap(True); layout.addWidget(text)
        install=QPushButton('1. 拡張パッケージを追加'); install.clicked.connect(self.install); layout.addWidget(install)
        deps=QPushButton('2. 必要な部品を準備'); deps.clicked.connect(self.dependencies); layout.addWidget(deps)
        config=store.setting('remote_relogin',{})
        form=QFormLayout(); layout.addLayout(form)
        self.url=QLineEdit(config.get('https_url','')); self.url.setPlaceholderText('https://自分の中継用ドメイン')
        self.url.setAccessibleName('遠隔再ログインの公開HTTPS URL'); form.addRow('公開HTTPS URL',self.url)
        self.port=QSpinBox(); self.port.setRange(1024,65535); self.port.setValue(config.get('port',18764))
        self.port.setAccessibleName('遠隔再ログインのローカル待受ポート'); form.addRow('PC内部の転送先ポート',self.port)
        relay=QLabel('中継の転送先は http://127.0.0.1:上記ポート です。ルーターのポート開放は不要です。\n'
                    'Cloudflare Tunnelの場合は、外向きTCP/UDP 7844番が必要です。遮断される環境では管理者へ許可を依頼してください。')
        relay.setWordWrap(True); layout.addWidget(relay)
        for title,url in [('中継の設定手順を開く','https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/'),
                          ('このBotのActivity設定を開く',self.portal_url(store))]:
            button=QPushButton(title); button.clicked.connect(lambda checked=False,u=url:QDesktopServices.openUrl(QUrl(u))); layout.addWidget(button)
        instructions=QLabel('Discord Developer PortalでActivitiesを有効化し、URL Mappingsの「/」を上の公開ドメインへ割り当てます。\n'
                            'モバイル対応を有効にし、利用者本人のDiscordで起動できることを確認してください。')
        instructions.setWordWrap(True); layout.addWidget(instructions)
        self.activity=QCheckBox('自分のBotのActivity・URL割当・モバイル対応を設定した')
        self.activity.setChecked(config.get('activity_confirmed',False)); layout.addWidget(self.activity)
        self.consent=QCheckBox('信頼した拡張コードを実行し、本人による専用ブラウザーの遠隔操作を許可する')
        self.consent.setChecked(config.get('consent',False)); layout.addWidget(self.consent)
        self.enabled=QCheckBox('遠隔再ログイン拡張を有効にする'); self.enabled.setChecked(config.get('enabled',False)); layout.addWidget(self.enabled)
        self.status=QLabel(''); self.status.setWordWrap(True); layout.addWidget(self.status)
        save=QPushButton('保存して適用'); save.clicked.connect(self.save); layout.addWidget(save)
        self.test=QPushButton('3. 公開HTTPS接続を試験'); self.test.clicked.connect(self.probe); layout.addWidget(self.test)
        close=QPushButton('閉じる'); close.clicked.connect(self.reject); layout.addWidget(close)
        self.refresh_status()

    @staticmethod
    def portal_url(store):
        app=str(store.setting('discord',{}).get('application',''))
        return 'https://discord.com/developers/applications/'+(app+'/embedded' if app.isdigit() else '')

    def refresh_status(self):
        installed=(self.manager.folder/'manifest.json').exists()
        self.status.setText('拡張: '+('追加済み' if installed else '未追加')+' / '+('待受中' if self.manager.plugin else '停止中'))

    def install(self):
        if self.manager.plugin: self.status.setText('拡張機能の有効チェックを外して保存してから追加してください。'); return
        path,_=QFileDialog.getOpenFileName(self,'信頼した拡張パッケージを選択','','note拡張 (*.noteext.zip)')
        if not path: return
        try: self.manager.install(path); self.refresh_status()
        except Exception as exc: self.status.setText('追加できませんでした: '+str(exc))

    def dependencies(self):
        requirements=self.manager.folder/'plugin'/'requirements.txt'
        if not requirements.exists(): self.status.setText('先に拡張パッケージを追加してください。'); return
        if requirements.read_text(encoding='utf-8').strip()!='cryptography==49.0.0':
            self.status.setText('必要部品の指定が正規パッケージと一致しません。追加し直してください。'); return
        executable=Path(sys.executable)
        if executable.name.lower()=='pythonw.exe': executable=executable.with_name('python.exe')
        subprocess.Popen([str(executable),'-m','pip','install','--disable-pip-version-check','--only-binary=:all:',
                          '-r',str(requirements)],creationflags=getattr(subprocess,'CREATE_NEW_CONSOLE',0))
        self.status.setText('表示された準備画面が正常に終了してから「保存して適用」を押してください。')

    def save(self):
        try:
            config={'enabled':self.enabled.isChecked(),'https_url':public_url(self.url.text()) if self.enabled.isChecked() else self.url.text().strip(),
                    'port':self.port.value(),'consent':self.consent.isChecked(),'activity_confirmed':self.activity.isChecked()}
            if config['enabled'] and (not config['consent'] or not config['activity_confirmed']):
                raise ValueError('操作範囲への同意とActivityの初回設定が必要です。')
            old=self.store.setting('remote_relogin',{})
            same_route=all(old.get(key)==config[key] for key in ('https_url','port','activity_confirmed'))
            config['schedule_verified']=bool(old.get('schedule_verified') and same_route)
            from .schedule_activity import ensure_host,stop_host
            stop_host(self.store)
            self.manager.stop(); self.store.set_setting('remote_relogin',config)
            try: self.manager.start()
            except Exception:
                self.store.set_setting('remote_relogin',{**config,'enabled':False})
                raise
            ensure_host(self.store)
            self.refresh_status()
            if config['enabled']: self.status.setText('PC内部で待受を開始しました。「公開HTTPS接続を試験」を行ってください。')
        except Exception as exc: self.status.setText('適用できませんでした: '+str(exc))

    def probe(self):
        if self.manager.plugin is None: self.status.setText('拡張機能を有効にして保存してください。'); return
        if self.probe_worker and self.probe_worker.isRunning(): return
        self.test.setEnabled(False); self.status.setText('外部HTTPSからこのPCへの経路を試験しています…')
        self.probe_worker=ProbeWorker(self.manager.plugin,self)
        self.probe_worker.result.connect(lambda ok,msg:(self.status.setText(msg),self.test.setEnabled(True)))
        self.probe_worker.start()

    def reject(self):
        if self.probe_worker and self.probe_worker.isRunning():
            self.status.setText('接続試験が完了してから閉じてください。'); return
        super().reject()
