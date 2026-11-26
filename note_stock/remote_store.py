"""Durable remote requests and note-owned catalog. No network or UI dependency."""
import json
import uuid
from .store_time import timestamp


class ObsoleteUI(ValueError):
    """A consumed/superseded screen is not a user-visible operation result."""


class RemoteStore:
    def init_remote(self):
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, action TEXT NOT NULL,
                    post_id TEXT, data TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'waiting',
                    result TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS notifications(
                    id TEXT PRIMARY KEY, body TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS queue_batches(
                    id TEXT PRIMARY KEY, sealed INTEGER NOT NULL DEFAULT 0,
                    errors TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS catalog(
                    account TEXT NOT NULL, note_key TEXT NOT NULL, post_id TEXT NOT NULL,
                    remote_status TEXT NOT NULL, remote_updated TEXT NOT NULL DEFAULT '',
                    seen REAL NOT NULL, PRIMARY KEY(account,note_key));
                CREATE TABLE IF NOT EXISTS catalog_imports(
                    post_id TEXT PRIMARY KEY, attempted_seen REAL NOT NULL, imported_revision TEXT);
                CREATE TABLE IF NOT EXISTS incoming_drafts(request_key TEXT PRIMARY KEY,post_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notification_messages(
                    notification_id TEXT PRIMARY KEY,result_id TEXT NOT NULL DEFAULT '',menu_id TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS job_receipts(
                    job_id TEXT PRIMARY KEY,message_id TEXT NOT NULL,channel_id TEXT NOT NULL,
                    application_id TEXT NOT NULL DEFAULT '',webhook_token TEXT NOT NULL DEFAULT '',
                    removed INTEGER NOT NULL DEFAULT 0);
            ''')

    def incoming_draft(self,key,source):
        """Reserve one buffer per Discord interaction before any browser work."""
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT post_id FROM incoming_drafts WHERE request_key=?',(str(key),)).fetchone()
            if old:
                return old[0]
            pid=uuid.uuid4().hex
            stamp=timestamp()
            c.execute('''INSERT INTO posts(id,title,body,tags,image,position,created,updated,editor_kind)
                       VALUES(?,?,?,?,?,(SELECT COALESCE(MAX(position),0)+1 FROM posts),?,?,'native')''',
                      (pid,source['title'],source['body'],json.dumps(source.get('tags',[]),ensure_ascii=False),source.get('image',''),stamp,stamp))
            if 'tags' in source:
                self._write_tags(c,pid,source['tags'])
            c.execute('INSERT INTO incoming_drafts VALUES(?,?)',(str(key),pid))
        return pid

    def enqueue(self, action, post_id=None, data=None, request_key=None,ui_session=None):
        key=request_key or uuid.uuid4().hex
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT id FROM jobs WHERE request_key=?',(key,)).fetchone()
            if row:
                return row['id']
            if (data or {}).get('draft_queue') or (data or {}).get('queue_only'):
                post=c.execute('SELECT status FROM posts WHERE id=?',(post_id,)).fetchone()
                expected='queued' if data.get('queue_only') else 'draft'
                if not post or post['status']!=expected:
                    raise ValueError('記事の状態が変わりました。一覧を更新してください。')
                self.require_schedulable(post_id,connection=c)
                pending=c.execute(
                    "SELECT id,state,data FROM jobs WHERE post_id=? AND action='save_note_draft' "
                    "AND state IN ('waiting','running') AND json_extract(data,'$.queue')=1",
                    (post_id,),
                ).fetchone()
                if pending:
                    details=json.loads(pending['data'])
                    if data.get('interrupt_day') and details.get('interrupt_day')!=data['interrupt_day']:
                        if pending['state']=='running':
                            raise ValueError('キュー追加が完了してから割り込み日を指定してください。')
                        original_batch=details.get('queue_batch')
                        details.update(data)
                        if original_batch:
                            details['queue_batch']=original_batch
                        c.execute('UPDATE jobs SET data=? WHERE id=?',
                                  (json.dumps(details,ensure_ascii=False),pending['id']))
                    return pending['id']
            if ui_session is not None:
                current=c.execute('SELECT value FROM settings WHERE key=?',('discord_active_ui',)).fetchone()
                if not current or json.loads(current['value'])!=ui_session:
                    raise ObsoleteUI()
                c.execute('UPDATE settings SET value=? WHERE key=?',
                          (json.dumps('used:'+uuid.uuid4().hex),'discord_active_ui'))
            jid=uuid.uuid4().hex
            stamp=timestamp()
            details=dict(data or {})
            if post_id:
                post=c.execute('SELECT title FROM posts WHERE id=?',(post_id,)).fetchone()
                if post: details['_history_title']=post['title']
            c.execute('INSERT INTO jobs(id,request_key,action,post_id,data,created,updated) VALUES(?,?,?,?,?,?,?)',
                      (jid,key,action,post_id,json.dumps(details,ensure_ascii=False),stamp,stamp))
            if (action=='save_note_draft' and details.get('queue')) or action in {'schedule','tags'}:
                self._apply_local_article_action(c,action,post_id,details,stamp)
                result={'ok':True,'message':('投稿時のハッシュタグを保存しました。' if action=='tags' else
                        '投稿日時を登録しました。' if action=='schedule' else 'キューへ追加しました。'),'url':''}
                c.execute("UPDATE jobs SET state='done',result=? WHERE id=?",(json.dumps(result,ensure_ascii=False),jid))
                if details.get('queue_batch'):
                    self._complete_queue_batch(c,details['queue_batch'])
                else:
                    c.execute('INSERT INTO notifications(id,body) VALUES(?,?)',('job:'+jid,json.dumps(result,ensure_ascii=False)))
        return jid

    def _apply_local_article_action(self,c,action,pid,data,stamp):
        row=c.execute('SELECT * FROM posts WHERE id=?',(pid,)).fetchone()
        if not row or row['status'] in {'posting','editing','unknown'}:
            raise ValueError('処理中または結果未確認です。')
        if action=='tags':
            self._write_tags(c,pid,data['tags'],data['tags_revision'])
            return
        self.require_schedulable(pid,connection=c)
        if row['editor_kind']=='native' and not row['draft_key']:
            raise ValueError('先に記事をnoteへ下書き保存してください。')
        if action=='schedule':
            import math
            due=data.get('due')
            if type(due) not in (int,float) or not math.isfinite(due) or due<=stamp:
                raise ValueError('未来の投稿日時を指定してください。')
            c.execute("UPDATE posts SET status='scheduled',due=?,error='',updated=? WHERE id=?",(data['due'],stamp,pid))
            return
        c.execute("UPDATE posts SET status='queued',due=NULL,error='',updated=? WHERE id=?",(stamp,pid))
        if data.get('draft_queue') and row['status']!='queued':
            c.execute('UPDATE posts SET position=(SELECT COALESCE(MAX(position),0)+1 FROM posts) WHERE id=?',(pid,))
        if data.get('interrupt_day'):
            self._set_recurring_override(c,pid,data['interrupt_day'],stamp)

    def operation_history(self,page=0,size=5):
        with self.connect() as c:
            condition="request_key NOT LIKE 'sync:%'"
            total=c.execute('SELECT COUNT(*) FROM jobs WHERE '+condition).fetchone()[0]
            page=max(0,min(page,max(0,(total-1)//size)))
            rows=c.execute('SELECT jobs.*,posts.title AS current_title FROM jobs LEFT JOIN posts ON posts.id=jobs.post_id WHERE '+condition+
                           ' ORDER BY jobs.created DESC,jobs.id DESC LIMIT ? OFFSET ?',(size,page*size)).fetchall()
        items=[]
        for row in rows:
            item=dict(row)
            item['data']=json.loads(item['data']); item['result']=json.loads(item['result'])
            item['subject']=item['data'].get('_history_title') or item['current_title'] or item['post_id'] or ''
            items.append(item)
        return {'total':total,'page':page,'items':items}

    def job(self, jid):
        with self.connect() as c:
            row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row:
            raise ValueError('処理が見つかりません。')
        result=dict(row)
        for key in ('data','result'):
            result[key]=json.loads(result[key])
        return result

    def preview_job_state(self,post_id):
        with self.connect() as c:
            row=c.execute("""SELECT state FROM jobs WHERE action='preview_article' AND post_id=?
                             AND state IN ('waiting','running') ORDER BY created LIMIT 1""",(post_id,)).fetchone()
        return row['state'] if row else None

    def next_job(self,browser_allowed=True,*,editing_locally=False):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM jobs WHERE state='running'").fetchone():
                return None
            allowed=("AND (action IN ('schedule','publish_note','tags') OR request_key LIKE 'publish-preflight:%' OR (action='refresh_article' AND json_extract(data,'$.selection_read')=1)) " if editing_locally else
                     "" if browser_allowed else "AND (action IN ('pause','resume','cancel_schedule','relogin_note',"
                     "'schedule','publish_note','tags') OR request_key LIKE 'publish-preflight:%') ")
            row=c.execute("SELECT id FROM jobs WHERE state='waiting' "+allowed+"ORDER BY created,rowid LIMIT 1").fetchone()
            if not row:
                return None
            c.execute("UPDATE jobs SET state='running',updated=? WHERE id=?",(timestamp(),row['id']))
        return self.job(row['id'])

    def complete_job(self,jid,ok,message,url='',notify=True):
        result={'ok':ok,'message':message,'url':url}
        with self.connect() as c:
            c.execute('UPDATE jobs SET state=?,result=?,updated=? WHERE id=?',
                      ('done' if ok else 'failed',json.dumps(result,ensure_ascii=False),timestamp(),jid))
            job=c.execute('SELECT data FROM jobs WHERE id=?',(jid,)).fetchone()
            batch=json.loads(job['data']).get('queue_batch') if job else None
            if batch:
                return self._complete_queue_batch(c,batch)
            local_only=notify and c.execute("""SELECT 1 FROM jobs WHERE id=? AND
                (request_key LIKE 'reservation-preflight:%' OR request_key LIKE 'publish-preflight:%'
                 OR action='preview_article')""",(jid,)).fetchone()
            if notify and not local_only:
                c.execute('INSERT OR IGNORE INTO notifications(id,body) VALUES(?,?)',
                          ('job:'+jid,json.dumps(result,ensure_ascii=False)))

    def begin_queue_batch(self):
        batch=uuid.uuid4().hex
        with self.connect() as c:
            c.execute('INSERT INTO queue_batches(id) VALUES(?)',(batch,))
        return batch

    def seal_queue_batch(self,batch,errors):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('UPDATE queue_batches SET sealed=1,errors=? WHERE id=?',
                      (json.dumps(errors,ensure_ascii=False),batch))
            return self._complete_queue_batch(c,batch)

    def _complete_queue_batch(self,c,batch):
        if c.execute('SELECT 1 FROM notifications WHERE id=?',('queue-batch:'+batch,)).fetchone():
            return None
        group=c.execute('SELECT * FROM queue_batches WHERE id=?',(batch,)).fetchone()
        if not group or not group['sealed']:
            return None
        jobs=c.execute("SELECT state,data,result FROM jobs WHERE json_extract(data,'$.queue_batch')=? "
                       "ORDER BY created,rowid",(batch,)).fetchall()
        if any(job['state'] in {'waiting','running'} for job in jobs):
            return None
        errors=json.loads(group['errors'])
        for job in jobs:
            if job['state']=='failed':
                details=json.loads(job['data'])
                result=json.loads(job['result'])
                errors.append(details.get('_history_title','記事')+': '+result.get('message','追加できませんでした。'))
        if not jobs and not errors:
            c.execute('DELETE FROM queue_batches WHERE id=?',(batch,))
            return None
        successful=sum(job['state']=='done' for job in jobs)
        message=f'キューへの一括追加が完了しました。\n成功 {successful}件 / 失敗 {len(errors)}件'
        if errors:
            message+='\n\n'+'\n'.join(errors)
        result={'ok':not errors,'message':message,'url':'','action':'queue_batch'}
        inserted=c.execute('INSERT OR IGNORE INTO notifications(id,body) VALUES(?,?)',
                           ('queue-batch:'+batch,json.dumps(result,ensure_ascii=False)))
        return result if inserted.rowcount else None

    def recover_jobs(self):
        with self.connect() as c:
            legacy=c.execute("""SELECT j.id,j.data FROM jobs j LEFT JOIN notifications n ON n.id='job:'||j.id
                WHERE j.action='save_note_draft' AND json_extract(j.data,'$.queue_batch') IS NULL
                AND (json_extract(j.data,'$.draft_queue')=1 OR json_extract(j.data,'$.queue_only')=1)
                AND (j.state IN ('waiting','running') OR n.delivered=0)""").fetchall()
            if legacy:
                batch=uuid.uuid4().hex
                c.execute('INSERT INTO queue_batches(id,sealed) VALUES(?,1)',(batch,))
                for job in legacy:
                    details=json.loads(job['data'])
                    details['queue_batch']=batch
                    c.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(details,ensure_ascii=False),job['id']))
                    c.execute('UPDATE notifications SET delivered=1 WHERE id=?',('job:'+job['id'],))
            for group in c.execute('SELECT id,errors FROM queue_batches WHERE sealed=0').fetchall():
                errors=json.loads(group['errors'])+['前回の一括受付が途中で中断されました。']
                c.execute('UPDATE queue_batches SET sealed=1,errors=? WHERE id=?',
                          (json.dumps(errors,ensure_ascii=False),group['id']))
            ids=[row[0] for row in c.execute("SELECT id FROM jobs WHERE state='running'")]
            stale=[row[0] for row in c.execute("""SELECT id FROM jobs WHERE state='waiting'
                AND (request_key LIKE 'reservation-preflight:%' OR request_key LIKE 'publish-preflight:%')""")]
        for jid in ids:
            self.complete_job(jid,False,'前回の処理が中断されました。note上の結果を確認してください。自動再送はしません。')
        for jid in stale:
            self.complete_job(jid,False,'前回の公開条件の確認は中断されました。',notify=False)
        with self.connect() as c:
            for group in c.execute('SELECT id FROM queue_batches WHERE sealed=1').fetchall():
                self._complete_queue_batch(c,group['id'])

    def cancel_publish_check(self,jid):
        result=json.dumps({'ok':False,'message':'公開条件の確認を中止しました。','url':''},ensure_ascii=False)
        with self.connect() as c:
            c.execute("""UPDATE jobs SET state='failed',result=?,updated=? WHERE id=?
                AND state='waiting' AND request_key LIKE 'publish-preflight:%'""",(result,timestamp(),jid))

    def expire_waiting_reservations(self,stamp=None):
        stamp=timestamp() if stamp is None else stamp
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=c.execute("""SELECT id,data FROM jobs WHERE action='schedule' AND state='waiting'
                AND (created<=? OR json_extract(data,'$.due')<=?)""",(stamp-120,stamp)).fetchall()
            for row in rows:
                due=json.loads(row['data']).get('due')
                message=('予約を処理する前に指定日時を過ぎました。日時を選び直してください。'
                         if type(due) in (int,float) and due<=stamp else
                         'PCが予約処理を開始できませんでした。PC側の編集中の画面や接続状態を確認してください。')
                result=json.dumps({'ok':False,'message':message,'url':''},ensure_ascii=False)
                c.execute("UPDATE jobs SET state='failed',result=?,updated=? WHERE id=?",(result,stamp,row['id']))
                c.execute('INSERT OR IGNORE INTO notifications(id,body) VALUES(?,?)',('job:'+row['id'],result))

    def pending_notifications(self):
        with self.connect() as c:
            return [(r['id'],json.loads(r['body'])) for r in c.execute('SELECT * FROM notifications WHERE delivered=0 ORDER BY rowid')]

    def delivered(self,nid):
        with self.connect() as c:
            c.execute('UPDATE notifications SET delivered=1 WHERE id=?',(nid,))

    def record_receipt(self,jid,message_id,channel_id,application_id='',webhook_token=''):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO job_receipts(job_id,message_id,channel_id,application_id,webhook_token) VALUES(?,?,?,?,?)',
                (jid,str(message_id),str(channel_id),str(application_id),webhook_token))

    def finished_receipts(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('''SELECT r.*,j.action FROM job_receipts r
                JOIN jobs j ON j.id=r.job_id
                JOIN notification_messages n ON n.notification_id='job:'||r.job_id
                WHERE r.removed=0 AND n.result_id!='' AND n.menu_id!='' ''')]

    def removed_receipt(self,jid):
        with self.connect() as c:
            c.execute("UPDATE job_receipts SET removed=1,webhook_token='' WHERE job_id=?",(jid,))

    def notification_message_ids(self,nid):
        with self.connect() as c:
            row=c.execute('SELECT result_id,menu_id FROM notification_messages WHERE notification_id=?',(nid,)).fetchone()
        return dict(row) if row else {'result_id':'','menu_id':''}

    def record_notification_message(self,nid,kind,message_id):
        if kind not in {'result_id','menu_id'}: raise ValueError('Unknown notification message kind')
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO notification_messages(notification_id) VALUES(?)',(nid,))
            c.execute('UPDATE notification_messages SET '+kind+'=? WHERE notification_id=?',(str(message_id),nid))

    def notification(self,ok,message,url='',key=None):
        nid=key or uuid.uuid4().hex
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO notifications(id,body) VALUES(?,?)',
                      (nid,json.dumps({'ok':ok,'message':message,'url':url},ensure_ascii=False)))
        return nid

    def merge_catalog(self,items,complete=False):
        account=self.setting('account','')
        stamp=timestamp()
        results=[]
        for item in items:
            try:
                import re
                if not re.fullmatch(r'n[a-z0-9]{12}',item.get('key','')):
                    raise ValueError('記事IDを確認できません。')
                if item.get('owner') and item['owner']!=account:
                    raise ValueError('別アカウントの記事は同期しません。')
                with self.connect() as c:
                    row=c.execute('SELECT post_id FROM catalog WHERE account=? AND note_key=?',(account,item['key'])).fetchone()
                    if not row:
                        row=c.execute('SELECT id AS post_id FROM posts WHERE draft_key=? AND (native_owner=? OR native_owner=?)',
                                      (item['key'],account,'')).fetchone()
                pid=row['post_id'] if row else self.create({'title':item['title'],'editor_kind':'native'})
                with self.connect() as c:
                    old=c.execute('SELECT * FROM posts WHERE id=?',(pid,)).fetchone()
                    before=c.execute('SELECT remote_updated FROM catalog WHERE account=? AND note_key=?',(account,item['key'])).fetchone()
                    status=old['status']
                    remote=item['status']
                    restored = remote == 'draft' and status in {'published', 'native_reserved', 'native_private'}
                    if status not in {'posting','editing','unknown'}:
                        if remote=='published':
                            status='published'
                        elif remote=='reserved':
                            status='native_reserved'
                        elif remote in {'private','frozen'}:
                            status='native_private'
                        if before and before[0] and before[0]!=str(item.get('updated','')) and status in {'scheduled','queued'}:
                            status='review'
                        if restored or (not row and remote=='draft'):
                            status='draft'
                    url='https://note.com/'+account+'/n/'+item['key'] if remote=='published' else old['url']
                    # Never overwrite an input buffer or cached body with a list summary.
                    changed=(old['draft_key']!=item['key'] or old['native_owner']!=account or status!=old['status'] or url!=old['url'] or
                             (before is not None and before[0]!=str(item.get('updated',''))))
                    c.execute('UPDATE posts SET draft_key=?,native_owner=?,status=?,url=?,updated=? WHERE id=?',
                              (item['key'],account,status,url,stamp if changed else old['updated'],pid))
                    if restored:
                        c.execute("""UPDATE posts SET url='', due=NULL, submitted=0, error='',
                                     native_ready=0, draft_restored_at=? WHERE id=?""", (stamp, pid))
                    if status=='published':
                        c.execute('UPDATE posts SET due=NULL WHERE id=?',(pid,))
                    if not old['native_ready'] and not old['body'] and old['status'] not in {'editing','posting'}:
                        c.execute('UPDATE posts SET title=? WHERE id=?',(item['title'],pid))
                    c.execute('INSERT OR REPLACE INTO catalog VALUES(?,?,?,?,?,?)',
                              (account,item['key'],pid,remote,str(item.get('updated','')),stamp))
                results.append({'ok':True,'id':pid})
            except Exception as exc:
                results.append({'ok':False,'key':item.get('key',''),'error':str(exc)})
        self.set_setting('catalog_sync',{'at':stamp,'complete':complete and all(r['ok'] for r in results),
                                        'count':len(items),'failures':[r for r in results if not r['ok']]})
        # Absence never implies deletion: filters/partial responses can omit records.
        return results

    def catalog_state(self,pid):
        with self.connect() as c:
            row=c.execute('SELECT * FROM catalog WHERE post_id=? AND account=?',(pid,self.setting('account',''))).fetchone()
        return dict(row) if row else {}

    def next_catalog_import(self,selected=None):
        account=self.setting('account','')
        if not account:
            return None
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute("""SELECT p.*,c.seen AS catalog_seen,c.remote_updated AS catalog_revision
                FROM catalog c JOIN posts p ON p.id=c.post_id AND p.draft_key=c.note_key
                LEFT JOIN catalog_imports i ON i.post_id=p.id
                WHERE c.account=? AND c.remote_status IN ('draft','published','private','frozen')
                AND p.status IN ('draft','published','native_private','queued','scheduled','review')
                AND (p.native_ready=0 OR (i.imported_revision IS NOT NULL AND i.imported_revision<>c.remote_updated))
                AND (i.attempted_seen IS NULL OR i.attempted_seen<>c.seen)
                ORDER BY CASE WHEN p.id=? THEN 0 ELSE 1 END,c.seen DESC,p.position LIMIT 1""",(account,selected)).fetchone()
            if row is None:
                return None
            c.execute("""INSERT INTO catalog_imports(post_id,attempted_seen) VALUES(?,?)
                ON CONFLICT(post_id) DO UPDATE SET attempted_seen=excluded.attempted_seen""",(row['id'],row['catalog_seen']))
        return self.decode(row)

    def finish_catalog_import(self,post):
        with self.connect() as c:
            c.execute("""UPDATE catalog_imports SET imported_revision=? WHERE post_id=? AND attempted_seen=?""",
                      (post['catalog_revision'],post['id'],post['catalog_seen']))

    def set_schedule(self,pid,due):
        import math
        if due is not None and (type(due) not in (int,float) or not math.isfinite(due) or due<=timestamp()):
            raise ValueError('未来の投稿日時を指定してください。')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM posts WHERE id=?',(pid,)).fetchone()
            if due is not None:
                self.require_schedulable(pid,connection=c)
            if not row or (due is not None and (not row['draft_key'] or not row['native_ready'])) or row['status'] in {'editing','posting','unknown','native_reserved'}:
                raise ValueError('noteの保存と公開設定を確認してから予約してください。')
            c.execute('UPDATE posts SET status=?,due=?,error=?,updated=? WHERE id=?',
                      ('scheduled' if due else ('published' if row['url'] else 'draft'),due,'',timestamp(),pid))

    def require_schedulable(self,pid,connection=None):
        if connection is None:
            with self.connect() as c:
                return self.require_schedulable(pid,connection=c)
        row=connection.execute('SELECT status,url FROM posts WHERE id=?',(pid,)).fetchone()
        published=connection.execute("SELECT 1 FROM catalog WHERE post_id=? AND remote_status='published'",(pid,)).fetchone()
        if row is None:
            raise ValueError('記事が見つかりません。')
        if row['status']=='published' or row['url'] or published:
            raise ValueError('投稿済みの記事は予約できません。更新する場合は「今すぐ投稿・更新」を使ってください。')

