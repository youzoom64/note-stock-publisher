from __future__ import annotations

from datetime import datetime, timedelta
from html import escape

from PySide6.QtCore import Qt, QDate, QDateTime, QEvent, QTime, QTimeZone, QTimer, QSize, QUrl, QProcess, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPalette, QPixmap, QTextCharFormat, QAction, QKeySequence, QDrag, QCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QPlainTextEdit, QComboBox, QListWidget, QListWidgetItem, QSplitter,
    QFormLayout, QCalendarWidget, QDateEdit, QDateTimeEdit, QDialog, QDialogButtonBox, QCheckBox,
    QMessageBox, QFrame, QScrollArea, QMenu, QTabWidget,
    QButtonGroup, QRadioButton, QSpinBox, QStyle, QStyledItemDelegate, QTableView, QToolButton, QStyleOptionViewItem, QSystemTrayIcon,
    QAbstractItemView, QToolTip,
)

from .platform import autostart_enabled, set_autostart
from .publisher import account_id
from .store import JST, LOCKED, STATUSES, Scheduler, article_timestamp, posting_order_key, now_ts, tags_from_text
from .store import DEFAULT_ARTICLE_SYNC_MINUTES, MIN_ARTICLE_SYNC_MINUTES, MAX_ARTICLE_SYNC_MINUTES
from .worker import BrowserWorker
from .preview import RichPreview
from .action_status import ActionStatusBar
from .calendar_notes import DateNotes
from .japanese_calendar import annotations
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
ARTICLE_ROW_COLORS.update({'scheduled':'#DCC3AA','native_reserved':'#DCC3AA'})
ARTICLE_LEGEND = (
    ('draft','ストック',ARTICLE_FILTERS['draft'][1],ARTICLE_ROW_COLORS['draft']),
    ('queued','キュー',frozenset({'queued','posting'}),ARTICLE_ROW_COLORS['queued']),
    ('scheduled','日時指定',frozenset({'scheduled','native_reserved'}),ARTICLE_ROW_COLORS['scheduled']),
    ('published','投稿済み',ARTICLE_FILTERS['published'][1],ARTICLE_ROW_COLORS['published']),
    ('review','要確認',ARTICLE_FILTERS['review'][1],ARTICLE_ROW_COLORS['failed']),
)

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
QComboBox#post_mode:disabled {background:#d5d9d7; color:#525d56; border-color:#aeb7b2;}
QComboBox#post_mode::drop-down:disabled {background:#d5d9d7; border:0;}
QComboBox#post_mode::down-arrow:disabled {image:none; width:0; height:0;}
QLineEdit:focus, QPlainTextEdit:focus {border:1px solid #16845e;}
QSpinBox#article_sync_minutes {background:#ffffff; color:#25342f; border:1px solid #cddbd2; border-radius:5px; padding:4px;}
QSpinBox#article_sync_minutes:focus {background:#ffffff; border-color:#16845e;}
QSpinBox#article_sync_minutes QLineEdit {background:#ffffff; color:#25342f; border:0; padding:0; selection-background-color:#16845e; selection-color:#ffffff;}
QCalendarWidget {background:#ffffff; color:#25342f;}
QCalendarWidget QWidget#qt_calendar_navigationbar {background:#e5ede7;}
QCalendarWidget QToolButton {background:#e5ede7; color:#25342f; border:0; padding:7px; font-size:16px;}
QCalendarWidget QToolButton:hover {background:#cde3d6;}
QCalendarWidget QAbstractItemView {background:#ffffff; alternate-background-color:#eaf0ec; color:#25342f; selection-background-color:#16845e; selection-color:#ffffff;}
QCalendarWidget QSpinBox {background:#ffffff; color:#25342f;}
QLabel#calendar_notes {background:#f4f7f5; color:#25342f; padding:6px 9px;}
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
        check = QStyleOptionViewItem(option)
        self.initStyleOption(check,index)
        text_left = row.left() + 13
        if check.features & QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator:
            style = option.widget.style()
            check.rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator,check,option.widget)
            check.state &= ~(QStyle.StateFlag.State_On | QStyle.StateFlag.State_Off)
            check.state |= (QStyle.StateFlag.State_On if check.checkState == Qt.CheckState.Checked
                            else QStyle.StateFlag.State_Off)
            style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck,check,painter,option.widget)
            text_left = check.rect.right() + 10

        painter.setFont(option.font)
        painter.setPen(QColor('#153f2e' if selected else '#25342f'))
        metrics = painter.fontMetrics()
        text = index.data(Qt.ItemDataRole.DisplayRole)
        lines = text.split('\n')
        baseline = row.top() + (row.height() - len(lines) * metrics.height()) // 2 + metrics.ascent()
        for line in lines:
            elided = metrics.elidedText(line, Qt.TextElideMode.ElideRight, max(0, row.right() - text_left - 10))
            painter.drawText(text_left, baseline, elided)
            baseline += metrics.height()
        painter.restore()


class ArticleList(QListWidget):
    queue_order_requested = Signal(list)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.dragged_id=None
        self.drag_slots=[]
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)

    def queued_ids(self):
        return [self.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.count())
                if self.item(index).data(Qt.ItemDataRole.UserRole+1)=='queued']

    def order_at(self,point):
        ordered=self.queued_ids()
        if self.dragged_id not in ordered:
            return None
        row=self.indexAt(point).row()
        boundary=self.count() if row<0 else row+(point.y()>self.visualItemRect(self.item(row)).center().y())
        following=[self.item(index).data(Qt.ItemDataRole.UserRole) for index in range(boundary,self.count())
                   if self.item(index).data(Qt.ItemDataRole.UserRole+1)=='queued'
                   and self.item(index).data(Qt.ItemDataRole.UserRole)!=self.dragged_id]
        ordered.remove(self.dragged_id)
        target=ordered.index(following[0]) if following else len(ordered)
        ordered.insert(target,self.dragged_id)
        return ordered

    def startDrag(self,actions):
        self.dragged_id=self.currentItem().data(Qt.ItemDataRole.UserRole)
        planned=self.window().store.queue_schedule()
        self.drag_slots=sorted(planned[pid] for pid in self.queued_ids() if pid in planned)
        try:
            drag=QDrag(self)
            drag.setMimeData(self.model().mimeData([self.indexFromItem(self.currentItem())]))
            rect=self.visualItemRect(self.currentItem())
            drag.setPixmap(self.viewport().grab(rect))
            drag.setHotSpot(self.viewport().mapFromGlobal(QCursor.pos())-rect.topLeft())
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.dragged_id=None
            self.drag_slots=[]
            QToolTip.hideText()

    def dragMoveEvent(self,event):
        if event.source() is not self:
            event.ignore()
            return
        ordered=self.order_at(event.position().toPoint())
        if not ordered:
            event.ignore()
            return
        event.acceptProposedAction()
        target=ordered.index(self.dragged_id)
        if target<len(self.drag_slots):
            label='移動後の投稿予定 '+datetime.fromtimestamp(self.drag_slots[target],JST).strftime('%Y/%m/%d %H:%M')
            QToolTip.showText(self.viewport().mapToGlobal(event.position().toPoint()),label,self)

    def dropEvent(self,event):
        ordered=self.order_at(event.position().toPoint()) if event.source() is self else None
        if ordered:
            self.queue_order_requested.emit(ordered)
            event.acceptProposedAction()
        else:
            event.ignore()


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


class PostingCalendar(QCalendarWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setMinimumSize(528,502)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        for name,arrow,help_text in (
                ('qt_calendar_prevmonth','‹','前の月を表示します。'),
                ('qt_calendar_nextmonth','›','次の月を表示します。')):
            control=self.findChild(QToolButton,name)
            control.setIcon(QIcon())
            control.setArrowType(Qt.ArrowType.NoArrow)
            control.setText(arrow)
            control.setAccessibleName(help_text)
            control.setProperty('cursorHelp',help_text)
        self.notes=DateNotes(self)
        self.layout().addWidget(self.notes)
        self.view=self.findChild(QTableView,'qt_calendar_calendarview')
        self.view.viewport().setMouseTracking(True)
        self.view.viewport().installEventFilter(self)
        self.selectionChanged.connect(lambda:self.notes.set_date(self.selectedDate()))
        self.currentPageChanged.connect(lambda year,month:self.notes.set_date(QDate(year,month,1)))

    def showEvent(self,event):
        self.notes.set_date(self.selectedDate())
        super().showEvent(event)

    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.MouseMove and watched is self.view.viewport():
            self.show_hover_date(self.view.indexAt(event.position().toPoint()))
        return super().eventFilter(watched,event)

    def show_hover_date(self,index):
        if index.row()<1:
            return
        first=QDate(self.yearShown(),self.monthShown(),1)
        day=index.model().index(1,0).data()
        if not isinstance(day,int):
            return
        if day!=1:
            previous=first.addMonths(-1)
            first=QDate(previous.year(),previous.month(),day)
        self.notes.set_date(first.addDays((index.row()-1)*7+index.column()))

    def paintCell(self,painter,rect,date):
        rokuyo,holiday=annotations(date.toPython()) if 1931<=date.year()<=2100 else ('','')
        selected=date==self.selectedDate()
        in_month=date.month()==self.monthShown()
        usable=self.minimumDate()<=date<=self.maximumDate()
        sunday=date.dayOfWeek()==7
        saturday=date.dayOfWeek()==6
        fill='#16845e' if selected else '#f5f7f6' if not in_month else (
            '#fff1ee' if holiday or sunday else '#edf5fb' if saturday else '#ffffff')
        primary='#ffffff' if selected else '#7c8b83' if not usable or not in_month else (
            '#9b3028' if holiday or sunday else '#225d86' if saturday else '#25342f')
        secondary='#ffffff' if selected else '#6a7b71' if usable and in_month else '#7c8b83'
        painter.save()
        painter.fillRect(rect,QColor(fill))
        painter.setPen(QColor('#e0e8e3' if not selected else '#16845e'))
        painter.drawRect(rect.adjusted(0,0,-1,-1))
        font=painter.font()
        font.setPixelSize(17)
        font.setBold(selected or bool(holiday))
        painter.setFont(font)
        painter.setPen(QColor(primary))
        painter.drawText(rect.adjusted(1,3,-1,0),Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop,
                         self.locale().toString(date.day()))
        font.setPixelSize(12)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(secondary))
        painter.drawText(rect.adjusted(1,23,-1,0),Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop,rokuyo)
        if holiday:
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(primary))
            name=painter.fontMetrics().elidedText(holiday,Qt.TextElideMode.ElideRight,rect.width()-3)
            painter.drawText(rect.adjusted(1,39,-1,0),Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop,name)
        painter.restore()


def enable_calendar_popup(editor):
    editor.setCalendarPopup(True)
    calendar=PostingCalendar(editor)
    editor.setCalendarWidget(calendar)
    palette=calendar.palette()
    for role,color in (
            (QPalette.ColorRole.Window,'#ffffff'),
            (QPalette.ColorRole.Base,'#ffffff'),
            (QPalette.ColorRole.AlternateBase,'#eaf0ec'),
            (QPalette.ColorRole.WindowText,'#25342f'),
            (QPalette.ColorRole.Text,'#25342f'),
            (QPalette.ColorRole.Button,'#e5ede7'),
            (QPalette.ColorRole.ButtonText,'#25342f'),
            (QPalette.ColorRole.Highlight,'#16845e'),
            (QPalette.ColorRole.HighlightedText,'#ffffff')):
        palette.setColor(role,QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled,QPalette.ColorRole.Text,QColor('#65746c'))
    calendar.setPalette(palette)
    heading=QTextCharFormat()
    heading.setBackground(QColor('#eaf0ec'))
    calendar.setHeaderTextFormat(heading)
    for day,color in ((Qt.DayOfWeek.Saturday,'#225d86'),(Qt.DayOfWeek.Sunday,'#9b3c33')):
        fmt=QTextCharFormat()
        fmt.setForeground(QColor(color))
        calendar.setWeekdayTextFormat(day,fmt)


class PostingDateTimeEdit(QDateTimeEdit):
    def __init__(self,date_time,parent=None):
        super().__init__(date_time,parent)
        self.setDisplayFormat("yyyy/MM/dd HH:mm '（日本時間）'")
        self.setToolTip('カレンダーで日付を選ぶと時計が開きます。日時を数字で直接入力することもできます。')
        enable_calendar_popup(self)
        self.clock_pending=False
        self.calendarWidget().clicked.connect(self.request_clock)
        self.calendarWidget().activated.connect(self.request_clock)

    def request_clock(self,date):
        if self.clock_pending:
            return
        self.clock_pending=True
        QTimer.singleShot(0,self,lambda:self.pick_time(date))

    def pick_time(self,date):
        from .clock_picker import PostingTimeDialog
        try:
            if self.isVisible() and self.isEnabled():
                dialog=PostingTimeDialog(date,self.time(),self)
                if dialog.exec()==QDialog.DialogCode.Accepted:
                    value=self.dateTime()
                    value.setDate(dialog.clock.date)
                    value.setTime(dialog.time_edit.time())
                    self.setDateTime(value)
        finally:
            self.clock_pending=False


class SettingsDialog(QDialog):
    setting_saved=Signal(str)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.request_login = False
        self.save_errors = {}
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
        self.browser.setProperty('cursorHelp','noteやDiscordを開く専用ブラウザーを選びます。')
        for text,value,help_text in (
                ('自動（Chrome → Edge → Chromium）','auto','利用できるブラウザーをChrome、Edge、Chromiumの順に探します。'),
                ('Google Chrome','chrome','専用ブラウザーにGoogle Chromeを使います。'),
                ('Microsoft Edge','edge','専用ブラウザーにMicrosoft Edgeを使います。'),
                ('Chromium（別途導入）','chromium','別途導入したChromiumを使います。')):
            self.browser.addItem(text,value)
            self.browser.setItemData(self.browser.count()-1,help_text,Qt.ItemDataRole.ToolTipRole)
        self.browser.setCurrentIndex(max(0,self.browser.findData(store.setting('browser','auto'))))
        form.addRow("ブラウザー", self.browser)
        self.login_method=StableDropdown()
        self.login_method.setProperty('cursorHelp','noteへのログイン方法を選びます。')
        self.login_method.addItem('メールアドレス・パスワード','password')
        self.login_method.setItemData(0,'noteのメールアドレスとパスワードでログインします。',Qt.ItemDataRole.ToolTipRole)
        self.login_method.addItem('Google（通常ブラウザーで本人がログイン）','google')
        self.login_method.setItemData(1,'ブラウザーで「Googleでログイン」を選びます。',Qt.ItemDataRole.ToolTipRole)
        self.login_method.setCurrentIndex(max(0,self.login_method.findData(store.setting('note_login_method','password'))))
        form.addRow('noteのログイン方法',self.login_method)
        self.article_sync_minutes=QSpinBox()
        self.article_sync_minutes.setObjectName('article_sync_minutes')
        self.article_sync_minutes.setProperty('cursorHelp','noteの記事一覧を自動で同期する間隔を分単位で設定します。')
        self.article_sync_minutes.setRange(MIN_ARTICLE_SYNC_MINUTES,MAX_ARTICLE_SYNC_MINUTES)
        self.article_sync_minutes.setSuffix(' 分')
        self.article_sync_minutes.setKeyboardTracking(False)
        self.article_sync_minutes.setValue(store.setting('article_sync_minutes',DEFAULT_ARTICLE_SYNC_MINUTES))
        form.addRow('記事一覧の自動同期間隔',self.article_sync_minutes)
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
        from .clock_picker import ClockTimeEdit
        self.time = ClockTimeEdit(QTime.fromString(recurrence["time"], "HH:mm"))
        self.time.setDisplayFormat("HH:mm")
        self.time.setKeyboardTracking(False)
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
        self.cursor_help_enabled = QCheckBox("カーソルヘルプを表示する")
        self.cursor_help_enabled.setChecked(store.setting("cursor_help_enabled", True))
        layout.addWidget(self.cursor_help_enabled)
        layout.addWidget(self.autostart)
        layout.addWidget(self.diagnostics)
        layout.addWidget(button('拡張機能：Discord内で再ログイン',self.remote_extension))
        info = label("原稿・画像・ログイン状態は、このアプリのdataフォルダー内に保存します。\n終了・スリープ中は投稿しません。期限を過ぎた予約は再予約が必要です。", "muted")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.save_status=label('変更は自動保存されます。','muted')
        self.save_status.setWordWrap(True)
        layout.addWidget(self.save_status)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText("閉じる")
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self.settings_fields={
            'account':('note ID',self.read_account,''),
            'browser':('ブラウザー',self.browser.currentData,'auto'),
            'note_login_method':('noteのログイン方法',self.login_method.currentData,'password'),
            'article_sync_minutes':('記事一覧の自動同期間隔',self.article_sync_minutes.value,DEFAULT_ARTICLE_SYNC_MINUTES),
            'recurrence':('定期投稿',self.read_recurrence,recurrence),
            'favorite_tags':('よく使うハッシュタグ',lambda:tags_from_text(self.favorites.toPlainText()),[]),
            'cursor_help_enabled':('カーソルヘルプ',self.cursor_help_enabled.isChecked,True),
            'diagnostics':('診断ログ',self.diagnostics.isChecked,DEFAULT_ENABLED),
            'autostart':('Windowsログイン時の起動',self.autostart.isChecked,False),
        }
        for key,changed in (
                ('account',self.account.editingFinished),('browser',self.browser.currentIndexChanged),
                ('note_login_method',self.login_method.currentIndexChanged),
                ('article_sync_minutes',self.article_sync_minutes.valueChanged),
                ('recurrence',self.time.timeChanged),
                ('cursor_help_enabled',self.cursor_help_enabled.toggled),
                ('diagnostics',self.diagnostics.toggled),('autostart',self.autostart.toggled)):
            changed.connect(lambda *_,field=key:self.save_field(field))
        for check in self.days:
            check.toggled.connect(lambda:self.save_field('recurrence'))
        self.tags_save_timer=QTimer(self)
        self.tags_save_timer.setSingleShot(True)
        self.tags_save_timer.setInterval(500)
        self.tags_save_timer.timeout.connect(lambda:self.save_field('favorite_tags'))
        self.favorites.textChanged.connect(self.tags_save_timer.start)

    def connect_account(self):
        self.flush_edits()
        self.save_field('browser')
        self.save_field('note_login_method')
        if any(key in self.save_errors for key in ('account','browser','note_login_method')):
            return
        if not self.store.setting('account',''):
            self.show_save_status('接続するnote IDを入力してください。')
            self.account.setFocus()
            return
        if self.parent() and self.parent().busy():
            self.show_save_status('処理が終わってから、ログイン・接続確認を押してください。')
            return
        self.request_login = True
        self.accept()

    def remote_extension(self):
        from .relogin_ui import ReloginDialog
        ReloginDialog(self.store,self).exec()

    def read_account(self):
        text=self.account.text().strip()
        return account_id(text) if text else ''

    def read_recurrence(self):
        days=[i for i,check in enumerate(self.days) if check.isChecked()]
        if not days:
            raise ValueError('定期投稿の曜日を1つ以上選んでください。')
        return {'days':days,'time':self.time.time().toString('HH:mm')}

    def save_field(self,key):
        caption,read,default=self.settings_fields[key]
        changed=False
        try:
            value=read()
            previous=autostart_enabled() if key=='autostart' else self.store.setting(key,default)
            if value!=previous:
                if key in {'account','browser'}:
                    if self.parent() and self.parent().busy():
                        raise ValueError('処理が終わってから変更してください。')
                    self.store.set_note_connection_setting(key,value,default)
                elif key=='autostart':
                    set_autostart(value)
                else:
                    self.store.set_setting(key,value)
                changed=True
        except Exception as exc:
            from .diagnostics import Diagnostics
            error=f'{caption}を保存できませんでした。{exc}'
            if self.save_errors.get(key)!=error:
                Diagnostics(self.store).error('setting_save_error',exc,setting=key)
            self.save_errors[key]=error
            self.show_save_status()
            return
        cleared=self.save_errors.pop(key,None)
        if changed:
            self.setting_saved.emit(key)
        if changed or cleared:
            self.show_save_status('変更を自動保存しました。' if changed else '変更は自動保存されます。')

    def show_save_status(self,message=''):
        self.save_status.setText('\n'.join(self.save_errors.values()) or message)

    def flush_edits(self):
        self.article_sync_minutes.interpretText()
        self.time.interpretText()
        self.save_field('account')
        if self.tags_save_timer.isActive():
            self.tags_save_timer.stop()
            self.save_field('favorite_tags')

    def done(self,result):
        self.flush_edits()
        super().done(result)


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
        self.when=PostingDateTimeEdit(jst_qdate(now_ts()+3600))
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
        self.checked_ids = set()
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
        recovered = store.recover(start_automatic_posting=not offline)
        from .toggle_sound import ToggleSound
        self.toggle_sound = ToggleSound(store, self)
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
        self.remote_controller=RemoteController(self,catalog_import_enabled=not offline)
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
        from .cursor_help import CursorHelp
        self.cursor_help=CursorHelp(self,enabled=self.store.setting('cursor_help_enabled',True))

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 8, 22, 12)
        layout.setSpacing(6)
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
        for key,name,_,color in ARTICLE_LEGEND:
            entry = QHBoxLayout()
            entry.setSpacing(4)
            swatch = QLabel()
            swatch.setFixedSize(14, 14)
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
        self.filter_group.idToggled.connect(self.filter_changed)
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
        self.list = ArticleList()
        self.list.setObjectName("posts")
        self.list.setItemDelegate(ArticleRowDelegate(self.list))
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.list.currentItemChanged.connect(self.selection_changed)
        self.list.itemDoubleClicked.connect(self.request_selected_article)
        self.list.itemChanged.connect(self.article_check_changed)
        self.list.queue_order_requested.connect(self.apply_queue_order)
        checks = QHBoxLayout()
        self.check_all_button = button('全チェック',self.check_all_drafts)
        self.check_all_button.setToolTip('表示中の下書きとキューの記事をすべてチェックします。')
        self.clear_checks_button = button('チェック解除',self.clear_article_checks)
        self.queue_checked_button = button('キューへ追加 (0)',self.queue_checked_articles,True)
        self.queue_checked_button.setProperty('cursorHelp','チェックした下書きを表示順に、現在のキューの後ろへ追加します。')
        self.queue_checked_button.setEnabled(False)
        self.interrupt_checked_button = button('割り込み',self.interrupt_checked_articles)
        self.interrupt_checked_button.setProperty('cursorHelp','指定した日の投稿に割り込み、後続の記事を順番を保ったまま1日ずつ後ろへ送ります。')
        self.interrupt_checked_button.setEnabled(False)
        self.cancel_queue_button = button('キュー解除 (0)',self.cancel_checked_queue)
        self.cancel_queue_button.setProperty('cursorHelp','チェックしたキュー記事を下書きへ戻します。')
        self.cancel_queue_button.setEnabled(False)
        for control in (self.check_all_button,self.clear_checks_button):
            checks.addWidget(control)
        checks.addStretch()
        pane.addLayout(checks)
        queue_actions = QHBoxLayout()
        for control in (self.queue_checked_button,self.interrupt_checked_button,self.cancel_queue_button):
            queue_actions.addWidget(control)
        pane.addLayout(queue_actions)
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
        for key, name in (('manual', '手動順'), ('newest', '新しい順'), ('oldest', '古い順'), ('planned','投稿予定順')):
            radio = QRadioButton(name)
            radio.setProperty('articleFilter', True)
            radio.setProperty('order', key)
            radio.setAccessibleName(f"記事の並べ替え: {name}")
            if key=='planned':
                radio.setToolTip('投稿予定が早い順に表示します。黄色のキューはドラッグで順番を変更でき、茶色の日時指定は固定です。')
            elif key != 'manual':
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
        self.posting_mode_form=form
        self.mode.setObjectName("post_mode")
        self.mode.addItem("下書きとして保存（投稿未定）", "draft")
        self.mode.addItem("定期投稿のキューへ追加", "queued")
        self.mode.addItem("日時を指定して投稿", "scheduled")
        self.mode.setItemData(0,'投稿を予約せず、下書きとして保存します。',Qt.ItemDataRole.ToolTipRole)
        self.mode.setItemData(1,'設定した曜日と時刻の定期投稿キューに追加します。',Qt.ItemDataRole.ToolTipRole)
        self.mode.setItemData(2,'指定した日付と時刻に投稿するよう予約します。',Qt.ItemDataRole.ToolTipRole)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        form.addRow("投稿方法", self.mode)
        self.when = PostingDateTimeEdit(jst_qdate(now_ts() + 3600))
        self.when.dateTimeChanged.connect(self.mark_dirty)
        form.addRow("投稿日時", self.when)
        self.recurring_override_button=button('定期投稿の日に差し込む…',self.edit_recurring_override)
        form.addRow(self.recurring_override_button)
        self.posting_fields.insertLayout(2,form)
        self.recurrence_label = label("", "muted")
        self.recurrence_label.setWordWrap(True)
        self.posting_fields.addWidget(self.recurrence_label)
        self.planned_time_label = QLabel()
        self.planned_time_label.setWordWrap(True)
        self.planned_time_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.posting_fields.addWidget(self.planned_time_label)
        self.public_url_label = QLabel()
        self.public_url_label.setTextFormat(Qt.TextFormat.RichText)
        self.public_url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.public_url_label.setOpenExternalLinks(True)
        self.public_url_label.setWordWrap(True)
        self.posting_fields.addWidget(self.public_url_label)
        self.error_label = label("")
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.posting_fields.addWidget(self.error_label)
        preview_header = QHBoxLayout()
        preview_header.addWidget(label('本文プレビュー', 'section'))
        self.preview_progress=label('', 'muted')
        self.preview_progress.hide()
        preview_header.addWidget(self.preview_progress)
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
        self.setStatusBar(ActionStatusBar(self))

    def show_normal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if getattr(self,'startup_setup_pending',False):
            self.startup_setup_pending = False
            self.tabs.setCurrentWidget(self.discord_panel)
            QTimer.singleShot(0,self.discord_panel.setup)

    def enable_tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(),self)
        self.tray.setToolTip('noteストック')
        menu = QMenu(self)
        menu.addAction('開く',self.show_normal)
        menu.addSeparator()
        menu.addAction('終了',self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray_menu = menu
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()

    def tray_activated(self,reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_normal()

    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    def foreground_busy(self):
        return self.busy() and not getattr(self.worker,'headless_sync',False)

    def notify(self, message, warning=False):
        self.statusBar().show_notice(message,warning)
        if warning:
            from .diagnostics import Diagnostics
            Diagnostics(self.store).error('application_warning_displayed')

    def note_progress(self,message):
        self.statusBar().wait_for_action('note','')
        self.statusBar().showMessage(message)

    def note_action_required(self,message):
        self.statusBar().wait_for_action('note',message)

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
        self.refresh_posting_datetime()
        self.mark_dirty()

    def refresh_posting_datetime(self):
        post=self.current or {}
        scheduled=bool(post) and self.mode.currentData()=='scheduled'
        self.posting_mode_form.setRowVisible(self.when,scheduled)
        self.when.setEnabled(scheduled and post.get('status') not in LOCKED)

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

    def request_selected_article(self,*_):
        if self.loading or not self.current_id:
            return
        try:
            from .remote import request_selected_article
            if request_selected_article(self.store,self.current_id,background=True):
                self.refresh_list()
                self.statusBar().showMessage('選択した記事の本文・公開条件を取得しています。')
        except Exception as exc:
            self.fail(exc)

    def load(self, post_id):
        self.loading = True
        try:
            self.current_id = post_id
            self.current = self.store.get(post_id) if post_id else None
            p = self.current or {}
            preview=self.store.article_preview(post_id) if p.get('draft_key') else None
            if preview is not None:
                if preview['html']:
                    self.body.setHtml(preview['html'])
                else:
                    self.body.setPlainText(preview['body'] or '本文がありません。')
            elif p.get('native_html'):
                self.body.setHtml(p['native_html'])
            else:
                self.body.setPlainText(p.get('body') or ('一覧の記事をダブルクリックすると本文を読み込みます。' if p.get('draft_key') else
                    '本文がありません。' if p else '記事を選ぶと本文が表示されます。'))
            self.body.verticalScrollBar().setValue(0)
            self.tags.setText(" ".join("#" + tag for tag in p.get("tags", [])))
            self.image = p.get("image", "")
            published=p.get('status')=='published'
            self.mode.setPlaceholderText('投稿済み' if published else '')
            self.mode.setCurrentIndex(-1 if published else max(0,self.mode.findData(p.get('status','draft'))))
            self.when.setDateTime(jst_qdate(p.get("due") or now_ts() + 3600))
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
        planned_due = p.get('due') or (self.store.queue_schedule().get(p['id']) if p.get('status')=='queued' else None)
        self.planned_time_label.setText('投稿予定 '+datetime.fromtimestamp(planned_due,JST).strftime('%Y/%m/%d %H:%M')
                                       +'（日本時間）' if planned_due else '')
        self.planned_time_label.setVisible(bool(planned_due))
        public_url = p.get('url','') if p.get('status')=='published' else ''
        self.public_url_label.setText(
            f'公開URL <a href="{escape(public_url, quote=True)}">{escape(public_url)}</a>' if public_url else ''
        )
        self.public_url_label.setVisible(bool(public_url))
        progress=self.store.preview_job_state(p['id']) if p else None
        if p and getattr(getattr(self,'remote_controller',None),'catalog_import_post_id',None)==p['id']:
            progress='running'
        self.preview_progress.setText('取得中…' if progress=='running' else '取得待ち…')
        self.preview_progress.setVisible(bool(progress))
        locked = p.get("status") in LOCKED
        self.save_button.setEnabled(bool(p) and not locked)
        self.local_draft_button.setVisible(bool(p) and not p.get('draft_key') and not locked)
        self.local_draft_button.setEnabled(not self.busy())
        self.tags.setEnabled(bool(p) and not locked)
        self.favorite_button.setEnabled(bool(p) and not locked)
        self.mode.setEnabled(bool(p) and not locked)
        self.mode.setProperty('cursorHelp','投稿済みの記事は投稿方法を変更できません。' if p.get('status')=='published'
                              else '下書き、定期投稿のキュー、日時指定のいずれかを選びます。' if self.mode.isEnabled()
                              else '投稿方法を変更できる記事を選んでください。')
        self.refresh_posting_datetime()
        queued=p.get('status')=='queued'
        override=self.store.recurring_override(p['id']) if queued else None
        self.recurring_override_button.setVisible(queued)
        self.recurring_override_button.setEnabled(queued and not self.foreground_busy())
        self.recurring_override_button.setText(f'定期投稿に差し込み：{override}' if override else '定期投稿の日に差し込む…')
        self.selected_title.setText(('📃 ' if p.get('native_ready') else '')+(p.get('title') or ('無題の原稿' if p else '一覧から記事を選んでください。')))
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
        readiness=('取り込み済み · '+kind+' · 投稿先 '+p.get('native_owner','')+' · '+summary.get('status','')) if p.get('native_ready') else '選択した記事の本文・公開条件を自動取得します。予約時にも取得を確認します。'
        self.error_label.setText(p.get("error", "") or (readiness if p else ''))

    def refresh_list(self, *_):
        try:
            posts = self.store.posts(with_catalog=True)
            status_by_id = {post['id']:post['status'] for post in posts}
            planned = self.store.queue_schedule()
            self.check_statuses = {p['id']:p['status'] for p in posts if p['status'] in {'draft','queued'}}
            draft_ids = set(self.check_statuses)
            self.checked_ids.intersection_update(draft_ids)
            with self.store.connect() as connection:
                reading = {row['post_id'] for row in connection.execute(
                    "SELECT DISTINCT post_id FROM jobs WHERE action IN ('refresh_article','schedule') "
                    "AND state IN ('waiting','running')"
                )}
                queuing = {row['post_id'] for row in connection.execute(
                    "SELECT post_id FROM jobs WHERE action='save_note_draft' "
                    "AND state IN ('waiting','running') AND json_extract(data,'$.draft_queue')=1"
                )}
            query = self.search.text().casefold()
            filter_key = self.filter_group.checkedButton().property('status')
            statuses = ARTICLE_FILTERS[filter_key][1]
            visible = [p for p in posts if (not statuses or p["status"] in statuses) and
                       query in (p["title"] + "\n" + p["body"] + " " + " ".join(p["tags"])).casefold()]
            order = self.order_group.checkedButton().property('order')
            if order == 'planned':
                visible.sort(key=lambda post:posting_order_key(post,planned))
            elif order != 'manual':
                visible.sort(key=lambda post: article_timestamp(post, post), reverse=order == 'newest')
            rows = []
            for p in visible:
                detail = STATUSES[p["status"]]
                if p["id"] in reading:
                    detail = "本文取得中 · " + detail
                if p["id"] in queuing:
                    detail = "キュー追加中 · " + detail
                remote=p['remote_status']
                detail += ' / note: '+(remote or ('保存済み' if p['draft_key'] else '未保存'))
                if p["due"]:
                    detail += '\n投稿予定 '+datetime.fromtimestamp(p["due"],JST).strftime('%Y/%m/%d %H:%M')+'（日本時間）'
                elif p['id'] in planned:
                    detail += '\n投稿予定 '+datetime.fromtimestamp(planned[p['id']],JST).strftime('%Y/%m/%d %H:%M')+'（日本時間）'
                if p.get('error'):
                    detail += '\n理由: '+p['error']
                tags=' '.join('#'+tag for tag in p['tags'])
                title = ('📃 ' if p.get('native_ready') else '')+(p["title"] or "無題の原稿")
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
                    item.setData(Qt.ItemDataRole.UserRole+1,status_by_id[pid])
                    if item.data(Qt.ItemDataRole.UserRole+1)!='queued':
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
                    if pid in draft_ids:
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(Qt.CheckState.Checked if pid in self.checked_ids else Qt.CheckState.Unchecked)
                    item.setToolTip(tooltip)
                    item.setBackground(QColor(color))
                    self.list.addItem(item)
                self._displayed_rows = rows
            selected_row = next((index for index, row in enumerate(rows) if row[0] == self.current_id), -1)
            if self.list.currentRow() != selected_row:
                self.list.setCurrentItem(self.list.item(selected_row) if selected_row >= 0 else None)
            if scroll is not None:
                restore_scroll(self.list,scroll,keep_bottom=False)
            for key,name,statuses,_ in ARTICLE_LEGEND:
                self.count_labels[key].setText(f"{name} {sum(p['status'] in statuses for p in posts)}")
        except Exception as exc:
            self.fail(exc)
        finally:
            self.list.blockSignals(False)
            self.update_check_controls()
            reorderable=(self.filter_group.checkedButton().property('status')=='queued'
                         and self.order_buttons['planned'].isChecked())
            self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove if reorderable
                                      else QAbstractItemView.DragDropMode.NoDragDrop)

    def apply_queue_order(self,post_ids):
        try:
            self.store.set_queue_order(post_ids)
        except Exception as exc:
            self.fail(exc)
        self._displayed_rows=None
        self.refresh()

    def update_check_controls(self):
        count = len(self.checked_ids)
        drafts = sum(self.check_statuses.get(pid)=='draft' for pid in self.checked_ids)
        queued = sum(self.check_statuses.get(pid)=='queued' for pid in self.checked_ids)
        self.queue_checked_button.setText(f'キューへ追加 ({drafts})')
        self.queue_checked_button.setEnabled(bool(drafts))
        self.clear_checks_button.setEnabled(bool(count))
        self.interrupt_checked_button.setEnabled(bool(count))
        self.cancel_queue_button.setText(f'キュー解除 ({queued})')
        self.cancel_queue_button.setEnabled(bool(queued))

    def article_check_changed(self,item):
        pid = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self.checked_ids.add(pid)
        else:
            self.checked_ids.discard(pid)
        self.update_check_controls()

    def check_all_drafts(self):
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.ItemDataRole.CheckStateRole) is not None:
                item.setCheckState(Qt.CheckState.Checked)

    def clear_article_checks(self):
        self.checked_ids.clear()
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.ItemDataRole.CheckStateRole) is not None:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.update_check_controls()

    def queue_checked_articles(self):
        self.enqueue_checked_articles()

    def cancel_checked_queue(self):
        if not self.can_leave():
            return
        removed = []
        errors = []
        for pid in list(self.checked_ids):
            try:
                post = self.store.get(pid)
                if post['status']!='queued':
                    continue
                self.store.set_schedule(pid,None)
                removed.append(pid)
            except Exception as exc:
                errors.append(f'{pid}: {exc}')
        self.checked_ids.difference_update(removed)
        self._displayed_rows = None
        self.refresh()
        self.notify(f'{len(removed)}件をキューから解除しました。'
                    + ('\n'+'\n'.join(errors) if errors else ''),bool(errors))

    def interrupt_checked_articles(self):
        if not self.checked_ids:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('キューへの割り込み')
        layout = QVBoxLayout(dialog)
        guide = label('選んだ日から、チェックした記事を表示順に1日1本ずつ投稿します。\n'
                      'その日以降の記事は順番を保って後ろへずれます。')
        guide.setWordWrap(True)
        layout.addWidget(guide)
        picker = QDateEdit()
        enable_calendar_popup(picker)
        picker.setDisplayFormat('yyyy/MM/dd')
        tomorrow = QDate.currentDate().addDays(1)
        picker.setMinimumDate(QDate.currentDate())
        picker.setDate(tomorrow)
        layout.addWidget(picker)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('この日から割り込む')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.enqueue_checked_articles(picker.date().toString('yyyy-MM-dd'))

    def enqueue_checked_articles(self,interrupt_day=None):
        if not self.checked_ids or not self.can_leave():
            return
        from .remote import submit
        accepted = []
        errors = []
        batch = self.store.begin_queue_batch()
        posts = {post['id']:post for post in self.store.posts()}
        displayed = [self.list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.list.count())]
        ordered = [pid for pid in displayed if pid in self.checked_ids]
        ordered.extend(pid for pid in self.checked_ids if pid not in displayed)
        for pid in ordered:
            try:
                queued = posts.get(pid,{}).get('status')=='queued'
                if queued and not interrupt_day:
                    continue
                data = {'queue':True,'queue_only':True} if queued else {'queue':True,'draft_queue':True}
                data['queue_batch'] = batch
                if interrupt_day:
                    start = datetime.strptime(interrupt_day,'%Y-%m-%d')
                    data['interrupt_day'] = (start+timedelta(days=len(accepted))).strftime('%Y-%m-%d')
                submit(self.store,'save_note_draft',pid,data)
                accepted.append(pid)
            except Exception as exc:
                errors.append(f"{posts.get(pid,{}).get('title',pid)}: {exc}")
        summary = self.store.seal_queue_batch(batch,errors)
        self.checked_ids.difference_update(accepted)
        self._displayed_rows = None
        self.refresh_list()
        if summary:
            self.notify(summary['message'],not summary['ok'])
        else:
            self.statusBar().showMessage(f'{len(accepted)}件のキュー追加を受け付けました。')

    def refresh(self):
        self.refresh_list()
        paused = self.store.setting("paused", True)
        if hasattr(self,'tray'):
            self.tray.setToolTip('noteストック\n自動投稿 '+('一時停止中' if paused else '動作中'))
        self.pause_button.setText("自動投稿を開始" if paused else "自動投稿を一時停止")
        verified=self.store.setting("account_verified",False)
        self.account_label.setText((self.store.setting("account", "") or "未接続") + (" · 接続確認済み" if verified else ""))
        connecting=self.busy() and self.worker.action in {'login','relogin'}
        self.statusBar().wait_for_action('initial','' if verified or connecting else '設定からnoteへログインしてください。')
        if not self.dirty or paused:
            self.statusBar().wait_for_action('schedule','')
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
            if due is not None and due<=now_ts():
                raise ValueError('投稿日時は現在より先に設定してください。')
            if status=='scheduled' and not self.store.setting('account_verified',False):
                raise ValueError('予約するには設定からnoteへのログインを確認してください。')
            # An existing note is read before a reservation is committed. Saving
            # PC settings must not mark an unverified note as already scheduled.
            local_status=('draft' if not source['native_ready'] else
                          (source['status'] if source['status'] in {'draft','queued','scheduled'} else 'draft')
                          if status=='scheduled' and source['draft_key'] else status)
            local_due=source['due'] if local_status=='scheduled' else None
            warning = self.store.save(self.current_id, title, body,
                            tags_from_text(self.tags.text()), self.image, local_status,
                            local_due if status=='scheduled' and source['draft_key'] else due)
            self.dirty = False
            self.current = self.store.get(self.current_id)
            if self.store.setting('account_verified',False):
                from .remote import submit
                reserving=status=='scheduled'
                action='schedule' if reserving and source['draft_key'] else 'save_note_draft'
                submit(self.store,action,self.current_id,{'due':due,'queue':status=='queued'})
                self.save_state.setText('本文・公開条件を確認して予約登録中' if reserving else 'note保存待ち')
            else:
                self.save_state.setText('PCに仮保存・note未保存')
            self.refresh()
            self.statusBar().showMessage(
                '予約を受け付けました。本文・公開条件を自動確認し、予約登録の結果を表示します。'
                if status=='scheduled' else 'PCに保存しました。note保存は処理結果で確認してください。',5000)
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

    def edit_recurring_override(self):
        post_id=self.current_id
        if not post_id or self.foreground_busy() or not self.can_leave():
            return
        current=self.store.recurring_override(post_id)
        recurrence=self.store.setting('recurrence',{'days':list(range(7)),'time':'09:00'})
        dialog=QDialog(self)
        dialog.setWindowTitle('定期投稿の日に差し込む')
        layout=QVBoxLayout(dialog)
        guide=label(f'選んだ日の {recurrence["time"]}（日本時間）に、この1本を定期投稿します。\n'
                    '元のキューは、順番を保ったまま次回以降へずれます。')
        guide.setWordWrap(True)
        layout.addWidget(guide)
        picker=QDateEdit(dialog)
        enable_calendar_popup(picker)
        picker.setDisplayFormat('yyyy/MM/dd')
        today=datetime.now(JST).date()
        upcoming=next(today+timedelta(days=offset) for offset in range(1,8)
                      if (today+timedelta(days=offset)).weekday() in recurrence['days'])
        picker.setMinimumDate(QDate(today.year,today.month,today.day))
        picker.setDate(QDate.fromString(current,'yyyy-MM-dd') if current and current>=today.isoformat()
                       else QDate(upcoming.year,upcoming.month,upcoming.day))
        layout.addWidget(picker)
        actions=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        actions.button(QDialogButtonBox.StandardButton.Save).setText('差し込みを保存')
        actions.rejected.connect(dialog.reject)
        layout.addWidget(actions)

        def save_override(day):
            try:
                self.store.set_recurring_override(post_id,day)
            except (ValueError,OverflowError) as exc:
                QMessageBox.warning(dialog,'差し込みを保存できません',str(exc))
                return
            dialog.accept()

        actions.accepted.connect(lambda:save_override(picker.date().toString('yyyy-MM-dd')))
        if current:
            remove=actions.addButton('差し込みを外す',QDialogButtonBox.ButtonRole.ActionRole)
            remove.clicked.connect(lambda:save_override(None))
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.refresh_controls()

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

    def fetch_article_preview(self,item):
        post_id=item.data(Qt.ItemDataRole.UserRole)
        if post_id!=self.current_id or self.quitting or not self.can_leave():
            return
        try:
            if self.store.preview_job_state(post_id):
                self.statusBar().showMessage('この本文は読み込み待ち、または読み込み中です。')
                return
            from .remote import submit
            submit(self.store,'preview_article',post_id)
            self.statusBar().showMessage('noteの本文の読み込みを受け付けました。')
            self.refresh_controls()
        except Exception as exc:
            self.fail(exc)

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

    def filter_changed(self, identifier, checked):
        if checked:
            if self.filter_group.checkedButton().property('status')=='queued':
                self.order_buttons['planned'].setChecked(True)
            self.refresh_list()

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
        dialog.setting_saved.connect(self.apply_setting_change)
        result=dialog.exec()
        if dialog.save_errors:
            self.notify('\n'.join(dialog.save_errors.values()),True)
        if result==QDialog.DialogCode.Accepted and dialog.request_login:
            self.start_worker('login',expected=self.store.setting('account',''))

    def apply_setting_change(self,key):
        if key=='cursor_help_enabled':
            self.cursor_help.set_enabled(self.store.setting(key,True))
        elif key in {'account','browser','recurrence'}:
            self.refresh()

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
        self.worker.progress.connect(self.note_progress)
        self.worker.action_required.connect(self.note_action_required)
        self.worker.outcome.connect(self.worker_result)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()
        self.refresh()

    def worker_result(self, status, message, url):
        self.note_action_required('')
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
        self.notify(message+('\n公開URL: '+url if status=='published' and url else ''),
                    status not in {"published", "connected",'prepared','native_reserved','native_private','listed','imported'})
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
                needs_save=(self.dirty and not self.busy() and not self.store.setting("paused",True)
                            and self.store.has_due_post(self.scheduler.last,self.scheduler.pending_slot))
                self.statusBar().wait_for_action('schedule',
                    '未保存の編集があります。保存すると自動投稿を再開します。' if needs_save else '')
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
        if hasattr(self,'tray') and self.tray.isVisible():
            if self.persist_layout():
                self.hide()
        else:
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
        if hasattr(self,'tray'):
            self.tray.hide()
        self.close()
        QApplication.instance().quit()
