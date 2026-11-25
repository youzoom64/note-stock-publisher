"""Japanese Wikipedia date notes, fetched as the user browses a calendar."""

from functools import lru_cache
from html import escape
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication, QLabel


class DateArticle(HTMLParser):
    def __init__(self):
        super().__init__()
        self.entries={'できごと':[],'誕生日':[]}
        self.section=''
        self.item=[]
        self.depth=0
        self.citation=False

    def handle_starttag(self,tag,attrs):
        name=dict(attrs).get('id','')
        if tag=='h2':
            self.section=name if name in self.entries else ''
        elif tag=='h3' and any(word in name for word in ('架空','フィクション')):
            self.section=''
        elif tag=='li' and self.section:
            if not self.depth:
                self.item=[]
            self.depth+=1
        elif tag=='sup':
            self.citation=True

    def handle_endtag(self,tag):
        if tag=='sup':
            self.citation=False
        elif tag=='li' and self.depth:
            self.depth-=1
            if not self.depth:
                text=' '.join(''.join(self.item).split()).strip()
                if self.section and re.match(r'^(?:紀元前)?\d{1,4}年',text):
                    self.entries[self.section].append(text)

    def handle_data(self,data):
        if self.depth==1 and not self.citation:
            self.item.append(data)


def read_date_article(raw):
    article=DateArticle()
    article.feed(json.loads(raw)['parse']['text'])
    def pick(section,minimum):
        entries=article.entries[section]
        return next((line for line in entries if re.match(r'^\d{4}年',line) and int(line[:4])>=minimum),
                    entries[0] if entries else '')
    return pick('できごと',1850),pick('誕生日',1900)


class DateNotesSource(QObject):
    ready=Signal(str)

    def __init__(self):
        super().__init__(QApplication.instance())
        self.network=QNetworkAccessManager(self)
        self.results={}
        self.pending=set()

    def request(self,title):
        if title in self.pending or title in self.results:
            return
        self.pending.add(title)
        url=QUrl('https://ja.wikipedia.org/w/api.php')
        query=QUrlQuery()
        for key,value in (
                ('action','parse'),('prop','text'),('page',title),('format','json'),('formatversion','2')):
            query.addQueryItem(key,value)
        url.setQuery(query)
        request=QNetworkRequest(url)
        request.setTransferTimeout(8000)
        request.setRawHeader(b'User-Agent',b'NoteStockPublisher/2.0 (https://github.com/youzoom64/note-stock-publisher)')
        reply=self.network.get(request)
        reply.finished.connect(lambda:self.finish(title,reply))

    def finish(self,title,reply):
        try:
            if reply.error()==QNetworkReply.NetworkError.NoError:
                events,birthdays=read_date_article(bytes(reply.readAll()))
                if events or birthdays:
                    self.results[title]=(events,birthdays)
        except (ValueError,TypeError,KeyError):
            pass
        finally:
            self.pending.discard(title)
            self.ready.emit(title)
            reply.deleteLater()


@lru_cache(maxsize=1)
def notes_source():
    return DateNotesSource()


class DateNotes(QLabel):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.title=''
        self.source=notes_source()
        self.source.ready.connect(self.show_result)
        self.setObjectName('calendar_notes')
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setOpenExternalLinks(True)
        self.setMinimumHeight(85)
        self.timer=QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fetch)

    def set_date(self,date):
        if not date.isValid():
            return
        title=f'{date.month()}月{date.day()}日'
        if title==self.title:
            return
        self.title=title
        if title in self.source.results:
            self.show_result(title)
        else:
            self.setText(f'<b>{title}の備考</b><br>出来事と誕生日を読み込んでいます…')
            self.timer.start(350)

    def showEvent(self,event):
        super().showEvent(event)
        if self.title and self.title not in self.source.results and not self.timer.isActive():
            self.timer.start(350)

    def fetch(self):
        if self.isVisible() and self.title:
            self.source.request(self.title)

    def show_result(self,title):
        if title!=self.title:
            return
        link=f'<a href="https://ja.wikipedia.org/wiki/{quote(title)}" style="color:#126249;">出典: Wikipedia</a>'
        result=self.source.results.get(title)
        if result:
            event,birthday=(escape(item if len(item)<=100 else item[:99]+'…') for item in result)
            text=f'<b>出来事</b>　{event or "掲載なし"}<br><b>誕生日</b>　{birthday or "掲載なし"}'
        else:
            text='備考を取得できませんでした。出典ページから確認できます。'
        self.setText(f'<b>{title}の備考</b>　{link}<br>{text}')
