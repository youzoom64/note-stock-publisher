"""Calendar pixels stay in seven columns at every Discord client width."""
import calendar
from datetime import datetime
from .store import JST

_image_app=None


def reservation_counts(posts,year,month):
    counts={};unknown=0
    for post in posts:
        if post.get('status') not in {'scheduled','queued','native_reserved'}:continue
        due=post.get('due')
        if due is None:
            if post.get('status')=='native_reserved':unknown+=1
            continue
        try:date=datetime.fromtimestamp(due,JST)
        except (ValueError,TypeError,OSError,OverflowError):
            unknown+=1;continue
        if (date.year,date.month)==(year,month):counts[date.day]=counts.get(date.day,0)+1
    return counts,unknown


def calendar_png(year,month,selected,counts):
    from PySide6.QtCore import QByteArray,QBuffer,QIODevice,QRectF,Qt
    from PySide6.QtGui import QImage,QPainter,QColor,QFont,QFontMetrics
    from PySide6.QtWidgets import QApplication
    global _image_app
    if QApplication.instance() is None:_image_app=QApplication([])
    weeks=calendar.Calendar(firstweekday=6).monthdayscalendar(year,month)
    width=560;top=108;cell_h=68
    image=QImage(width,top+cell_h*len(weeks)+20,QImage.Format.Format_RGB32)
    image.fill(QColor('#1e1f29'))
    painter=QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    def text(rect,label,size,color,bold=False):
        font=QFont();font.setPixelSize(size);font.setBold(bold)
        while QFontMetrics(font).horizontalAdvance(label)>rect.width()-6 and font.pixelSize()>9:
            font.setPixelSize(font.pixelSize()-1)
        painter.setFont(font);painter.setPen(QColor(color))
        painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,label)
    try:
        text(QRectF(0,12,width,40),f'{year}年{month}月',28,'#f2f3f5',True)
        for col,weekday in enumerate('日月火水木金土'):
            color='#ff9c9c' if col==0 else '#9fc5ff' if col==6 else '#c1c4ce'
            text(QRectF(col*80,64,80,32),weekday,22,color,True)
        for row,week in enumerate(weeks):
            for col,day in enumerate(week):
                if not day:continue
                x=col*80;y=top+row*cell_h
                if day==selected:
                    painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#5865f2'))
                    painter.drawEllipse(QRectF(x+21,y+1,38,38))
                color='#ffffff' if day==selected else '#ffb5b5' if col==0 else '#aecfff' if col==6 else '#f2f3f5'
                text(QRectF(x+2,y,76,40),str(day),26,color,day==selected)
                if counts.get(day):
                    text(QRectF(x+2,y+40,76,25),str(counts[day])+'件',20,'#aeb6ff',True)
    finally:painter.end()
    data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer,'PNG'):raise RuntimeError('カレンダー画像を作成できませんでした。')
    return bytes(data)
