"""Qt API shared by the Qt 6 desktop build and the separate Qt 5/x86 build."""
from __future__ import annotations

try:
    from PySide6 import QtCore,QtGui,QtWidgets,QtNetwork,QtTest
    BINDING='PySide6'
except ImportError:
    from PySide2 import QtCore,QtGui,QtWidgets,QtNetwork,QtTest
    BINDING='PySide2'

for _module in (QtCore,QtGui,QtWidgets,QtNetwork,QtTest):
    globals().update({name:getattr(_module,name) for name in dir(_module) if not name.startswith('_')})

if BINDING=='PySide2':
    import json
    from pathlib import Path

    class QTest(QtTest.QTest):
        @staticmethod
        def qWait(milliseconds):
            loop=QtCore.QEventLoop()
            QtCore.QTimer.singleShot(milliseconds,loop.quit)
            loop.exec_()

    if not hasattr(QAbstractItemView,'initViewItemOption'):
        def _init_view_option(self,option):
            option.initFrom(self)
            option.font=self.font()
            option.fontMetrics=self.fontMetrics()
            option.decorationSize=self.iconSize()
        QAbstractItemView.initViewItemOption=_init_view_option

    class _EnumScope:
        def __init__(self,owner,original):
            self.owner,self.original=owner,original
        def __getattr__(self,name):
            if self.original is not None and hasattr(self.original,name):
                return getattr(self.original,name)
            return getattr(self.owner,name)
        def __call__(self,*args):
            return self.original(*args)

    _scopes=json.loads(Path(__file__).with_name('qt5_scopes.json').read_text(encoding='utf-8'))
    for _owner_name,_groups in _scopes.items():
        _owner=globals().get(_owner_name)
        if _owner is None:continue
        for _group,_members in _groups.items():
            _original=getattr(_owner,_group,None)
            if any(not hasattr(_original,member) for member in _members):
                setattr(_owner,_group,_EnumScope(_owner,_original))

    for _class in (QCoreApplication,QApplication,QDialog,QMessageBox,QMenu,QEventLoop):
        if not hasattr(_class,'exec'):
            _class.exec=_class.exec_
    for _class in (QMouseEvent,QHoverEvent,QWheelEvent,QDropEvent):
        if not hasattr(_class,'position'):
            _class.position=lambda self:QPointF(self.pos())

    class QTimer(QtCore.QTimer):
        @staticmethod
        def singleShot(interval,*args):
            if len(args)==2 and isinstance(args[0],QObject) and callable(args[1]):
                context,callback=args
                timer=QtCore.QTimer(context)
                timer.setSingleShot(True)
                timer.timeout.connect(callback)
                timer.timeout.connect(timer.deleteLater)
                timer.start(interval)
            else:
                QtCore.QTimer.singleShot(interval,*args)

    class QFormLayout(QtWidgets.QFormLayout):
        def setRowVisible(self,field,visible):
            if isinstance(field,int):
                row=field
            else:
                row,_=self.getWidgetPosition(field)
            for role in (self.LabelRole,self.FieldRole,self.SpanningRole):
                item=self.itemAt(row,role)
                if item is not None and item.widget() is not None:
                    item.widget().setVisible(visible)
