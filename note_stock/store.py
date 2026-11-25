"""Durable drafts and single-writer posting claims. No network dependency."""
from __future__ import annotations

import json
import math
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from .remote_store import RemoteStore

JST = timezone(timedelta(hours=9))
DEFAULT_ARTICLE_SYNC_MINUTES = 60
MIN_ARTICLE_SYNC_MINUTES = 1
MAX_ARTICLE_SYNC_MINUTES = 1440
STATUSES = {
    "draft": "下書き", "queued": "定期投稿待ち", "scheduled": "日時指定待ち",
    "posting": "投稿中", "published": "投稿済み", "failed": "失敗",
    "held": "ログイン確認待ち", "unknown": "結果未確認", "overdue": "期限超過",
    "editing":"noteで編集中", "native_reserved":"noteで予約済み", "native_private":"noteへ反映済み（非公開）",
    "review":"変更あり・要確認",
}
LOCKED = {"posting", "published", "unknown", "editing", "native_reserved", "native_private", "review"}


def now_ts():
    return datetime.now(timezone.utc).timestamp()


def article_display_title(post, fallback='無題'):
    return ('📃 ' if post.get('native_ready') else '')+(post.get('title') or fallback)


def posting_order_key(post, queue_schedule):
    planned = None
    if post['status'] in {'queued','scheduled','native_reserved','posting'}:
        planned = post.get('due') or queue_schedule.get(post['id'])
        if not planned and post['status']=='posting':
            planned = post.get('updated')
    return (planned if planned is not None else math.inf,post['position'],post['created'],post['id'])


def article_timestamp(post, catalog):
    restored = post.get('draft_restored_at', 0) if post['status'] == 'draft' else 0
    value = catalog.get('remote_updated')
    if value:
        try:
            number = float(value)
            if math.isfinite(number):
                return max(restored, number / 1000 if number > 100_000_000_000 else number)
        except (ValueError, TypeError):
            try:
                parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                return max(restored, parsed.replace(tzinfo=parsed.tzinfo or JST).timestamp())
            except (ValueError, TypeError, OverflowError):
                pass
    return restored or post['created']


def tags_from_text(text):
    tags = []
    for word in re.split(r"[\s,，、]+", text.strip()):
        word = word.lstrip("#＃")
        if not word:
            continue
        if "#" in word or "＃" in word or len(word) > 100:
            raise ValueError("ハッシュタグは区切って入力してください（1つ100文字以内）。")
        if word not in tags:
            tags.append(word)
    return tags


