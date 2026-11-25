from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QDateTime, QTime, QTimeZone, QTimer, QSize, QUrl, QProcess
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap, QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QPlainTextEdit, QComboBox, QListWidget, QListWidgetItem, QSplitter,
    QFormLayout, QDateTimeEdit, QTimeEdit, QDialog, QDialogButtonBox, QCheckBox,
    QMessageBox, QFrame, QScrollArea, QMenu, QTabWidget,
    QButtonGroup, QRadioButton, QStyle, QStyledItemDelegate,
)

from .platform import autostart_enabled, set_autostart
from .publisher import account_id
from .store import JST, LOCKED, STATUSES, Scheduler, article_timestamp, now_ts, tags_from_text
from .worker import BrowserWorker
from .preview import RichPreview
from .vendor.qt_dropdown import StableDropdown
from .vendor.qt_anchor_splitter import bind_splitter_anchor
from .vendor.qt_scroll_guard import capture_scroll, restore_scroll

ARTICLE_FILTERS = {
    '': ('すべて', frozenset()),
    'draft': ('下書き', frozenset({'draft', 'editing'})),
    'queued': ('投稿待ち', frozenset({'queued', 'scheduled', 'native_reserved', 'posting'})),
    'published': ('投稿済み', frozenset({'published'})),
    'native_private': ('非公開', frozenset({'native_private'})),
    'review': ('要確認', frozenset({'failed', 'held', 'unknown', 'overdue', 'review'})),
}
ARTICLE_ROW_COLORS = {
    status: color
    for group, color in {
        'draft': '#EAF4FF',
        'queued': '#FFF4D9',
        'published': '#E7F5EC',
        'native_private': '#F1EBFA',
        'review': '#FCEBE7',
    }.items()
    for status in ARTICLE_FILTERS[group][1]
}

STYLE = """
QWidget {font-family:'Yu Gothic UI','Meiryo'; font-size:13px; color:#25342f;}
QMainWindow, QDialog {background:#f4f7f5;}
QMenu {background:#ffffff; color:#25342f; border:1px solid #cddbd2; padding:4px; border-radius:5px;}
QMenu::item {padding:6px 14px;}
QMenu::item:selected {background:#d8eadd; color:#153f2e;}
QMenu::item:disabled {color:#52685b;}
QToolTip {background:#ffffff; color:#25342f; border:1px solid #9aaba2; padding:4px;}
QLabel#section {font-size:17px; font-weight:700;}
QLabel#muted {color:#687a72;}
QLabel#notice {background:#fff3d9; color:#6e4d12; border:1px solid #e9d29d; border-radius:7px; padding:10px;}
QFrame#card {background:white; border:1px solid #dce5df; border-radius:10px;}
QPushButton {background:white; border:1px solid #cad8cf; padding:7px 13px; border-radius:6px;}
QPushButton:hover {background:#eaf4ee; border-color:#77aa92;}
QPushButton:pressed {background:#d8eadd;}
QPushButton:disabled {color:#99a49e; background:#f1f3f1; border-color:#e2e8e3;}
QPushButton#primary {background:#16845e; color:white; border-color:#16845e; font-weight:600;}
QPushButton#primary:hover {background:#106b4b;}
QPushButton#primary:disabled {background:#b3cabc; border-color:#b3cabc;}
QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QDateTimeEdit, QTimeEdit {background:white; border:1px solid #cddbd2; border-radius:5px; padding:7px; selection-background-color:#16845e;}
QLineEdit:focus, QPlainTextEdit:focus {border:1px solid #16845e;}
QLineEdit#title {font-size:21px; font-weight:600; padding:10px;}
QListWidget {background:white; border:0; outline:0;}
QTableWidget {background:white; alternate-background-color:#f4f7f5; color:#25342f; border:1px solid #cddbd2; gridline-color:#dce5df; selection-background-color:#16845e; selection-color:white;}
QTableWidget::item {padding:5px;}
QTableWidget::item:selected {background:#16845e; color:white;}
QHeaderView::section {background:#e5ede7; color:#25342f; border:0; border-right:1px solid #cddbd2; border-bottom:1px solid #cddbd2; padding:6px; font-weight:600;}
QTableWidget QLineEdit {background:white; color:#25342f; selection-background-color:#16845e; selection-color:white;}
QListWidget::item {padding:12px 10px; border-bottom:1px solid #edf1ed; border-left:3px solid transparent;}
QListWidget::item:selected {color:#153f2e; border-left:3px solid #16845e;}
QListWidget#draft_imports::item:selected {background:#e9f5ee;}
QListWidget#draft_imports::item:hover:!selected {background:#f5f9f6;}
QSplitter::handle {background:#dce5df; width:5px;}
QPushButton[primary="true"] {background:#16845e; color:white; border-color:#16845e; font-weight:600;}
QPushButton[primary="true"]:hover {background:#106b4b;}
QPushButton[primary="true"]:disabled {background:#b3cabc; border-color:#b3cabc;}
QStatusBar {background:#eaf0ec; color:#52685b;}
QTabWidget::pane {border:0; background:transparent;}
QTabBar::tab {background:#e5ede7; color:#52685b; padding:9px 24px; border:0;}
QTabBar::tab:selected {background:white; color:#143e30; border-bottom:2px solid #16845e; font-weight:600;}
QTabBar::tab:hover:!selected {background:#d7e7dc;}
QCheckBox {spacing:5px;}
QRadioButton[articleFilter="true"] {spacing:5px; padding:2px 0;}
QRadioButton[articleFilter="true"]:checked {color:#106b4b; font-weight:600;}
QListWidget#posts QScrollBar:vertical,
QScrollArea#posting_settings_scroll QScrollBar:vertical {
    background:#eaf0ec; width:13px; margin:2px;
    border-radius:4px;
}
QListWidget#posts QScrollBar::handle:vertical,
QScrollArea#posting_settings_scroll QScrollBar::handle:vertical {
    background:#78958a; min-height:24px; border-radius:4px;
}
QListWidget#posts QScrollBar::handle:vertical:hover,
QScrollArea#posting_settings_scroll QScrollBar::handle:vertical:hover {background:#4e7867;}
QListWidget#posts QScrollBar::add-line:vertical,
QListWidget#posts QScrollBar::sub-line:vertical,
QScrollArea#posting_settings_scroll QScrollBar::add-line:vertical,
QScrollArea#posting_settings_scroll QScrollBar::sub-line:vertical {height:0;}
QListWidget#posts QScrollBar::add-page:vertical,
QListWidget#posts QScrollBar::sub-page:vertical,
QScrollArea#posting_settings_scroll QScrollBar::add-page:vertical,
QScrollArea#posting_settings_scroll QScrollBar::sub-page:vertical {background:transparent;}
"""


class ArticleRowDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        row = option.rect
        painter.setClipRect(row)
        painter.fillRect(row, index.data(Qt.ItemDataRole.BackgroundRole))
        painter.fillRect(row.left(), row.bottom(), row.width(), 1, QColor(37, 52, 47, 26))
        selected = option.state & QStyle.StateFlag.State_Selected
        if selected:
            painter.fillRect(row.left(), row.top(), 3, row.height(), QColor('#16845e'))

        painter.setFont(option.font)
        painter.setPen(QColor('#153f2e' if selected else '#25342f'))
        metrics = painter.fontMetrics()
        text = index.data(Qt.ItemDataRole.DisplayRole)
        lines = text.split('\n')
        baseline = row.top() + (row.height() - len(lines) * metrics.height()) // 2 + metrics.ascent()
        for line in lines:
            elided = metrics.elidedText(line, Qt.TextElideMode.ElideRight, max(0, row.width() - 23))
            painter.drawText(row.left() + 13, baseline, elided)
            baseline += metrics.height()
        painter.restore()


def icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#16845e"))
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setBrush(QColor("white"))
    for y, w in ((17, 34), (28, 34), (39, 23)):
        painter.drawRoundedRect(15, y, w, 5, 2, 2)
    painter.end()
    return QIcon(pix)


def button(text, handler, primary=False, name=""):
    item = QPushButton(text)
    item.setObjectName(name or ("primary" if primary else ""))
    item.setProperty("primary", primary)
    item.clicked.connect(handler)
    return item


def label(text, name=""):
    item = QLabel(text)
    item.setObjectName(name)
    return item


