from datetime import datetime,timezone
from note_stock.calendar_image import reservation_counts,calendar_png


def test_reservation_dates_use_japan_time_and_ignore_published_and_cancelled_posts():
    # UTC Jan 25 15:30 is Jan 26 00:30 in Japan.
    due=datetime(2028,1,25,15,30,tzinfo=timezone.utc).timestamp()
    posts=[{'status':state,'due':due} for state in ['scheduled','native_reserved','published','draft']]
    posts+=[{'status':'native_reserved','due':None},{'status':'queued','due':None}]
    counts,unknown=reservation_counts(posts,2028,1)
    assert counts=={26:2} and unknown==1
    assert reservation_counts(posts,2028,2)[0]=={}


def test_calendar_png_has_seven_fixed_columns_when_scaled_to_phone_width():
    from note_stock.qt import QImage
    raw=calendar_png(2026,11,26,{26:2,28:1})
    image=QImage.fromData(raw)
    assert image.width()==560 and image.height()>300
    phone=image.scaledToWidth(280)
    assert phone.width()==280 and not phone.isNull()
    assert raw!=calendar_png(2026,11,26,{})
