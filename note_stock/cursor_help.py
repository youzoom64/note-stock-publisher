"""Show one non-interactive help label next to the cursor inside note Stock."""

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QAbstractButton, QApplication, QComboBox, QLabel, QListWidget, QTabBar, QWidget


BUTTON_HELP = {
    '設定': 'noteへのログイン、自動同期の間隔、定期投稿の曜日などを設定します。',
    'カーソルヘルプを表示する': 'べ、別に迷ってると思ったわけじゃないけど……チェックを入れたら、カーソルの横で操作を教えてあげる。',
    '自動投稿を開始': '日時指定と定期投稿の自動実行を開始します。',
    '自動投稿を一時停止': '新しい自動投稿の開始を止めます。処理中の投稿は結果確認まで続きます。',
    'すべて': 'すべての状態の記事を一覧に表示します。',
    '下書き': '下書きの記事だけを表示します。',
    '投稿待ち': '日時指定または定期投稿を待っている記事だけを表示します。',
    '投稿済み': '公開済みの記事だけを表示します。',
    '非公開': '非公開の記事だけを表示します。',
    '要確認': '投稿結果などの確認が必要な記事だけを表示します。',
    '手動順': '「上へ」「下へ」で設定した順に記事を表示します。',
    'noteの全記事を同期': 'noteから記事の一覧と投稿状態を読み直します。',
    '＋ 新規': '新しい下書きを作ります。',
    '複製': '選択した記事をもとに、別の下書きを作ります。',
    '削除': '選択した記事をnoteストックの一覧から削除します。',
    '保存済みタグから追加': '設定に保存したハッシュタグを選択した記事へ追加します。',
    '下書き入力': 'PCに仮保存している下書きのタイトルと本文を編集します。',
    'noteで編集': '専用ブラウザーでnoteの編集画面を開きます。',
    '中止': '現在のnote編集を中止します。',
    '投稿設定を保存': '投稿方法、日時、タグなどの設定を保存します。',
    '今すぐ投稿': '公開条件を確認して、選択した記事の投稿へ進みます。',
    '公開結果を確認': '選択した記事がnote上で公開されたか確認します。',
    '記事を開く': '選択した記事をブラウザーで開きます。',
    '履歴': '選択した記事の処理・投稿履歴を表示します。',
    '定期投稿の日に差し込む…': '選択した記事を指定日の定期投稿枠に入れ、元のキューを次の枠へ送ります。',
    '差し込みを保存': '選んだ日にこの1本を定期投稿する設定を保存します。',
    '差し込みを外す': '日付の指定を解除し、通常のキューに戻します。',
    'ログイン・接続確認': '選択した方法でnoteへログインし、アカウントを照合します。',
    'Discord連携': 'Discordからnoteストックを操作できるようにします。',
    'Discord連携を設定・変更': 'Discord連携の有無と、Botを入れるサーバーを設定します。',
    '自動で設定': 'ログインと本人認証の後、Botの作成・導入をnoteストックに任せます。',
    '手動で設定（HTML手順を開く）': 'Botを手動で作成・導入するためのHTML手順を表示します。',
    'HTML手順をもう一度開く': 'Discordの手動設定手順をもう一度表示します。',
    '保存して接続': 'Botがサーバーに導入されていることを確認し、接続情報を保存します。',
    '接続・再接続': 'DiscordのBotを接続し、管理メニューを準備します。',
    'Discord接続を停止': 'DiscordのBotとの接続を切ります。',
    'Discordアプリページを開く': '現在のBotの開発者ポータルをブラウザーで開きます。',
    'コマンド名の設定': 'Discordで使うスラッシュコマンドの名前を変更します。',
    'このサーバーに管理メニューを作成': '選択したサーバーに「note管理」の操作メニューを用意します。',
    '追加設定：予約用Web画面の接続': '予約用Web画面を使う場合に、HTTPS中継を設定します。',
    '自動で設定して接続を確認': '入力したHTTPS中継URLをDiscordに登録し、予約画面への接続を確認します。',
    '拡張機能：Discord内で再ログイン': 'Discordからnoteへの再ログインを行うための設定を開きます。',
    'Windowsログイン時にアプリを起動する': 'Windowsへログインしたときにnoteストックを自動で起動します。',
    '診断ログを保存する（次回起動で初期化）': '起動やエラーなどの記録を保存します。',
    '取り込み後に、指定した日時で投稿する': '選んだ下書きの取り込みと同時に、指定日時の投稿を予約します。',
    'OK': '現在の内容を確定します。',
    'はい': '確認画面に書かれた操作を実行します。',
    'いいえ': '確認画面に書かれた操作を取りやめます。',
    '初期名に戻す': 'コマンド名を既定値に戻します。',
    'すべて選択': '一覧の項目をすべて選びます。',
    '選択を解除': '一覧の選択をすべて外します。',
    '選んだ下書きを取り込む': '選んだ下書きの本文と公開設定をnoteストックへ取得します。',
    '保存': 'この画面で変更した内容を保存します。',
    '保存して反映': '変更を保存して適用します。',
    'この時刻にする': '時計で選んだ時刻を投稿日時の欄に反映します。',
    'キャンセル': '変更を保存せずに、現在の画面を閉じます。',
    '閉じる': 'この画面を閉じます。',
    '戻る': '前の画面に戻ります。',
    '次へ': '現在の選択で次の手順に進みます。',
    '今は使わない': 'Discord連携を設定せず、PCでnoteストックを使います。',
    '使う': 'Discord連携の設定に進みます。',
    '専用サーバーを新規作成する': 'noteストック専用のDiscordサーバーを作成します。',
    '既存のサーバーを選ぶ': 'すでにあるDiscordサーバーをBotの導入先にします。',
    '設定を開始': 'Discordを開き、専用Botの設定を開始します。',
    '完了': '現在の選択を保存して設定を閉じます。',
}