def jst_qdate(stamp):
    return QDateTime.fromSecsSinceEpoch(int(stamp // 60) * 60, QTimeZone(9 * 3600))


class SettingsDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.request_login = False
        self.setWindowTitle("設定 — noteストック")
        self.setMinimumWidth(550)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.addWidget(label("投稿先アカウント", "section"))
        form = QFormLayout()
        self.account = QLineEdit(store.setting("account", ""))
        self.account.setObjectName("account")
        self.account.setPlaceholderText("note ID または https://note.com/あなたのID")
        form.addRow("note ID", self.account)
        self.browser = StableDropdown()
        for text,value in (("自動（Chrome → Edge → Chromium）","auto"),("Google Chrome","chrome"),("Microsoft Edge","edge"),("Chromium（別途導入）","chromium")):
            self.browser.addItem(text,value)
        self.browser.setCurrentIndex(max(0,self.browser.findData(store.setting('browser','auto'))))
        form.addRow("ブラウザー", self.browser)
        self.login_method=StableDropdown()
        self.login_method.addItem('メールアドレス・パスワード','password')
        self.login_method.addItem('Google（通常ブラウザーで本人がログイン）','google')
        self.login_method.setCurrentIndex(max(0,self.login_method.findData(store.setting('note_login_method','password'))))
        form.addRow('noteのログイン方法',self.login_method)
        layout.addLayout(form)
        info = label("利用者本人のアカウントでログインします。パスワードはこのアプリに入力しません。", "muted")
        info.setWordWrap(True)
        layout.addWidget(info)
        google_hint = label('「Googleでログイン」を使う方は、先に上のログイン方法を「Google」に変更してください。', 'muted')
        google_hint.setWordWrap(True)
        layout.addWidget(google_hint)
        self.connect_button = button("ログイン・接続確認", self.connect_account)
        self.connect_button.setEnabled(not parent or not parent.busy())
        layout.addWidget(self.connect_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(label("定期投稿", "section"))
        recurrence = store.setting("recurrence", {"days": list(range(7)), "time": "09:00"})
        days = QHBoxLayout()
        self.days = []
        for i, day in enumerate("月火水木金土日"):
            check = QCheckBox(day)
            check.setChecked(i in recurrence["days"])
            self.days.append(check)
            days.addWidget(check)
        layout.addLayout(days)
        self.time = QTimeEdit(QTime.fromString(recurrence["time"], "HH:mm"))
        self.time.setDisplayFormat("HH:mm")
        form = QFormLayout()
        form.addRow("投稿時刻（日本時間）", self.time)
        layout.addLayout(form)
        layout.addWidget(label("1回につき、定期投稿待ちの先頭から1本を公開します。", "muted"))
        layout.addWidget(label("よく使うハッシュタグ", "section"))
        self.favorites = QPlainTextEdit(" ".join("#" + tag for tag in store.setting("favorite_tags", [])))
        self.favorites.setMaximumHeight(85)
        self.favorites.setPlaceholderText("#日記 #エッセイ #暮らし　空白・改行・カンマで区切る")
        layout.addWidget(self.favorites)
        self.autostart = QCheckBox("Windowsログイン時にアプリを起動する")
        self.autostart.setChecked(autostart_enabled())
        from .diagnostics import DEFAULT_ENABLED
        self.diagnostics = QCheckBox("診断ログを保存する（次回起動で初期化）")
        self.diagnostics.setChecked(store.setting("diagnostics", DEFAULT_ENABLED))
        layout.addWidget(self.autostart)
        layout.addWidget(self.diagnostics)
        layout.addWidget(button('拡張機能：Discord内で再ログイン',self.remote_extension))
        info = label("原稿・画像・ログイン状態は、このアプリのdataフォルダー内に保存します。\n終了・スリープ中は投稿しません。期限を過ぎた予約は再予約が必要です。", "muted")
        info.setWordWrap(True)
        layout.addWidget(info)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def connect_account(self):
        self.request_login = True
        self.save()

    def remote_extension(self):
        from .relogin_ui import ReloginDialog
        ReloginDialog(self.store,self).exec()

    def save(self):
        try:
            account = account_id(self.account.text()) if self.account.text().strip() else ""
            if self.request_login and not account:
                raise ValueError("接続するnote IDを入力してください。")
            days = [i for i, check in enumerate(self.days) if check.isChecked()]
            if not days:
                raise ValueError("定期投稿の曜日を1つ以上選んでください。")
            values = {"account": account, "favorite_tags": tags_from_text(self.favorites.toPlainText()),
                      "recurrence": {"days": days, "time": self.time.time().toString("HH:mm")},
                      "diagnostics": self.diagnostics.isChecked(), "browser": self.browser.currentData(),
                      'note_login_method':self.login_method.currentData()}
            if account != self.store.setting("account", "") or values['browser'] != self.store.setting('browser','auto'):
                if self.parent() and self.parent().busy():
                    raise ValueError("投稿処理中はアカウントを変更できません。")
                values.update({"account_verified": False, "paused": True})
            results = self.store.settings_batch(values)
            if self.autostart.isChecked() != autostart_enabled():
                try:
                    set_autostart(self.autostart.isChecked())
                    results["自動起動"] = ""
                except Exception as exc:
                    results["自動起動"] = str(exc)
            errors = [f"{key}: {error}" for key, error in results.items() if error]
            if errors:
                raise ValueError("一部の設定を保存できませんでした。成功した項目は保存済みです。\n" + "\n".join(errors))
            self.accept()
        except Exception as exc:
            self.request_login = False
            QMessageBox.warning(self, "設定を確認してください", str(exc))


class DraftImportDialog(QDialog):
    def __init__(self,items,parent=None):
        super().__init__(parent)
        self.setWindowTitle('noteの下書きを取り込む')
        self.resize(680,550)
        layout=QVBoxLayout(self)
        layout.addWidget(label('note側に保存されている下書き','section'))
        info=label('本文の装飾・画像・音声・埋め込みと公開設定を、そのまま取り込みます。取り込み中は公開しません。','muted')
        info.setWordWrap(True)
        layout.addWidget(info)
        self.list=QListWidget()
        self.list.setObjectName('draft_imports')
        for row in items:
            item=QListWidgetItem((row['title'] or '無題の下書き')+'\n'+str(row.get('updated',''))+' · '+row.get('owner',''))
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole,row['key'])
            self.list.addItem(item)
        layout.addWidget(self.list,1)
        row=QHBoxLayout()
        row.addWidget(button('すべて選択',lambda:self.check_all(True)))
        row.addWidget(button('選択を解除',lambda:self.check_all(False)))
        row.addStretch()
        layout.addLayout(row)
        self.schedule=QCheckBox('取り込み後に、指定した日時で投稿する')
        layout.addWidget(self.schedule)
        self.when=QDateTimeEdit(jst_qdate(now_ts()+3600))
        self.when.setDisplayFormat("yyyy/MM/dd HH:mm '（日本時間）'")
        self.when.setCalendarPopup(True)
        self.when.setEnabled(False)
        self.schedule.toggled.connect(self.when.setEnabled)
        layout.addWidget(self.when)
        layout.addWidget(label('複数の記事を同じ日時に指定した場合は、取り込んだ順に1件ずつ投稿します。','muted'))
        box=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText('選んだ下書きを取り込む')
        box.button(QDialogButtonBox.StandardButton.Cancel).setText('キャンセル')
        box.accepted.connect(self.accept_selected)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def check_all(self,checked):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def selected_keys(self):
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count()) if self.list.item(i).checkState()==Qt.CheckState.Checked]

    def accept_selected(self):
        if not self.selected_keys():
            QMessageBox.warning(self,'下書きを選んでください','取り込む下書きを1件以上選んでください。')
            return
        if self.schedule.isChecked() and self.when.dateTime().toSecsSinceEpoch()<=now_ts():
            QMessageBox.warning(self,'日時を確認してください','現在より先の日時を指定してください。')
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, store, offline=False):
        super().__init__()
        self.store = store
        self.current_id = None
        self.current = None
        self.image = ""
        self.dirty = False
        self.loading = False
        self.worker = None
        self.quitting = False
        self.pending_drafts=None
        self.pending_edit_id=None
        self.revision = 0
        self.setWindowTitle("noteストック — 投稿管理")
        self.setWindowIcon(icon())
        screen=QApplication.primaryScreen().availableGeometry()
        saved_size=store.setting("window_size", [1260, 900])
        self.resize(min(saved_size[0],screen.width()),min(saved_size[1],screen.height()-40))
        self.setMinimumSize(940, 640)
        self.build_ui()
        recovered = store.recover()
        self.scheduler = Scheduler(store)
        self.refresh()
        state=store.setting('ui_state',{})
        saved_order = state.get('article_order')
        if isinstance(saved_order, str) and saved_order in self.order_buttons:
            self.order_buttons[saved_order].setChecked(True)
        self.search.setText(state.get('search',''))
        saved_filter = state.get('filter')
        for key, (_, statuses) in ARTICLE_FILTERS.items():
            if isinstance(saved_filter, str) and saved_filter in statuses:
                self.filter_buttons[key].setChecked(True)
                break
        selected=state.get('selected')
        if selected and any(p['id']==selected for p in store.posts()):
            self.load(selected)
            self.refresh_list()
        QTimer.singleShot(0,lambda:self.list.verticalScrollBar().setValue(state.get('scroll',0)))
        from .commands import Commands
        from .ipc import CommandServer
        self.command_service=Commands(store,self)
        self.command_server=CommandServer(store,self.command_service.handle,self)
        if store.setting_errors:
            self.notify('\n'.join(store.setting_errors),True)
        if recovered:
            self.notify("前回処理中の原稿や期限を過ぎた予約があります。一覧の状態を確認してください。", True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        from .remote import RemoteController
        from .discord_ui import DiscordPanel
        self.remote_controller=RemoteController(self)
        if offline:
            self.remote_controller.timer.stop()
        self.discord_panel=DiscordPanel(self,connect_on_start=not offline)
        self.tabs.addTab(self.discord_panel,'Discord・セットアップ')
        if state.get('page')=='discord' or (not state.get('page') and state.get('tab')==2):
            self.tabs.setCurrentWidget(self.discord_panel)
        if not offline:
            try:
                from .relogin import manager
                manager(self.store).start()
                from .schedule_activity import ensure_host
                ensure_host(self.store)
            except Exception as exc:
                self.notify('遠隔再ログイン拡張を開始できません: '+str(exc),True)
        save_action = QAction("保存", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_current)
        self.addAction(save_action)

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 8, 22, 12)
        layout.setSpacing(6)
        self.notice = label("", "notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.tabs=QTabWidget()
        self.tabs.setObjectName('main_tabs')
        layout.addWidget(self.tabs,1)
        controls = QWidget()
        header = QHBoxLayout(controls)
        header.setContentsMargins(8, 0, 0, 0)
        header.setSpacing(8)
        self.counts = QWidget()
        self.counts.setStyleSheet('background:#f4f7f5;')
        legend = QHBoxLayout(self.counts)
        legend.setContentsMargins(0, 0, 0, 0)
        legend.setSpacing(10)
        self.count_labels = {}
        for key, name in (('draft', 'ストック'), ('queued', '投稿待ち'), ('published', '投稿済み'), ('review', '要確認')):
            entry = QHBoxLayout()
            entry.setSpacing(4)
            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            color = ARTICLE_ROW_COLORS[next(iter(ARTICLE_FILTERS[key][1]))]
            swatch.setStyleSheet(f'background:{color}; border:1px solid #9aaba2; border-radius:2px;')
            swatch.setToolTip(name + 'の記事の背景色')
            entry.addWidget(swatch)
            self.count_labels[key] = label(name + ' 0', 'muted')
            entry.addWidget(self.count_labels[key])
            legend.addLayout(entry)
        header.addWidget(self.counts)
        header.addSpacing(8)
        self.account_label = label("未接続", "muted")
        header.addWidget(self.account_label)
        header.addWidget(button("設定", self.settings))
        self.pause_button = button("自動投稿を開始", self.toggle_pause, True)
        self.pause_button.setObjectName("pause")
        header.addWidget(self.pause_button)
        self.tabs.setCornerWidget(controls, Qt.Corner.TopRightCorner)
        management=QWidget()
        self.management_page=management
        self.tabs.addTab(management,'投稿管理')
        management_layout=QVBoxLayout(management)
        management_layout.setContentsMargins(0,6,0,0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setObjectName("main_splitter")
        management_layout.addWidget(self.splitter,1)
        left = QFrame()
        left.setObjectName("card")
        pane = QVBoxLayout(left)
        pane.setContentsMargins(12, 14, 12, 10)
        pane.addWidget(label("ストックと投稿予定", "section"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("タイトル・本文・タグを検索")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_list)
        pane.addWidget(self.search)
        self.filter_group = QButtonGroup(left)
        self.filter_buttons = {}
        filters = QHBoxLayout()
        filters.setSpacing(12)
        for status, (name, _) in ARTICLE_FILTERS.items():
            radio = QRadioButton(name)
            radio.setProperty('articleFilter', True)
            radio.setProperty('status', status)
            radio.setAccessibleName(f"原稿の絞り込み: {name}")
            self.filter_buttons[status] = radio
            self.filter_group.addButton(radio)
            filters.addWidget(radio)
        filters.addStretch()
        self.filter_buttons[''].setChecked(True)
        self.filter_group.idToggled.connect(lambda _, checked: checked and self.refresh_list())
        pane.addLayout(filters)
        self.import_button=button('noteの全記事を同期',self.import_drafts,True)
        pane.addWidget(self.import_button)
        tools = QHBoxLayout()
        self.new_button = button("＋ 新規", self.new_post, False, "new")
        self.duplicate_button = button("複製", self.duplicate)
        self.delete_button = button("削除", self.delete)
        for b in (self.new_button, self.duplicate_button, self.delete_button):
            tools.addWidget(b)
        pane.addLayout(tools)
        self.list = QListWidget()
        self.list.setObjectName("posts")
        self.list.setItemDelegate(ArticleRowDelegate(self.list))
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.list.currentItemChanged.connect(self.selection_changed)
        pane.addWidget(self.list, 1)
        order = QHBoxLayout()
        self.move_up = button("↑ 上へ", lambda: self.reorder(-1))
        self.move_down = button("↓ 下へ", lambda: self.reorder(1))
        for control in (self.move_up, self.move_down):
            control.setToolTip("手動順で記事の位置を変更します")
            control.setEnabled(False)
            order.addWidget(control)
        order.addStretch()
        self.order_group = QButtonGroup(left)
        self.order_buttons = {}
        for key, name in (('manual', '手動順'), ('newest', '新しい順'), ('oldest', '古い順')):
            radio = QRadioButton(name)
            radio.setProperty('articleFilter', True)
            radio.setProperty('order', key)
            radio.setAccessibleName(f"記事の並べ替え: {name}")
            if key != 'manual':
                radio.setToolTip("noteの更新日時で並べ替えます。未同期の記事は登録日時を使います。")
            self.order_group.addButton(radio)
            self.order_buttons[key] = radio
            order.addWidget(radio)
        self.order_buttons['newest'].setChecked(True)
        self.order_group.buttonToggled.connect(self.order_changed)
        pane.addLayout(order)
        self.splitter.addWidget(left)
        posting=QFrame()
        posting.setObjectName('card')
        posting.setMinimumWidth(330)
        posting_layout = QVBoxLayout(posting)
        posting_layout.setContentsMargins(0, 0, 0, 0)
        posting_layout.setSpacing(0)
        self.posting_scroll = QScrollArea()
        self.posting_scroll.setObjectName('posting_settings_scroll')
        self.posting_scroll.setAccessibleName('投稿設定')
        self.posting_scroll.setWidgetResizable(True)
        self.posting_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.posting_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.posting_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        posting_contents = QWidget()
        posting_contents.setObjectName('posting_contents')
        posting_contents.setStyleSheet('QWidget#posting_contents {background: white;}')
        self.posting_scroll.setWidget(posting_contents)
        posting_layout.addWidget(self.posting_scroll, 1)
        self.posting_fields=QVBoxLayout(posting_contents)
        self.posting_fields.setContentsMargins(16,14,16,14)
        self.posting_fields.setSpacing(12)
        self.posting_fields.addWidget(label('投稿設定','section'))
        self.selected_title=label('一覧から記事を選んでください。')
        self.selected_title.setWordWrap(True)
        self.posting_fields.addWidget(self.selected_title)
        self.splitter.addWidget(posting)
        tagrow = QHBoxLayout()
        tagrow.addWidget(label("ハッシュタグ"))
        tagrow.addStretch()
        self.favorite_button = button("保存済みタグから追加", self.favorite_menu)
        tagrow.addWidget(self.favorite_button)
        self.posting_fields.addLayout(tagrow)
        self.tags = QLineEdit()
        self.tags.setObjectName("tags")
        self.tags.setPlaceholderText("#日記 #エッセイ　空白・カンマで区切る")
        self.tags.textChanged.connect(self.mark_dirty)
        self.posting_fields.addWidget(self.tags)
        form = QFormLayout()
        self.mode = StableDropdown()
        self.mode.setObjectName("post_mode")
        self.mode.addItem("下書きとして保存", "draft")
        self.mode.addItem("定期投稿のキューへ追加", "queued")
        self.mode.addItem("日時を指定して投稿", "scheduled")
        self.mode.currentIndexChanged.connect(self.mode_changed)
        form.addRow("投稿方法", self.mode)
        self.when = QDateTimeEdit(jst_qdate(now_ts() + 3600))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy/MM/dd HH:mm '（日本時間）'")
        self.when.dateTimeChanged.connect(self.mark_dirty)
        form.addRow("投稿日時", self.when)
        self.posting_fields.insertLayout(2,form)
        self.recurrence_label = label("", "muted")
        self.recurrence_label.setWordWrap(True)
        self.posting_fields.addWidget(self.recurrence_label)
        self.error_label = label("")
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.posting_fields.addWidget(self.error_label)
        preview_header = QHBoxLayout()
        preview_header.addWidget(label('本文プレビュー', 'section'))
        preview_header.addStretch()
        self.local_draft_button = button('下書き入力', self.edit_local_draft)
        self.edit_button = button('noteで編集', self.edit_native, True, 'native_edit')
        self.cancel_edit_button = button('中止', self.cancel_edit)
        for control in (self.local_draft_button, self.edit_button, self.cancel_edit_button):
            preview_header.addWidget(control)
        self.posting_fields.addLayout(preview_header)
        self.body = RichPreview()
        self.body.setObjectName('article_preview')
        self.body.setAccessibleName('記事の本文プレビュー')
        self.body.setReadOnly(True)
        self.body.setMinimumHeight(270)
        self.body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.posting_fields.addWidget(self.body, 1)
        self.save_state = label('', 'muted')
        self.posting_fields.addWidget(self.save_state)
        footer = QFrame()
        footer.setObjectName('posting_actions')
        footer.setStyleSheet('QFrame#posting_actions {border-top: 1px solid #dce5df;}')
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(16, 10, 16, 10)
        footer_layout.setSpacing(8)
        posting_layout.addWidget(footer)
        actions = QHBoxLayout()
        self.save_button = button("投稿設定を保存", self.save_current, True, "save")
        self.publish_button = button("今すぐ投稿", self.publish_now)
        self.verify_button = button("公開結果を確認", self.verify)
        self.open_button = button("記事を開く", self.open_article)
        self.history_button = button("履歴", self.history)
        for b in (self.save_button,self.publish_button):
            actions.addWidget(b)
        footer_layout.addLayout(actions)
        results=QHBoxLayout()
        for b in (self.verify_button,self.open_button,self.history_button):
            results.addWidget(b)
        footer_layout.addLayout(results)
        self.splitter.setSizes(self.store.setting("management_splitter_sizes", [760,360]))
        self.splitter_binding=bind_splitter_anchor(self.splitter,fixed_index=1,
            initial_size=self.store.setting('management_splitter_sizes',[760,360])[1],min_size=330,max_size=500,handle_width=8)
        self.statusBar().showMessage("原稿を登録して、設定からnoteへログインしてください。")

    def show_normal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    def foreground_busy(self):
        return self.busy() and not getattr(self.worker,'headless_sync',False)

    def notify(self, message, warning=False):
        self.notice.setText(message)
        self.notice.show()
        self.statusBar().showMessage(message)
        if warning:
            from .diagnostics import Diagnostics
            Diagnostics(self.store).error('application_warning_displayed')

    def fail(self, exc):
        from .diagnostics import Diagnostics
        Diagnostics(self.store).error('gui_operation_error',exc)
        self.notify(str(exc), True)

    def mark_dirty(self, *_):
        if self.loading or not self.current_id:
            return
        self.dirty = True
        self.save_state.setText("未保存の変更")

    def mode_changed(self, *_):
        self.when.setEnabled(self.mode.currentData() == "scheduled")
        self.mark_dirty()

    def can_leave(self):
        if not self.dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("未保存の変更")
        box.setText("編集中の原稿を保存しますか？")
        save = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("変更を破棄", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == save:
            return self.save_current()
        if box.clickedButton() == discard:
            self.dirty = False
            return True
        return False

    def selection_changed(self, item, previous):
        if self.loading:
            return
        chosen = item.data(Qt.ItemDataRole.UserRole) if item else None
        if chosen == self.current_id:
            return
        if not self.can_leave():
            self.list.blockSignals(True)
            self.list.setCurrentItem(previous)
            self.list.blockSignals(False)
            return
        self.load(chosen)

    def load(self, post_id):
        self.loading = True
        try:
            self.current_id = post_id
            self.current = self.store.get(post_id) if post_id else None
            p = self.current or {}
            if p.get('native_html'):
                self.body.setHtml(p['native_html'])
            else:
                self.body.setPlainText(p.get('body') or ('本文をまだ取得していません。' if p.get('draft_key') else
                    '本文がありません。' if p else '記事を選ぶと本文が表示されます。'))
            self.body.verticalScrollBar().setValue(0)
            self.tags.setText(" ".join("#" + tag for tag in p.get("tags", [])))
            self.image = p.get("image", "")
            self.mode.setCurrentIndex(max(0, self.mode.findData(p.get("status", "draft"))))
            self.when.setDateTime(jst_qdate(p.get("due") or now_ts() + 3600))
            self.when.setEnabled(self.mode.currentData() == "scheduled")
            self.dirty = False
            self.save_state.setText(('note保存確認済み' if p.get('draft_key') and p.get('native_ready') else
                                     'note上の記事・内容未確認' if p.get('draft_key') else 'PCに仮保存・note未保存') if p else '')
            self.refresh_controls()
        except Exception as exc:
            self.fail(exc)
        finally:
            self.loading = False

    def refresh_controls(self):
        p = self.current or {}
        locked = p.get("status") in LOCKED
        self.save_button.setEnabled(bool(p) and not locked)
        self.local_draft_button.setVisible(bool(p) and not p.get('draft_key') and not locked)
        self.local_draft_button.setEnabled(not self.busy())
        self.tags.setEnabled(bool(p) and not locked)
        self.favorite_button.setEnabled(bool(p) and not locked)
        self.mode.setEnabled(bool(p) and not locked)
        self.when.setEnabled(bool(p) and not locked and self.mode.currentData()=='scheduled')
        self.selected_title.setText(p.get('title') or ('無題の原稿' if p else '一覧から記事を選んでください。'))
        self.publish_button.setEnabled(bool(p) and not locked and not self.busy())
        self.publish_button.setEnabled(self.publish_button.isEnabled() and bool(p.get('native_ready')))
        self.import_button.setEnabled(not self.foreground_busy())
        self.edit_button.setEnabled(bool(p) and p.get('status') not in {'posting','unknown','editing','native_reserved'}
                                    and not self.foreground_busy())
        self.cancel_edit_button.setEnabled(self.busy() and self.worker.action=='edit')
        self.cancel_edit_button.setVisible(self.cancel_edit_button.isEnabled())
        self.verify_button.setEnabled(bool(p.get("draft_key")) and p.get("status") in {"unknown", "failed", "held"} and not self.busy())
        self.open_button.setEnabled(bool(p.get("url") or p.get("draft_key")))
        self.duplicate_button.setEnabled(bool(p))
        self.delete_button.setEnabled(bool(p) and p.get("status") not in {"posting", "unknown",'editing'})
        self.history_button.setEnabled(bool(p))
        summary=p.get('native_summary',{})
        price=summary.get('price',0)
        kind=(f'有料 {price}円' if price else ('限定公開' if summary.get('limited') else '無料公開'))
        readiness=('取り込み済み · '+kind+' · 投稿先 '+p.get('native_owner','')+' · '+summary.get('status','')) if p.get('native_ready') else '未取り込み：noteで編集し、公開設定の最後の投稿操作でアプリへ保存してください（この段階では公開しません）。'
        self.error_label.setText(p.get("error", "") or (readiness if p else ''))

    def refresh_list(self, *_):
        try:
            posts = self.store.posts(with_catalog=True)
            query = self.search.text().casefold()
            filter_key = self.filter_group.checkedButton().property('status')
            statuses = ARTICLE_FILTERS[filter_key][1]
            visible = [p for p in posts if (not statuses or p["status"] in statuses) and
                       query in (p["title"] + "\n" + p["body"] + " " + " ".join(p["tags"])).casefold()]
            order = self.order_group.checkedButton().property('order')
            if order != 'manual':
                visible.sort(key=lambda post: article_timestamp(post, post), reverse=order == 'newest')
            rows = []
            for p in visible:
                detail = STATUSES[p["status"]]
                remote=p['remote_status']
                detail += ' / note: '+(remote or ('保存済み' if p['draft_key'] else '未保存'))
                if p["due"]:
                    detail += " · " + datetime.fromtimestamp(p["due"], JST).strftime("%m/%d %H:%M")
                tags=' '.join('#'+tag for tag in p['tags'])
                title = p["title"] or "無題の原稿"
                rows.append((p["id"], title + "\n" + detail + ('\n'+tags if tags else ''),
                             title + "\n" + p["error"], ARTICLE_ROW_COLORS[p["status"]]))
            self.list.blockSignals(True)
            scroll = None
            if rows != getattr(self, '_displayed_rows', None):
                scroll = capture_scroll(self.list)
                self.list.clear()
                for pid, text, tooltip, color in rows:
                    item = QListWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, pid)
                    item.setToolTip(tooltip)
                    item.setBackground(QColor(color))
                    self.list.addItem(item)
                self._displayed_rows = rows
            selected_row = next((index for index, row in enumerate(rows) if row[0] == self.current_id), -1)
            if self.list.currentRow() != selected_row:
                self.list.setCurrentItem(self.list.item(selected_row) if selected_row >= 0 else None)
            if scroll is not None:
                restore_scroll(self.list,scroll,keep_bottom=False)
            for key, name in (('draft', 'ストック'), ('queued', '投稿待ち'), ('published', '投稿済み'), ('review', '要確認')):
                statuses = ARTICLE_FILTERS[key][1]
                self.count_labels[key].setText(f"{name} {sum(p['status'] in statuses for p in posts)}")
        except Exception as exc:
            self.fail(exc)
        finally:
            self.list.blockSignals(False)

    def refresh(self):
        self.refresh_list()
        paused = self.store.setting("paused", True)
        self.pause_button.setText("自動投稿を開始" if paused else "自動投稿を一時停止")
        self.account_label.setText((self.store.setting("account", "") or "未接続") + (" · 接続確認済み" if self.store.setting("account_verified", False) else ""))
        recurrence = self.store.setting("recurrence", {"days": list(range(7)), "time": "09:00"})
        self.recurrence_label.setText("定期投稿：" + "・".join("月火水木金土日"[i] for i in recurrence["days"]) + " " + recurrence["time"] + "　／　noteの公開設定を保持")
        if self.current_id and not self.dirty:
            try:
                self.current = self.store.get(self.current_id)
            except ValueError:
                self.load(None)
        self.refresh_controls()

    def new_post(self):
        if not self.can_leave():
            return
        source = self.ask_draft_content('新規下書き')
        if source is None:
            return
        try:
            self.search.clear()
            self.filter_buttons[''].setChecked(True)
            self.load(self.store.create(source))
            self.refresh_list()
            self.tabs.setCurrentWidget(self.management_page)
            self.save_current()
        except Exception as exc:
            self.fail(exc)

    def ask_draft_content(self, caption, source=None):
        source = source or {}
        dialog = QDialog(self)
        dialog.setWindowTitle(caption)
        dialog.resize(min(840, self.width()), min(680, self.height()))
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('タイトル'))
        title = QLineEdit(source.get('title', ''))
        title.setObjectName('title')
        layout.addWidget(title)
        layout.addWidget(label('本文'))
        body = QPlainTextEdit(source.get('body', ''))
        layout.addWidget(body, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setText('下書きを保存')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('キャンセル')
        def update_save():
            save.setEnabled(bool(title.text().strip() and body.toPlainText().strip()))
        title.textChanged.connect(update_save)
        body.textChanged.connect(update_save)
        update_save()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return {'title': title.text(), 'body': body.toPlainText()}

    def edit_local_draft(self):
        if self.busy() or not self.current_id or not self.can_leave():
            return
        source = self.store.get(self.current_id)
        if source['draft_key']:
            return
        content = self.ask_draft_content('下書き入力', source)
        if content is not None and self.save_current(draft_content=content):
            self.load(self.current_id)
            self.refresh_list()

    def save_current(self, *, draft_content=None):
        if not self.current_id:
            return False
        try:
            status = self.mode.currentData()
            source=self.store.get(self.current_id)
            content = draft_content if draft_content is not None and not source['draft_key'] else source
            title, body = content['title'], content['body']
            due=self.when.dateTime().toSecsSinceEpoch() if status=='scheduled' else None
            warning = self.store.save(self.current_id, title, body,
                            tags_from_text(self.tags.text()), self.image, 'draft' if not source['native_ready'] else status,
                            due)
            self.dirty = False
            self.current = self.store.get(self.current_id)
            if self.store.setting('account_verified',False):
                from .remote import submit
                submit(self.store,'save_note_draft',self.current_id,{'due':due,'queue':status=='queued'})
                self.save_state.setText('note保存待ち')
            else:
                self.save_state.setText('PCに仮保存・note未保存')
            self.refresh()
            self.statusBar().showMessage('PCに仮保存しました。note保存は処理結果で確認してください。',5000)
            if warning:
                self.notify(warning, True)
            return True
        except Exception as exc:
            self.fail(exc)
            return False

    def edit_native(self):
        post_id=self.current_id
        if self.pending_edit_id:
            self.statusBar().showMessage('同期完了後に、先ほど選んだ記事をnoteで開きます。')
            return
        if self.foreground_busy() or not post_id:
            return
        try:
            if not self.store.setting('account_verified',False):
                raise ValueError('設定から利用者本人のnoteアカウントへログインしてください。')
            if not self.can_leave():
                return
            if self.foreground_busy() or self.quitting:
                return
            if self.busy():
                worker=self.worker
                self.pending_edit_id=post_id
                worker.finished.connect(self.resume_pending_edit,Qt.ConnectionType.QueuedConnection)
                self.statusBar().showMessage('同期完了後に、選んだ記事をnoteで開きます。')
                self.refresh_controls()
                if not worker.isRunning():
                    QTimer.singleShot(0,self.resume_pending_edit)
                return
            self.open_native_editor(post_id)
        except Exception as exc:
            self.fail(exc)

    def resume_pending_edit(self):
        post_id=self.pending_edit_id
        if not post_id or self.quitting:
            self.pending_edit_id=None
            return
        try:
            if self.can_leave() and not self.busy() and not self.quitting:
                self.open_native_editor(post_id)
        except Exception as exc:
            self.fail(exc)
        finally:
            self.pending_edit_id=None
            self.refresh_controls()

    def open_native_editor(self,post_id):
        if not self.store.setting('account_verified',False):
            raise ValueError('設定から利用者本人のnoteアカウントへログインしてください。')
        self.tabs.setCurrentWidget(self.management_page)
        post=self.store.begin_native_edit(post_id)
        self.start_worker('edit',post)

    def import_drafts(self):
        if self.foreground_busy():
            return
        if not self.store.setting('account_verified',False):
            self.fail('設定から利用者本人のnoteアカウントへログインしてください。')
            return
        from .remote import submit
        submit(self.store,'sync_articles')
        self.notify('記事同期を受け付けました。')

    def browser_data(self,data):
        if data.get('action')=='drafts':
            self.pending_drafts=data['items']
        elif data.get('action')=='import':
            errors=[row.get('title',row.get('key',''))+': '+row.get('error','') for row in data['results'] if not row['ok']]
            if errors:
                self.notify('一部の下書きを取り込めませんでした。成功分は保存済みです。\n'+'\n'.join(errors),True)

    def cancel_edit(self):
        if self.busy() and self.worker.action=='edit':
            self.worker.cancel.set()

    def duplicate(self):
        if not self.current_id or not self.can_leave():
            return
        try:
            self.load(self.store.create(self.store.get(self.current_id)))
            self.refresh_list()
            if self.store.setting('account_verified',False):
                from .remote import submit
                submit(self.store,'save_note_draft',self.current_id)
        except Exception as exc:
            self.fail(exc)

    def delete(self):
        if not self.current_id:
            return
        post=self.store.get(self.current_id)
        if QMessageBox.question(self, "記事を削除", 'note上の記事も削除します。よろしいですか？' if post['draft_key'] else '未保存の原稿を削除しますか？') != QMessageBox.StandardButton.Yes:
            return
        try:
            if post['draft_key']:
                from .remote import submit
                submit(self.store,'delete_note',self.current_id,{'confirmed_updated':post['updated']})
            else:
                self.store.delete(self.current_id)
            self.dirty = False
            self.load(None)
            self.refresh()
        except Exception as exc:
            self.fail(exc)

    def order_changed(self, radio, checked):
        if checked:
            manual = radio is self.order_buttons['manual']
            self.move_up.setEnabled(manual)
            self.move_down.setEnabled(manual)
            self.refresh_list()
            self.list.verticalScrollBar().setValue(0)

    def reorder(self, direction):
        if self.current_id and self.order_buttons['manual'].isChecked():
            try:
                self.store.reorder(self.current_id, direction)
                self.refresh_list()
            except Exception as exc:
                self.fail(exc)

    def favorite_menu(self):
        menu = QMenu(self)
        tags = self.store.setting("favorite_tags", [])
        if not tags:
            menu.addAction("設定からよく使うタグを保存できます").setEnabled(False)
        for tag in tags:
            menu.addAction("#" + tag, lambda tag=tag: self.add_favorite(tag))
        menu.exec(self.favorite_button.mapToGlobal(self.favorite_button.rect().bottomLeft()))

    def add_favorite(self, tag):
        try:
            tags = tags_from_text(self.tags.text())
            if tag not in tags:
                tags.append(tag)
            self.tags.setText(" ".join("#" + t for t in tags))
        except Exception as exc:
            self.fail(exc)

    def settings(self):
        dialog = SettingsDialog(self.store, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            if dialog.request_login:
                self.start_worker("login", expected=self.store.setting("account", ""))

    def toggle_pause(self):
        try:
            paused = self.store.setting("paused", True)
            if paused and not self.store.setting("account_verified", False):
                raise ValueError("設定からnote IDを登録し、「ログイン・接続確認」を完了してください。")
            self.store.set_setting("paused", not paused)
            if paused:
                self.timer.start(1000)
            self.refresh()
            self.notify("自動投稿を開始しました。PC起動中に実行します。" if paused else "自動投稿を一時停止しました。処理中の1件は結果確認まで続行します。")
        except Exception as exc:
            self.fail(exc)

    def publish_now(self):
        if self.busy() or not self.current_id:
            return
        try:
            if not self.store.setting("account_verified", False):
                raise ValueError("設定から「ログイン・接続確認」を完了してください。")
            if not self.can_leave():
                return
            post = self.store.get(self.current_id)
            summary=post.get('native_summary',{})
            if QMessageBox.question(self, "今すぐ投稿", f"「{post['title']}」を {post.get('native_owner','')} へ投稿しますか？\n価格: {summary.get('price',0)}円 / 状態: {summary.get('status','')}\n有料部分・限定公開・通知などの設定も取り込み内容を反映します。") != QMessageBox.StandardButton.Yes:
                return
            claimed = self.store.claim(now_ts(), post_id=self.current_id)
            if claimed:
                self.start_worker("publish", claimed)
        except Exception as exc:
            self.fail(exc)

    def verify(self):
        if not self.busy() and self.current_id:
            self.start_worker("verify", self.store.get(self.current_id))

    def open_article(self):
        if self.busy() or not self.current_id:
            return
        p = self.store.get(self.current_id)
        if not p['url']:
            self.tabs.setCurrentWidget(self.management_page)
            if p['draft_key']:
                self.edit_native()
            return
        QDesktopServices.openUrl(QUrl(p['url']))

    def history(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("投稿履歴")
        dialog.resize(660, 400)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText("\n\n".join(datetime.fromtimestamp(e["at"], JST).strftime("%Y/%m/%d %H:%M:%S") + "　" + STATUSES.get(e["status"], e["status"]) + "\n" + e["message"] for e in self.store.history(self.current_id)) or "まだ投稿履歴がありません。")
        layout.addWidget(text)
        layout.addWidget(button("閉じる", dialog.accept))
        dialog.exec()

    def start_worker(self, action, post=None, expected="",items=None,due=None):
        if self.busy():
            return
        self.worker = BrowserWorker(self.store, action, post, expected, self)
        self.worker.items=items or []
        self.worker.import_due=due
        self.worker.data.connect(self.browser_data)
        self.worker.progress.connect(lambda msg: self.statusBar().showMessage(msg))
        self.worker.outcome.connect(self.worker_result)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()
        self.refresh()

    def worker_result(self, status, message, url):
        if self.worker and self.worker.action in {'publish','verify'}:
            self.store.notification(status in {'published','native_reserved','native_private'},message,url)
        if status=='connected':
            from .remote import submit
            submit(self.store,'sync_articles')
            for post in self.store.posts():
                if not post['draft_key'] and post['title'].strip() and post['body'].strip() and post['status'] not in LOCKED:
                    submit(self.store,'save_note_draft',post['id'],{'due':post['due'],'queue':post['status']=='queued'},'migrate:'+post['id'])
        if status not in {"published", "connected",'prepared','native_reserved','native_private','listed','imported'}:
            self.timer.stop()
        if status=='prepared' and self.current_id and self.worker.post and self.worker.post['id']==self.current_id and not self.dirty:
            self.load(self.current_id)
        self.notify(message, status not in {"published", "connected",'prepared','native_reserved','native_private','listed','imported'})
        self.refresh()

    def worker_finished(self):
        self.refresh_controls()
        if self.quitting:
            self.finish_quit()
        elif self.pending_drafts is not None:
            items=self.pending_drafts
            self.pending_drafts=None
            if not items:
                self.notify('note側に下書きがありません。')
                return
            dialog=DraftImportDialog(items,self)
            if dialog.exec()==QDialog.DialogCode.Accepted:
                due=dialog.when.dateTime().toSecsSinceEpoch() if dialog.schedule.isChecked() else None
                self.start_worker('import',items=dialog.selected_keys(),due=due)

    def tick(self):
        if self.quitting:
            return
        try:
            post = self.scheduler.tick(busy=self.busy() or self.dirty or self.pending_edit_id is not None)
            if post:
                self.start_worker("publish", post)
            # Poll only database metadata, never rebuild the editor or move focus.
            self.revision += 1
            if self.revision % 5 == 0:
                self.refresh()
                if self.dirty and not self.store.setting("paused", True):
                    self.statusBar().showMessage("未保存の編集があるため、自動投稿を保留しています。保存して続行してください。")
        except Exception as exc:
            self.timer.stop()
            try:
                self.store.set_setting("paused", True)
                self.refresh()
            except Exception:
                pass
            self.fail("自動投稿を停止しました: " + str(exc))

    def persist_layout(self):
        results = self.store.settings_batch({"window_size": [self.width(), self.height()], "management_splitter_sizes": self.splitter.sizes(),
            'ui_state':{'selected':self.current_id,'search':self.search.text(),'filter':self.filter_group.checkedButton().property('status'),'scroll':self.list.verticalScrollBar().value(),
                        'article_order':self.order_group.checkedButton().property('order'),
                        'page':'discord' if self.tabs.currentWidget() is self.discord_panel else 'management'}})
        failures = [key + ": " + error for key, error in results.items() if error]
        if failures:
            self.fail("画面設定の一部を保存できません: " + "\n".join(failures))
        return not failures

    def closeEvent(self, event):
        if self.quitting:
            if hasattr(self,'command_server'):
                self.command_server.server.close()
            event.accept()
            return
        event.ignore()
        self.quit_app()

    def quit_app(self):
        if self.busy() and self.worker.action=='edit':
            if QMessageBox.question(self,'noteで編集中','取り込み前の公開設定は未保存です。本文をnoteへ下書き保存して終了しますか？')!=QMessageBox.StandardButton.Yes:
                return
        if not self.can_leave() or not self.persist_layout():
            return
        self.quitting = True
        self.timer.stop()
        if self.busy():
            self.worker.cancel.set()
            self.notify("処理の中止と結果保存が完了してから終了します。")
        else:
            self.finish_quit()

    def finish_quit(self):
        if (list(self.store.root.glob('browser-owner*.json')) or list((self.store.root/'discord-browser').glob('browser-owner*.json'))) and not hasattr(self,'browser_cleanup'):
            import sys
            from .platform import app_folder
            self.browser_cleanup=QProcess(self)
            arguments=['--close-browser','--data-dir',str(self.store.root)]
            if not getattr(sys,'frozen',False):
                arguments.insert(0,str(app_folder()/'main.py'))
            self.browser_cleanup.finished.connect(self.browser_cleanup_finished)
            self.browser_cleanup.errorOccurred.connect(self.browser_cleanup_failed)
            self.browser_cleanup.start(sys.executable,arguments)
            self.notify('専用ブラウザーを終了しています。')
            return
        self.final_exit()

    def browser_cleanup_finished(self, code, _status):
        if code:
            from .diagnostics import Diagnostics
            Diagnostics(self.store).error('browser_cleanup_error',exit_code=code)
        self.final_exit()

    def browser_cleanup_failed(self, error):
        from .diagnostics import Diagnostics
        Diagnostics(self.store).error('browser_cleanup_process_error',reason=error.name)
        if error==QProcess.ProcessError.FailedToStart:
            self.final_exit()

    def final_exit(self):
        from .relogin import manager
        manager(self.store).stop()
        from .schedule_activity import stop_host
        stop_host(self.store)
        if hasattr(self,'discord_panel') and not self.discord_panel.shutdown():
            QTimer.singleShot(500,self.final_exit)
            return
        self.close()
        QApplication.instance().quit()