def normalize_body(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


class Store(RemoteStore):
    def __init__(self, root):
        self.setting_errors = []
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "note-stock.sqlite3"
        if self.db.exists() and not (self.root/'before-discord-v2.sqlite3').exists():
            with sqlite3.connect(self.db) as source:
                if source.execute('PRAGMA user_version').fetchone()[0]<2:
                    with sqlite3.connect(self.root/'before-discord-v2.sqlite3') as target:
                        source.backup(target)
        with self.connect() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS posts(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '[]', image TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft', due REAL, position INTEGER NOT NULL,
                    draft_key TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '', submitted INTEGER NOT NULL DEFAULT 0,
                    updated REAL NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS slots(key TEXT PRIMARY KEY, post_id TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS recurring_overrides(day TEXT PRIMARY KEY, post_id TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS article_previews(post_id TEXT PRIMARY KEY, body TEXT NOT NULL, html TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS article_preview_delete AFTER DELETE ON posts
                    BEGIN DELETE FROM article_previews WHERE post_id=OLD.id; END;
                CREATE TRIGGER IF NOT EXISTS recurring_override_delete AFTER DELETE ON posts
                    BEGIN DELETE FROM recurring_overrides WHERE post_id=OLD.id; END;
                CREATE TRIGGER IF NOT EXISTS recurring_override_remove AFTER UPDATE OF status ON posts
                    WHEN NEW.status NOT IN ('queued','editing')
                    BEGIN DELETE FROM recurring_overrides WHERE post_id=NEW.id; END;
                CREATE TABLE IF NOT EXISTS history(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT NOT NULL,
                    at REAL NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL);
            """)
            columns={row['name'] for row in c.execute('PRAGMA table_info(posts)')}
            additions={'editor_kind':"TEXT NOT NULL DEFAULT 'legacy'",'native_ready':"INTEGER NOT NULL DEFAULT 0",
                       'native_html':"TEXT NOT NULL DEFAULT ''",'native_document':"TEXT NOT NULL DEFAULT '{}'",
                       'native_fingerprint':"TEXT NOT NULL DEFAULT ''",'native_payload':"TEXT NOT NULL DEFAULT ''",
                       'native_owner':"TEXT NOT NULL DEFAULT ''",'native_summary':"TEXT NOT NULL DEFAULT '{}'",
                       'draft_restored_at':"REAL NOT NULL DEFAULT 0"}
            for name,definition in additions.items():
                if name not in columns:
                    c.execute(f'ALTER TABLE posts ADD COLUMN {name} {definition}')
            c.execute("""CREATE TRIGGER IF NOT EXISTS article_preview_invalidate
                AFTER UPDATE OF body,native_html,draft_key ON posts
                WHEN OLD.body IS NOT NEW.body OR OLD.native_html IS NOT NEW.native_html
                     OR OLD.draft_key IS NOT NEW.draft_key
                BEGIN DELETE FROM article_previews WHERE post_id=NEW.id; END""")
        self.init_remote()
        with self.connect() as c:
            c.execute('PRAGMA user_version=2')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def setting(self, key, default=None):
        with self.connect() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            value = json.loads(row[0])
            if default is not None and type(value) is not type(default):
                raise ValueError('設定の型が正しくありません。')
            if key == 'recurrence' and (not isinstance(value,dict) or
                    not value.get('days') or any(type(d) is not int or d not in range(7) for d in value['days']) or
                    not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value.get('time',''))):
                raise ValueError('定期投稿の設定が正しくありません。')
            if key == 'article_sync_minutes' and (type(value) is not int or
                    not MIN_ARTICLE_SYNC_MINUTES <= value <= MAX_ARTICLE_SYNC_MINUTES):
                raise ValueError('自動同期の間隔が正しくありません。')
            if key in {'window_size','splitter_sizes','management_splitter_sizes'} and (not isinstance(value,list) or len(value)!=2 or any(type(n) is not int or not 0<n<20000 for n in value)):
                raise ValueError('画面サイズの設定が正しくありません。')
            if key == 'favorite_tags' and (not isinstance(value,list) or any(not isinstance(t,str) for t in value)):
                raise ValueError('保存済みタグの設定が正しくありません。')
            if key == 'browser' and value not in {'auto','chrome','edge','chromium'}:
                raise ValueError('ブラウザーの設定が正しくありません。')
            if key == 'ui_state' and (not isinstance(value,dict) or not isinstance(value.get('search',''),str) or
                    value.get('filter','') not in {'',*STATUSES} or value.get('tab',0) not in (0,1,2) or type(value.get('scroll',0)) is not int or value.get('scroll',0)<0 or
                    (value.get('selected') is not None and not isinstance(value.get('selected'),str))):
                raise ValueError('前回の選択状態を復元できません。')
            return value
        except (ValueError, TypeError, KeyError):
            message = key + ' の設定を読み込めません。保存済みの値を保全し、初期表示で起動しました。'
            if message not in self.setting_errors:
                self.setting_errors.append(message)
            return default

    def set_setting(self, key, value):
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))
        if key == 'diagnostics' and hasattr(self, 'diagnostic_session'):
            self.diagnostic_session.configure(value is True)

    def settings_batch(self, values):
        results = {}
        for key, value in values.items():
            try:
                self.set_setting(key, value)
                results[key] = ""
            except Exception as exc:
                results[key] = str(exc)
        return results

    def set_note_connection_setting(self,key,value,default):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
            if value==(json.loads(row[0]) if row else default):
                return
            c.executemany("INSERT OR REPLACE INTO settings VALUES(?,?)",
                          [(key,json.dumps(value,ensure_ascii=False)),('account_verified','false'),('paused','true')])

    @staticmethod
    def decode(row):
        if row is None:
            raise ValueError("原稿が見つかりません。")
        item = dict(row)
        item["tags"] = json.loads(item["tags"])
        item.pop('native_payload',None)
        item['native_summary']=json.loads(item.get('native_summary','{}'))
        return item

    def get(self, post_id):
        with self.connect() as c:
            return self.decode(c.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone())

    def posts(self, *, with_catalog=False):
        if with_catalog:
            account = self.setting('account', '')
            with self.connect() as c:
                return [self.decode(row) for row in c.execute("""
                    SELECT p.*, c.remote_status, c.remote_updated
                    FROM posts p LEFT JOIN catalog c
                    ON c.account=? AND c.note_key=p.draft_key AND c.post_id=p.id
                    ORDER BY p.position,p.created
                """, (account,))]
        with self.connect() as c:
            return [self.decode(row) for row in c.execute("SELECT * FROM posts ORDER BY position,created")]

    def create(self, source=None):
        post_id, stamp = uuid.uuid4().hex, now_ts()
        source = source or {}
        with self.connect() as c:
            c.execute("""INSERT INTO posts(id,title,body,tags,image,position,created,updated)
                VALUES(?,?,?,?,?,(SELECT COALESCE(MAX(position),0)+1 FROM posts),?,?)""",
                (post_id, source.get("title", ""), source.get("body", ""),
                 json.dumps(source.get("tags", []), ensure_ascii=False), source.get("image", ""), stamp, stamp))
            document=source.get('native_document','{}')
            if not isinstance(document,str):
                document=json.dumps(document,ensure_ascii=False)
            c.execute('UPDATE posts SET editor_kind=?,native_html=?,native_document=? WHERE id=?',
                      (source.get('editor_kind','native'),source.get('native_html',''),document,post_id))
        return post_id

    def import_image(self, source):
        from PySide6.QtGui import QImageReader
        source = Path(source)
        reader = QImageReader(str(source))
        if not reader.canRead() or reader.read().isNull():
            raise ValueError("読み込める画像を選んでください。")
        folder = self.root / "images"
        folder.mkdir(exist_ok=True)
        dest = folder / (uuid.uuid4().hex + source.suffix.lower())
        shutil.copy2(source, dest)
        return str(dest.relative_to(self.root))

    def image_path(self, relative):
        if not relative:
            return None
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root / "images"):
            raise ValueError("管理フォルダー外の画像は使用できません。")
        if not target.is_file():
            raise ValueError("見出し画像が見つかりません。画像を選び直してください。")
        return target

    def save(self, post_id, title, body, tags, image, status, due=None, stamp=None, *, append_to_queue=False, interrupt_day=None):
        stamp = now_ts() if stamp is None else stamp
        native_title,native_body=title,body
        if status not in {"draft", "queued", "scheduled"}:
            raise ValueError("保存方法が正しくありません。")
        title, body = title.strip(), normalize_body(body)
        if status == "scheduled" and (due is None or due <= stamp):
            raise ValueError("投稿日時は現在より先に設定してください。")
        self.image_path(image)
        tags = tags_from_text(" ".join(tags))
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = self.decode(c.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone())
            if status in {'scheduled','queued'}:
                self.require_schedulable(post_id,connection=c)
            if old["status"] in LOCKED:
                raise ValueError("投稿中・投稿済み・結果未確認の原稿は編集できません。複製、または公開結果の確認を行ってください。")
            if status!='draft':
                if old['editor_kind']=='native' and not old['native_ready']:
                    raise ValueError('noteで編集し、公開設定をアプリへ取り込んでから予約してください。')
                if old['editor_kind']=='legacy' and (not title or not body.strip()):
                    raise ValueError('投稿するにはタイトルと本文が必要です。')
            if old['native_ready']:
                if native_title!=old['title'] or native_body!=old['body'] or image!=old['image']:
                    raise ValueError('本文・装飾・素材は「noteで編集」から変更してください。')
                title,body=old['title'],old['body']
                if tags!=old['tags']:
                    from .secrets import protect
                    native=self.native_template(post_id)
                    native['payload']['hashtags']=['#'+tag for tag in tags]
                    c.execute('UPDATE posts SET native_payload=? WHERE id=?',(protect(native),post_id))
            c.execute("""UPDATE posts SET title=?,body=?,tags=?,image=?,status=?,due=?,error='',updated=? WHERE id=?""",
                      (title, body, json.dumps(tags, ensure_ascii=False), image, status,
                       due if status == "scheduled" else None, stamp, post_id))
            if append_to_queue and status=='queued':
                c.execute("UPDATE posts SET position=(SELECT COALESCE(MAX(position),0)+1 FROM posts) WHERE id=?",
                          (post_id,))
            if interrupt_day and status=='queued':
                self._set_recurring_override(c,post_id,interrupt_day,stamp)
        from .diagnostics import Diagnostics
        return Diagnostics(self).record("draft_saved", post_id=post_id, status=status, database=str(self.db), table="posts")

    def delete(self, post_id):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            old = self.decode(c.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone())
            if old["status"] in {"posting", "unknown", "editing"}:
                raise ValueError("投稿中・結果未確認の原稿は削除できません。")
            c.execute("DELETE FROM posts WHERE id=?", (post_id,))

    def reorder(self, post_id, direction):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute("SELECT id,position FROM posts ORDER BY position,created").fetchall()
            ids = [row["id"] for row in rows]
            index = ids.index(post_id)
            other = index + direction
            if 0 <= other < len(ids):
                ids[index], ids[other] = ids[other], ids[index]
                c.executemany("UPDATE posts SET position=? WHERE id=?", enumerate(ids, 1))

    def event(self, c, post_id, status, message, stamp):
        c.execute("INSERT INTO history(post_id,at,status,message) VALUES(?,?,?,?)", (post_id, stamp, status, message))

    def recover(self, stamp=None, *, start_automatic_posting=False):
        stamp = now_ts() if stamp is None else stamp
        changed = []
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            for row in c.execute("SELECT id FROM posts WHERE status='posting'").fetchall():
                message = "前回の投稿処理が途中で終了しました。公開結果を確認してください。"
                c.execute("UPDATE posts SET status='unknown',error=?,updated=? WHERE id=?", (message, stamp, row["id"]))
                self.event(c, row["id"], "unknown", message, stamp)
                changed.append(row["id"])
            for row in c.execute("SELECT id FROM posts WHERE status='editing'").fetchall():
                message='前回の編集が取り込み前に終了しました。noteの下書きから編集を再開してください。'
                c.execute("UPDATE posts SET status='draft',native_ready=0,error=?,updated=? WHERE id=?",(message,stamp,row['id']))
                changed.append(row['id'])
            changed.extend(self._expire(c, stamp))
            if start_automatic_posting:
                verified=c.execute("SELECT value FROM settings WHERE key='account_verified'").fetchone()
                paused=not (verified and json.loads(verified['value']) is True)
                c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('paused',?)",(json.dumps(paused),))
        return changed

    def _expire(self, c, stamp):
        rows = c.execute("SELECT id FROM posts WHERE status='scheduled' AND due<=?", (stamp,)).fetchall()
        for row in rows:
            message = "停止中または休止中に投稿時刻を過ぎました。再予約してください。"
            c.execute("UPDATE posts SET status='overdue',error=?,updated=? WHERE id=?", (message, stamp, row["id"]))
            self.event(c, row["id"], "overdue", message, stamp)
        return [row["id"] for row in rows]

    def expire(self, stamp):
        with self.connect() as c:
            return self._expire(c, stamp)

    def recurring_override(self, post_id):
        with self.connect() as c:
            row = c.execute('SELECT day FROM recurring_overrides WHERE post_id=?',(post_id,)).fetchone()
        return row['day'] if row else None

    def article_preview(self,post_id):
        with self.connect() as c:
            row=c.execute('SELECT body,html FROM article_previews WHERE post_id=?',(post_id,)).fetchone()
        return dict(row) if row else None

    def save_article_preview(self,post_id,draft_key,body,html):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT draft_key FROM posts WHERE id=?',(post_id,)).fetchone()
            if not row or row['draft_key']!=draft_key:
                raise ValueError('取得中に記事が変更されました。もう一度選び直してください。')
            c.execute('INSERT OR REPLACE INTO article_previews VALUES(?,?,?)',(post_id,body,html))

    def set_recurring_override(self, post_id, day, stamp=None):
        stamp = now_ts() if stamp is None else stamp
        if day is None:
            with self.connect() as c:
                c.execute('DELETE FROM recurring_overrides WHERE post_id=?',(post_id,))
            return
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            self._set_recurring_override(c,post_id,day,stamp)

    def _set_recurring_override(self,c,post_id,day,stamp):
        day = date.fromisoformat(day)
        recurrence = self.setting('recurrence',{'days':list(range(7)), 'time':'09:00'})
        when = datetime.combine(day,datetime.strptime(recurrence['time'],'%H:%M').time(),tzinfo=JST)
        if day.weekday() not in recurrence['days'] or when.timestamp() <= stamp:
            raise ValueError('まだ実行されていない定期投稿の日を選んでください。')
        self.require_schedulable(post_id,connection=c)
        row=c.execute('SELECT status,editor_kind,native_ready,draft_key FROM posts WHERE id=?',(post_id,)).fetchone()
        if row['status']!='queued':
            raise ValueError('先にこの記事を定期投稿のキューに入れてください。')
        if row['editor_kind']=='native' and not (row['native_ready'] and row['draft_key']):
            raise ValueError('noteの本文と公開条件を確認してから差し込んでください。')
        if c.execute('SELECT 1 FROM slots WHERE key=?',(when.isoformat(),)).fetchone():
            raise ValueError('この日の定期投稿枠はすでに使われています。')
        previous=c.execute('SELECT day FROM recurring_overrides WHERE post_id=?',(post_id,)).fetchone()
        if previous and previous['day']==day.isoformat():
            return
        c.execute('DELETE FROM recurring_overrides WHERE post_id=?',(post_id,))
        plan=self.queue_schedule(stamp,connection=c,exclude=post_id)
        following=[(datetime.fromtimestamp(scheduled,JST).date()+timedelta(days=1),pid)
                   for pid,scheduled in plan.items() if datetime.fromtimestamp(scheduled,JST).date()>=day]
        for _,pid in following:
            c.execute('DELETE FROM recurring_overrides WHERE post_id=?',(pid,))
        for shifted,pid in following:
            c.execute('INSERT INTO recurring_overrides VALUES(?,?)',(shifted.isoformat(),pid))
        c.execute('INSERT INTO recurring_overrides VALUES(?,?)',(day.isoformat(),post_id))

    def queue_schedule(self,stamp=None,*,connection=None,exclude=''):
        stamp=now_ts() if stamp is None else stamp
        if connection is None:
            with self.connect() as c:
                return self.queue_schedule(stamp,connection=c,exclude=exclude)
        c=connection
        recurrence=self.setting('recurrence',{'days':list(range(7)), 'time':'09:00'})
        time=datetime.strptime(recurrence['time'],'%H:%M').time().replace(tzinfo=JST)
        queued=c.execute("SELECT id FROM posts WHERE status='queued' AND id<>? ORDER BY position,created",
                         (exclude,)).fetchall()
        queued_ids={row['id'] for row in queued}
        today=datetime.fromtimestamp(stamp,JST).date()
        fixed={date.fromisoformat(row['day']):row['post_id'] for row in c.execute(
            'SELECT day,post_id FROM recurring_overrides WHERE day>=?',(today.isoformat(),))
            if row['post_id'] in queued_ids}
        fixed_ids=set(fixed.values())
        normal=iter(row['id'] for row in queued if row['id'] not in fixed_ids)
        next_normal=next(normal,None)
        cursor=today
        plan={}
        while next_normal is not None or fixed:
            if next_normal is None:
                cursor=min(fixed)
            moment=datetime.combine(cursor,time)
            if moment.timestamp()>stamp:
                chosen=fixed.pop(cursor,None)
                if chosen is None and cursor.weekday() in recurrence['days']:
                    chosen=next_normal
                    next_normal=next(normal,None)
                if chosen:
                    plan[chosen]=moment.timestamp()
            else:
                expired=fixed.pop(cursor,None)
                if expired:
                    plan[expired]=moment.timestamp()
            if next_normal is None and not fixed:
                break
            cursor+=timedelta(days=1)
        return plan

    def set_queue_order(self,post_ids,stamp=None):
        stamp=now_ts() if stamp is None else stamp
        if len(post_ids)!=len(set(post_ids)):
            raise ValueError('同じ記事を重複して移動できません。')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            plan=self.queue_schedule(stamp,connection=c)
            if not set(post_ids).issubset(plan):
                raise ValueError('キューの状態が変わりました。一覧を更新して移動し直してください。')
            current=sorted(plan,key=plan.get)
            moving=set(post_ids)
            replacement=iter(post_ids)
            ordered=[next(replacement) if pid in moving else pid for pid in current]
            if current==ordered:
                return
            positions=sorted(c.execute('SELECT position FROM posts WHERE id=?',(pid,)).fetchone()['position']
                             for pid in current)
            dates=sorted(plan.values())
            if any(moment<=stamp for moment in dates):
                raise ValueError('期限を過ぎた割り込み日を変更してから並べ替えてください。')
            for pid in current:
                c.execute('DELETE FROM recurring_overrides WHERE post_id=?',(pid,))
            for pid,position,moment in zip(ordered,positions,dates):
                c.execute("UPDATE posts SET position=?,updated=? WHERE id=? AND status='queued'",
                          (position,stamp,pid))
                c.execute('INSERT INTO recurring_overrides(day,post_id) VALUES(?,?)',
                          (datetime.fromtimestamp(moment,JST).date().isoformat(),pid))

    def claim(self, stamp, post_id=None, slot=None):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM posts WHERE status='posting'").fetchone():
                return None
            if post_id:
                row = c.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
                if row is None or row["status"] in LOCKED:
                    raise ValueError("この原稿は投稿できません。公開結果を確認してください。")
            else:
                row = c.execute("SELECT * FROM posts WHERE status='scheduled' AND due<=? ORDER BY due,position LIMIT 1", (stamp,)).fetchone()
                if row is None and slot:
                    if c.execute("SELECT 1 FROM slots WHERE key=?", (slot,)).fetchone():
                        return None
                    day=slot.split('T',1)[0]
                    row=c.execute("""SELECT p.* FROM recurring_overrides o JOIN posts p ON p.id=o.post_id
                                       WHERE o.day=? AND p.status='queued'""",(day,)).fetchone()
                    if row is None:
                        row=c.execute("""SELECT p.* FROM posts p WHERE p.status='queued'
                                      AND NOT EXISTS(SELECT 1 FROM recurring_overrides o WHERE o.post_id=p.id)
                                      ORDER BY p.position,p.created LIMIT 1""").fetchone()
                    c.execute("INSERT INTO slots VALUES(?,?,?)", (slot, row["id"] if row else None, stamp))
                if row is None:
                    return None
            item = self.decode(row)
            if item['status'] in {'scheduled','queued'}:
                self.require_schedulable(item['id'],connection=c)
            if item['editor_kind']=='native' and not item['native_ready']:
                raise ValueError('noteで編集し、公開設定をアプリへ取り込んでください。')
            if item['editor_kind']=='legacy' and (not item["title"] or not item["body"].strip()):
                raise ValueError("タイトルと本文を入力してください。")
            self.image_path(item["image"])
            c.execute("UPDATE posts SET status='posting',error='',updated=? WHERE id=?", (stamp, item["id"]))
            self.event(c, item["id"], "posting", "投稿を開始しました。", stamp)
            item["status"] = "posting"
            return item

    def has_due_post(self,stamp,slot=None):
        with self.connect() as c:
            if c.execute("SELECT 1 FROM posts WHERE status='scheduled' AND due<=? LIMIT 1",(stamp,)).fetchone():
                return True
            if not slot or c.execute("SELECT 1 FROM slots WHERE key=?",(slot,)).fetchone():
                return False
            day=slot.split('T',1)[0]
            return bool(c.execute("""SELECT 1 FROM posts p LEFT JOIN recurring_overrides o ON o.post_id=p.id
                WHERE p.status='queued' AND (o.post_id IS NULL OR o.day=?) LIMIT 1""",(day,)).fetchone())

    def checkpoint(self, post_id, key=None, submitted=False):
        with self.connect() as c:
            if key:
                c.execute("UPDATE posts SET draft_key=?,updated=? WHERE id=?", (key, now_ts(), post_id))
            if submitted:
                c.execute("UPDATE posts SET submitted=1,updated=? WHERE id=?", (now_ts(), post_id))

    def finish(self, post_id, status, message="", url=""):
        if status not in {"published", "failed", "unknown", "held", "native_reserved", "native_private"}:
            raise ValueError("処理結果が正しくありません。")
        with self.connect() as c:
            stamp = now_ts()
            c.execute("UPDATE posts SET status=?,error=?,url=CASE WHEN ?<>'' THEN ? ELSE url END,updated=? WHERE id=?",
                      (status, message, url, url, stamp, post_id))
            self.event(c, post_id, status, message or url, stamp)
            if status in {'published','native_reserved','native_private'}:
                row=c.execute('SELECT draft_key,native_owner FROM posts WHERE id=?',(post_id,)).fetchone()
                if row and row['draft_key']:
                    remote={'published':'published','native_reserved':'reserved','native_private':'private'}[status]
                    c.execute('INSERT OR REPLACE INTO catalog VALUES(?,?,?,?,?,?)',
                              (row['native_owner'],row['draft_key'],post_id,remote,'',stamp))

    def begin_native_edit(self, post_id):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            post=self.decode(c.execute('SELECT * FROM posts WHERE id=?',(post_id,)).fetchone())
            if post['status'] in {'posting','unknown','editing','native_reserved'}:
                raise ValueError('処理中または結果未確認です。先に公開結果を確認してください。')
            c.execute("UPDATE posts SET status='editing',native_ready=0,editor_kind='native',error='',submitted=0,updated=? WHERE id=?",(now_ts(),post_id))
        editing=self.get(post_id)
        editing['_previous_status']=post['status']
        editing['_previous_due']=post['due']
        return editing

    def link_note_drafts(self, items):
        results=[]
        for item in items:
            try:
                if item.get('status')!='draft' or not re.fullmatch(r'n[a-z0-9]{12}',item.get('key','')):
                    raise ValueError('下書きとして確認できない記事は取り込めません。')
                with self.connect() as c:
                    existing=c.execute('SELECT id FROM posts WHERE draft_key=?',(item['key'],)).fetchone()
                if existing:
                    pid=existing['id']
                    new=False
                else:
                    pid=self.create({'title':item['title'],'editor_kind':'native'})
                    self.checkpoint(pid,key=item['key'])
                    with self.connect() as c:
                        c.execute('UPDATE posts SET native_owner=? WHERE id=?',(item.get('owner') or self.setting('account',''),pid))
                    new=True
                results.append({'ok':True,'id':pid,'key':item['key'],'new':new,'title':item['title']})
            except Exception as exc:
                results.append({'ok':False,'key':item.get('key',''),'error':str(exc)})
        return results

    def capture_native(self, post_id, template, snapshot, edit_state=None):
        from .secrets import protect
        previous=edit_state or {}
        automatic_import=previous.get('_catalog_import',False)
        if automatic_import and previous['tags']:
            template={**template,'payload':{**template['payload'],'hashtags':['#'+tag for tag in previous['tags']]}}
        protected=protect(template)
        payload=template['payload']
        tags=tags_from_text(' '.join(payload.get('hashtags',[])))
        owner=payload.get('owner_urlname') or template['account']
        html=(payload.get('free_body') or '')+(payload.get('pay_body') or '')
        summary={'price':payload.get('price',0),'status':payload.get('status','published'),
                 'limited':bool(payload.get('limited') or payload.get('circle_permissions')),
                 'magazines':len(payload.get('magazine_ids',[]))+len(payload.get('magazine_keys',[])),
                 'publish_at':payload.get('publish_at'),'notifications':payload.get('send_notifications_flag',True)}
        stamp=now_ts()
        status=previous.get('_previous_status','draft')
        due=previous.get('_previous_due') if status in {'scheduled','review'} else None
        message=''
        if status=='scheduled' and due is not None:
            if due<=stamp:
                status='overdue'
                message='編集中に投稿時刻を過ぎました。再予約してください。'
        elif status not in {'queued','review','published','native_private'}:
            status,due='draft',None
        with self.connect() as c:
            if automatic_import:
                c.execute('BEGIN IMMEDIATE')
                current=c.execute('SELECT updated,draft_key FROM posts WHERE id=?',(post_id,)).fetchone()
                if current is None or current['updated']!=previous['updated'] or current['draft_key']!=previous['draft_key']:
                    raise ValueError('取り込み中に記事の設定が変更されました。変更内容を保持しました。')
            c.execute("""UPDATE posts SET editor_kind='native',native_ready=1,native_html=?,native_document=?,native_fingerprint=?,
                  native_payload=?,native_owner=?,native_summary=?,title=?,body=?,tags=?,image='',status=?,due=?,error=?,updated=? WHERE id=?""",
                (html,json.dumps(snapshot['document'],ensure_ascii=False),snapshot['fingerprint'],protected,owner,json.dumps(summary,ensure_ascii=False),
                   payload.get('name') or snapshot['title'],snapshot['text'],json.dumps(tags,ensure_ascii=False),status,due,message,stamp,post_id))
            self.event(c,post_id,status,message or 'noteの本文・全素材・公開設定を取り込みました。投稿予定を保持し、まだ公開していません。',stamp)

    def native_template(self, post_id):
        from .secrets import unprotect
        with self.connect() as c:
            row=c.execute('SELECT native_payload FROM posts WHERE id=?',(post_id,)).fetchone()
        if not row or not row[0]:
            raise ValueError('noteの公開設定がまだ取り込まれていません。')
        try:
            return unprotect(row[0])
        except OSError as exc:
            raise ValueError('このWindowsユーザーでは公開設定を復号できません。noteで編集して取り込み直してください。') from exc

    def native_edit_failed(self, post_id, message):
        with self.connect() as c:
            c.execute("UPDATE posts SET status='draft',native_ready=0,error=?,updated=? WHERE id=?",(message,now_ts(),post_id))
            self.event(c,post_id,'draft',message,now_ts())

    def restore_interrupted_publish_check(self,post):
        with self.connect() as c:
            c.execute("""UPDATE posts SET status=?,due=?,native_ready=?,editor_kind=?,error=?,submitted=?,updated=?
                WHERE id=? AND status='editing' AND draft_key=?""",
                (post['status'],post['due'],post['native_ready'],post['editor_kind'],post['error'],
                 post['submitted'],now_ts(),post['id'],post['draft_key']))

    def history(self, post_id):
        with self.connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM history WHERE post_id=? ORDER BY id DESC", (post_id,))]


class Scheduler:
    def __init__(self, store, stamp=None):
        self.store = store
        self.last = now_ts() if stamp is None else stamp
        self.pending_slot = None

    def tick(self, stamp=None, busy=False):
        stamp = now_ts() if stamp is None else stamp
        before, self.last = self.last, stamp
        if stamp < before or stamp - before > 90:
            self.store.expire(stamp)
            self.pending_slot = None
            return None
        if self.store.setting("paused", True):
            self.store.expire(stamp)
            self.pending_slot = None
            return None
        if not self.store.setting("account", ""):
            return None
        local = datetime.fromtimestamp(stamp, JST)
        minute = local.replace(second=0, microsecond=0)
        schedule = self.store.setting("recurrence", {"days": list(range(7)), "time": "09:00"})
        if local.strftime("%H:%M") == schedule["time"] and before < minute.timestamp() <= stamp:
            with self.store.connect() as c:
                override=c.execute("SELECT 1 FROM recurring_overrides o JOIN posts p ON p.id=o.post_id "
                                   "WHERE o.day=? AND p.status='queued'",(local.date().isoformat(),)).fetchone()
            if local.weekday() in schedule["days"] or override:
                self.pending_slot = minute.isoformat()
        if busy:
            return None
        item = self.store.claim(stamp, slot=self.pending_slot)
        if self.pending_slot:
            with self.store.connect() as c:
                if c.execute("SELECT 1 FROM slots WHERE key=?", (self.pending_slot,)).fetchone():
                    self.pending_slot = None
        return item
