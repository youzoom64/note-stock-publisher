from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel, QSizePolicy, QStatusBar, QVBoxLayout, QWidget


class ActionStatusBar(QStatusBar):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.waits={}
        self.prompt=QLabel()
        self.prompt.setTextFormat(Qt.TextFormat.PlainText)
        self.prompt.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.prompt.setContentsMargins(4,0,4,0)
        self.prompt.setStyleSheet('color:#653400; font-weight:600;')
        self.detail=QLabel()
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        self.detail.setContentsMargins(4,0,4,0)
        self.detail.setStyleSheet('color:#783527;')
        self.detail.hide()
        self.wait_panel=QWidget()
        layout=QVBoxLayout(self.wait_panel)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(2)
        layout.addWidget(self.prompt)
        layout.addWidget(self.detail)
        self.glow=QGraphicsDropShadowEffect(self.prompt)
        self.glow.setOffset(0,0)
        self.glow.setBlurRadius(8)
        self.prompt.setGraphicsEffect(self.glow)
        self.addPermanentWidget(self.wait_panel,1)
        self.wait_panel.hide()
        self.setStyleSheet('QStatusBar::item {border:0;}')
        self.messageChanged.connect(self.on_message_changed)
        self.fade=QVariantAnimation(self)
        self.fade.setDuration(6000)
        self.fade.setLoopCount(-1)
        self.fade.setKeyValues([(0.0,0.0),(0.5,1.0),(1.0,0.0)])
        self.fade.valueChanged.connect(self.set_intensity)
        self.set_intensity(0.0)

    def set_intensity(self,value):
        eased=QEasingCurve(QEasingCurve.Type.InOutSine).valueForProgress(value)
        self.glow.setColor(QColor(244,145,12,round(255*eased)))

    def on_message_changed(self,message):
        self.setToolTip(message)
        self.detail.clear()
        self.detail.hide()

    def show_notice(self,message,warning):
        self.showMessage(' / '.join(message.splitlines()))
        self.setToolTip(message)
        self.detail.setText(message if warning else '')
        self.detail.setVisible(warning)

    def wait_for_action(self,source,message):
        if message:
            self.waits[source]=message
        else:
            self.waits.pop(source,None)
        active=next((text for key,text in reversed(self.waits.items()) if key!='initial'),
                    self.waits.get('initial',''))
        self.prompt.setText(active)
        if active:
            self.prompt.setToolTip(active)
            if self.wait_panel.isHidden():
                self.wait_panel.show()
                self.fade.start()
        else:
            self.fade.stop()
            self.set_intensity(0.0)
            self.wait_panel.hide()
