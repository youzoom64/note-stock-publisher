"""Offline Japanese holiday names and lunisolar month starts."""

from bisect import bisect_right
import csv
from functools import lru_cache
from pathlib import Path

DATA=Path(__file__).with_name('calendar_data')
ROKUYO=('大安','赤口','先勝','友引','先負','仏滅')


@lru_cache(maxsize=1)
def calendar_data():
    from datetime import date
    with (DATA/'kyureki_months.csv').open(encoding='utf-8',newline='') as source:
        months={date.fromisoformat(day).toordinal():int(month) for day,month in list(csv.reader(source))[1:]}
    with (DATA/'japan_holidays.csv').open(encoding='utf-8',newline='') as source:
        holidays=dict(list(csv.reader(source))[1:])
    return sorted(months),months,holidays


def annotations(day):
    starts,months,holidays=calendar_data()
    ordinal=day.toordinal()
    index=bisect_right(starts,ordinal)-1
    rokuyo=''
    if 0<=index<len(starts)-1:
        start=starts[index]
        rokuyo=ROKUYO[(months[start]+ordinal-start+1)%6]
    return rokuyo,holidays.get(day.isoformat(),'')
