from PySide6.QtCore import QByteArray, QUrl
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QTextBrowser


class RichPreview(QTextBrowser):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.network=QNetworkAccessManager(self)
        self.pending=set()
        self.setOpenExternalLinks(True)

    def setPlainText(self,text):
        super().setPlainText(text)

    def setPlaceholderText(self,text):
        self.setToolTip(text)

    def loadResource(self,kind,url):
        if kind==QTextDocument.ResourceType.ImageResource and url.scheme() in {'https','http'}:
            key=url.toString()
            if key not in self.pending:
                self.pending.add(key)
                reply=self.network.get(QNetworkRequest(url))
                def loaded():
                    image=QImage.fromData(bytes(reply.readAll()))
                    if not image.isNull():
                        self.document().addResource(kind,url,image)
                        self.document().markContentsDirty(0,self.document().characterCount())
                        self.viewport().update()
                    reply.deleteLater()
                reply.finished.connect(loaded)
            return QByteArray()
        if url.isLocalFile():
            return QByteArray()
        return super().loadResource(kind,url)
