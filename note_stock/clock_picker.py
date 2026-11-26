"""Analog time selection for the desktop posting date fields."""

from math import atan2, cos, floor, hypot, pi, sin

from PySide6.QtCore import QDate, QEvent, QPointF, QRectF, Qt, QTime, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QTimeEdit, QVBoxLayout, QWidget,
)
from .calendar_notes import DateNotes


def dial_hour(position,morning):
    return position % 12 + (0 if morning else 12)


class HandClock(QWidget):
    date_changed=Signal(QDate)
    time_changed=Signal(QTime)

    def __init__(self,date,time,parent=None):
        super().__init__(parent)
        self.date=date
        self.time=time
        self.dragging=None
        self.drag_fraction=None
        self.setMinimumSize(250,250)
        self.setAccessibleName('短針で時、長針で分を選ぶアナログ時計')
        self.setProperty('cursorHelp','短針をドラッグすると時、長針をドラッグすると分を選べます。')

    def set_time(self,time):
        if self.time!=time:
            self.time=time
            self.update()

    def center(self):
        return QPointF(self.width()/2,self.height()/2)

    def radius(self):
        return min(self.width(),self.height())/2-8

    def point_on_face(self,fraction,radius):
        angle=2*pi*fraction
        return self.center()+QPointF(sin(angle)*radius,-cos(angle)*radius)

    def hand_point(self,hand):
        minute=self.time.minute()/60
        fraction=(self.time.hour()+minute)/12 if hand=='hour' else minute
        if hand==self.dragging and self.drag_fraction is not None:
            fraction=self.drag_fraction
        return self.point_on_face(fraction,self.radius()*(.52 if hand=='hour' else .83))

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius=self.radius()
        center=self.center()
        painter.setPen(QPen(QColor('#c9d8d0'),1))
        painter.setBrush(QColor('#ffffff'))
        painter.drawEllipse(center,radius,radius)
        for minute in range(60):
            fraction=minute/60
            painter.setPen(QPen(QColor('#63786d' if minute%5==0 else '#c9d8d0'),1))
            painter.drawLine(self.point_on_face(fraction,radius-5),
                             self.point_on_face(fraction,radius-(12 if minute%5==0 else 8)))
        painter.setPen(QColor('#25342f'))
        for position in range(12):
            point=self.point_on_face(position/12,radius-27)
            painter.drawText(QRectF(point.x()-15,point.y()-10,30,20),Qt.AlignmentFlag.AlignCenter,
                             str(position or 12))
        for hand,color,thickness in (('minute','#16845e',3),('hour','#25342f',5)):
            end=self.hand_point(hand)
            pen=QPen(QColor(color),thickness)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(center,end)
            painter.setBrush(QColor(color))
            painter.drawEllipse(end,5,5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#25342f'))
        painter.drawEllipse(center,4,4)

    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        point=event.position()
        delta=point-self.center()
        if hypot(delta.x(),delta.y())<12:
            return
        distances=[]
        for hand in ('hour','minute'):
            end=self.hand_point(hand)-self.center()
            length_sq=end.x()**2+end.y()**2
            projection=max(0,min(1,(delta.x()*end.x()+delta.y()*end.y())/length_sq))
            nearest=end*projection
            distance=hypot(delta.x()-nearest.x(),delta.y()-nearest.y())
            tip=hypot(delta.x()-end.x(),delta.y()-end.y())
            distances.append((0 if tip<=10 else 1,distance,hand))
        _,distance,hand=min(distances)
        if distance<=12:
            self.dragging=hand
            if hand=='minute':
                self.drag_start_date=self.date
                self.drag_minutes=self.time.hour()*60+self.time.minute()
            event.accept()

    def move_hand(self,point):
        delta=point-self.center()
        if hypot(delta.x(),delta.y())<9:
            return
        fraction=atan2(delta.x(),-delta.y())/(2*pi)
        hour,minute=self.time.hour(),self.time.minute()
        if self.dragging=='hour':
            hour=dial_hour(round(fraction*12-minute/60),hour<12)
        else:
            previous=self.drag_fraction if self.drag_fraction is not None else minute/60
            # Unwrap each movement across twelve so completed turns are retained.
            movement=(fraction-previous+.5)%1-.5
            self.drag_minutes+=movement*60
            days,minutes=divmod(floor(self.drag_minutes+.5),24*60)
            date=self.drag_start_date.addDays(days)
            if date!=self.date:
                self.date=date
                self.date_changed.emit(date)
            hour,minute=divmod(minutes,60)
        self.drag_fraction=fraction
        time=QTime(hour,minute)
        if time!=self.time:
            self.set_time(time)
            self.time_changed.emit(time)
        self.update()

    def mouseMoveEvent(self,event):
        if self.dragging is not None:
            self.move_hand(event.position())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if self.dragging is not None and event.button()==Qt.MouseButton.LeftButton:
            if self.drag_fraction is not None:
                self.move_hand(event.position())
            self.end_drag()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def event(self,event):
        if event.type() in {QEvent.Type.UngrabMouse,QEvent.Type.WindowDeactivate}:
            self.end_drag()
        return super().event(event)

    def end_drag(self):
        self.dragging=None
        self.drag_fraction=None
        self.update()


class PostingTimeDialog(QDialog):
    def __init__(self,date,time,parent=None):
        super().__init__(parent)
        self.setWindowTitle('投稿時刻を選ぶ')
        self.setMinimumWidth(315)
        layout=QVBoxLayout(self)
        periods=QHBoxLayout()
        self.am=QPushButton('午前 00〜11時')
        self.pm=QPushButton('午後 12〜23時')
        self.periods=QButtonGroup(self)
        for control,morning in ((self.am,True),(self.pm,False)):
            control.setCheckable(True)
            control.setProperty('cursorHelp','同じ針の位置で午前（00〜11時）を選びます。' if morning else
                                '同じ針の位置で午後（12〜23時）を選びます。')
            control.clicked.connect(lambda _,morning=morning:self.select_period(morning))
            self.periods.addButton(control)
            periods.addWidget(control)
        layout.addLayout(periods)
        self.date_label=QLabel()
        self.update_date_label(date)
        layout.addWidget(self.date_label,alignment=Qt.AlignmentFlag.AlignHCenter)
        self.clock=HandClock(date,time)
        layout.addWidget(self.clock,1)
        row=QHBoxLayout()
        row.addWidget(QLabel('短針で時、長針で分'))
        row.addStretch()
        self.time_edit=QTimeEdit(time)
        self.time_edit.setDisplayFormat('HH:mm')
        self.time_edit.setKeyboardTracking(False)
        self.time_edit.setAccessibleName('投稿時刻（日本時間）')
        self.time_edit.setProperty('cursorHelp','投稿時刻の時と分を数字で入力できます。')
        row.addWidget(self.time_edit)
        layout.addLayout(row)
        self.notes=DateNotes(self)
        self.notes.set_date(date)
        layout.addWidget(self.notes)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('この時刻にする')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('キャンセル')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setStyleSheet('QPushButton:checked {background:#16845e; color:#ffffff; border-color:#16845e;}')
        self.clock.date_changed.connect(self.update_date_label)
        self.clock.date_changed.connect(self.notes.set_date)
        self.clock.time_changed.connect(self.time_edit.setTime)
        self.time_edit.timeChanged.connect(self.refresh_clock)
        self.refresh_clock(time)

    def select_period(self,morning):
        time=self.time_edit.time()
        self.time_edit.setTime(QTime(dial_hour(time.hour(),morning),time.minute()))

    def update_date_label(self,date):
        self.date_label.setText(date.toString('yyyy/MM/dd')+'（日本時間）')

    def refresh_clock(self,time):
        self.clock.set_time(time)
        (self.am if time.hour()<12 else self.pm).setChecked(True)

    def accept(self):
        self.time_edit.interpretText()
        super().accept()


class ClockTimeEdit(QWidget):
    timeChanged=Signal(QTime)

    def __init__(self, time, parent=None):
        super().__init__(parent)
        self.clock_open = False
        self.editor=QTimeEdit(time,self)
        self.editor.setButtonSymbols(QTimeEdit.ButtonSymbols.NoButtons)
        self.editor.setToolTip('数字を直接入力して時刻を変更できます。')
        self.editor.timeChanged.connect(self.timeChanged.emit)
        self.clock_button=QPushButton(self)
        self.clock_button.setText('時計')
        self.clock_button.setAccessibleName('時計で時刻を選ぶ')
        self.clock_button.setToolTip('時計を開いて時刻を選びます。')
        self.clock_button.clicked.connect(self.open_clock)
        layout=QHBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(6)
        layout.addWidget(self.editor,1)
        layout.addWidget(self.clock_button)
        self.setFocusProxy(self.editor)

    def time(self):
        return self.editor.time()

    def setTime(self,time):
        self.editor.setTime(time)

    def setDisplayFormat(self,format):
        self.editor.setDisplayFormat(format)

    def setKeyboardTracking(self,enabled):
        self.editor.setKeyboardTracking(enabled)

    def interpretText(self):
        self.editor.interpretText()

    def open_clock(self):
        if self.clock_open:
            return
        self.clock_open = True
        try:
            dialog = PostingTimeDialog(QDate.currentDate(), self.time(), self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.setTime(dialog.time_edit.time())
        finally:
            self.clock_open = False