class CursorHelp(QObject):
    def __init__(self,window,enabled=True):
        super().__init__(window)
        self.window=window
        self.bubble=QLabel(window,Qt.WindowType.ToolTip | Qt.WindowType.WindowTransparentForInput |
                           Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.bubble.setTextFormat(Qt.TextFormat.PlainText)
        self.bubble.setWordWrap(True)
        self.bubble.setMaximumWidth(310)
        self.bubble.setStyleSheet('QLabel {background:#fff8e4; color:#30281c; border:1px solid #b9a679; padding:5px 8px;}')
        self.timer=QTimer(self)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self.update)
        QApplication.instance().installEventFilter(self)
        self.set_enabled(enabled)

    def set_enabled(self,enabled):
        if enabled:
            if not self.timer.isActive():
                self.timer.start()
            self.update()
        else:
            self.timer.stop()
            self.bubble.hide()

    def belongs_to_window(self,widget):
        while isinstance(widget,QWidget):
            if widget is self.window:
                return True
            widget=widget.parentWidget()
        return False

    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.ToolTip and self.belongs_to_window(watched):
            return True
        return False

    def help_for(self,widget):
        while self.belongs_to_window(widget):
            if isinstance(widget,QTabBar):
                index=widget.tabAt(widget.mapFromGlobal(QCursor.pos()))
                if index>=0:
                    title=widget.tabText(index).replace('&','')
                    return {
                        '投稿管理':'記事の一覧、本文、投稿設定を開きます。',
                        'Discord・セットアップ':'Discordの接続と設定を開きます。',
                    }.get(title,f'「{title}」を開きます。')
                return ''
            if isinstance(widget,QListWidget) and widget.objectName()=='posts':
                point=widget.viewport().mapFromGlobal(QCursor.pos())
                return ('記事の行をダブルクリックすると、noteから本文を読み込みます。'
                        if widget.viewport().rect().contains(point) and widget.itemAt(point) else '')
            custom=widget.property('cursorHelp') or widget.toolTip()
            if custom:
                return str(custom)
            if isinstance(widget,QAbstractButton):
                caption=widget.text().replace('&','').strip()
                if caption.startswith('定期投稿に差し込み：'):
                    return '定期投稿へ差し込む日を変更、または解除します。'
                if caption in {'接続中…','再接続中…','切断中…'}:
                    return 'Discordの接続処理の結果が出るまで待ちます。'
                if caption:
                    return BUTTON_HELP.get(caption) or widget.accessibleDescription()
            widget=widget.parentWidget()
        return ''

    def popup_help(self,popup,point):
        owner=popup.parentWidget()
        while owner is not None and not isinstance(owner,QComboBox):
            owner=owner.parentWidget()
        if owner is None:
            return ''
        view=owner.view()
        viewport=view.viewport()
        local=viewport.mapFromGlobal(point)
        if not viewport.rect().contains(local):
            return ''
        index=view.indexAt(local)
        if index.isValid():
            return index.data(Qt.ItemDataRole.ToolTipRole) or self.help_for(owner)
        return ''

    def update(self):
        active=QApplication.activeWindow()
        popup=QApplication.activePopupWidget()
        if not self.window.isVisible() or not self.belongs_to_window(popup or active):
            self.bubble.hide()
            return
        point=QCursor.pos()
        if popup is not None:
            text=self.popup_help(popup,point)
        else:
            widget=QApplication.widgetAt(point)
            if widget is None:
                local=active.mapFromGlobal(point)
                if active.rect().contains(local):
                    widget=active.childAt(local) or active
            text=self.help_for(widget)
        if not text:
            self.bubble.hide()
            return
        if self.bubble.text()!=text:
            self.bubble.setText(text)
            self.bubble.adjustSize()
        area=(QGuiApplication.screenAt(point) or self.window.screen()).availableGeometry()
        x=point.x()+18
        if x+self.bubble.width()>area.right():
            x=max(area.left(),point.x()-self.bubble.width()-18)
        y=point.y()+8
        if y+self.bubble.height()>area.bottom():
            y=point.y()-self.bubble.height()-8
        self.bubble.move(QPoint(x,max(area.top(),y)))
        self.bubble.show()
        if popup is not None:
            self.bubble.raise_()
