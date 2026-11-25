"""Owner-authenticated reservation Activity; independent of remote desktop recovery."""
import asyncio
import hashlib
import json
import secrets
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
from aiohttp import web,ClientSession,ClientTimeout
from .store import JST,now_ts
from .secrets import unprotect

_services={}
_hosts={}

def service(store):
    key=str(store.root.resolve())
    if key not in _services:_services[key]=ScheduleActivity(store)
    return _services[key]

class ScheduleActivity:
    def __init__(self,store):
        self.store=store
        self.auth_lock=asyncio.Lock()
        self.attempts=[]
        self.probe_id=secrets.token_urlsafe(32)
        with store.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS activity_reservations(
                id TEXT PRIMARY KEY,instance TEXT UNIQUE,owner TEXT NOT NULL,post_id TEXT NOT NULL,
                revision REAL NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL,session_hash TEXT,
                job_id TEXT,due REAL,ui_session TEXT NOT NULL,last_seen REAL NOT NULL DEFAULT 0)''')
            if 'last_seen' not in {r[1] for r in c.execute('PRAGMA table_info(activity_reservations)')}:
                c.execute('ALTER TABLE activity_reservations ADD COLUMN last_seen REAL NOT NULL DEFAULT 0')

    def prepare(self,pid,owner,ui_session):
        config=self.store.setting('discord',{})
        if str(owner)!=str(config.get('owner')):raise ValueError('本人専用です。')
        self.store.require_schedulable(pid)
        post=self.store.get(pid)
        if not post['native_ready'] or post['status']=='review':raise ValueError('先に本文・公開条件を読み込んでください。')
        config=self.store.setting('remote_relogin',{})
        if not config.get('https_url') or not config.get('schedule_verified'):
            raise ValueError('PCの「Discord・セットアップ」で「予約画面の接続を設定」を実行してください。')
        lid=uuid.uuid4().hex
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE activity_reservations SET state='superseded',session_hash=NULL WHERE owner=? AND state IN ('opening','editing','cancelled')",(str(owner),))
            c.execute('INSERT INTO activity_reservations(id,owner,post_id,revision,expires,state,ui_session) VALUES(?,?,?,?,?,?,?)',
                      (lid,str(owner),pid,post['updated'],now_ts()+600,'opening',ui_session))
        return lid

    def bind(self,lid,instance):
        if not instance:raise ValueError('Discord内画面の起動結果を確認できません。')
        with self.store.connect() as c:
            changed=c.execute("UPDATE activity_reservations SET instance=?,state='editing' WHERE id=? AND state='opening' AND expires>?",(str(instance),lid,now_ts())).rowcount
            if not changed:raise ValueError('予約画面の起動が失効しました。')

    def row(self,instance):
        with self.store.connect() as c:
            row=c.execute('SELECT * FROM activity_reservations WHERE instance=?',(str(instance),)).fetchone()
        if not row or row['expires']<=now_ts() or row['state'] not in {'editing','submitted','cancelled'}:
            raise web.HTTPGone(text='予約画面の接続が失効しました。記事から開き直してください。')
        return dict(row)

    async def discord(self,path,token,*,method='GET',data=None):
        async with ClientSession(timeout=ClientTimeout(total=12)) as client:
            async with client.request(method,'https://discord.com/api/v10'+path,headers={'Authorization':token} if token else {},data=data) as response:
                if response.status>=400:raise web.HTTPUnauthorized(text='Discordの本人認証を確認できませんでした。開き直してください。')
                return await response.json()

    async def intent(self,request):
        try:self.row(request.query.get('instance',''))
        except web.HTTPGone:
            ticket=self.store.setting('remote_relogin_ticket',{})
            state=self.store.setting('remote_relogin_activity',{})
            recovery=bool(ticket.get('expires',0)>now_ts() and str(state.get('instance_id'))==request.query.get('instance'))
            return web.json_response({'purpose':'relogin' if recovery else 'pending'})
        return web.json_response({'purpose':'schedule'})

    async def auth(self,request):
        async with self.auth_lock:
            stamp=time.monotonic();self.attempts=[t for t in self.attempts if stamp-t<60]
            if len(self.attempts)>=12:raise web.HTTPTooManyRequests(text='少し待ってから開き直してください。')
            self.attempts.append(stamp)
            body=await request.json();row=self.row(body.get('instance'))
            config=self.store.setting('discord',{})
            if not isinstance(body.get('code'),str) or not 1<=len(body['code'])<=2048:raise web.HTTPBadRequest()
            verifier=body.get('verifier')
            import re
            if not isinstance(verifier,str) or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}',verifier):raise web.HTTPBadRequest(text='本人認証の照合情報が不正です。')
            access=await self.discord('/oauth2/token','',method='POST',data={'client_id':config['application'],
                'code_verifier':verifier,'grant_type':'authorization_code','code':body['code']})
            token=access['access_token']
            identity=await self.discord('/users/@me','Bearer '+token)
            application=await self.discord('/oauth2/@me','Bearer '+token)
            instance=await self.discord('/applications/'+str(config['application'])+'/activity-instances/'+str(row['instance']),
                'Bot '+unprotect(self.store.setting('discord_token','')))
            location=instance.get('location',{})
            if (str(identity.get('id'))!=row['owner'] or row['owner']!=str(config.get('owner')) or
                str(application.get('application',{}).get('id'))!=str(config['application']) or
                row['owner'] not in [str(u) for u in instance.get('users',[])] or
                str(location.get('channel_id'))!=str(config.get('channel')) or str(location.get('guild_id'))!=str(config.get('guild'))):
                raise web.HTTPForbidden(text='この予約画面は登録した本人専用です。')
            session=secrets.token_urlsafe(32)
            with self.store.connect() as c:
                changed=c.execute("UPDATE activity_reservations SET session_hash=? WHERE id=? AND state IN ('editing','submitted','cancelled') AND expires>?",
                    (hashlib.sha256(session.encode()).hexdigest(),row['id'],now_ts())).rowcount
            if not changed:raise web.HTTPGone(text='起動要求が失効しました。')
            return web.json_response({'session':session,'access_token':token})

    def authorize(self,request):
        token=request.headers.get('Authorization','').removeprefix('Bearer ')
        if not token:raise web.HTTPUnauthorized()
        with self.store.connect() as c:
            row=c.execute('SELECT * FROM activity_reservations WHERE session_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row:raise web.HTTPUnauthorized(text='予約画面を開き直してください。')
        row=self.row(row['instance'])
        if row['owner']!=str(self.store.setting('discord',{}).get('owner')):raise web.HTTPForbidden()
        return row

    async def state(self,request):
        row=self.authorize(request)
        with self.store.connect() as c:c.execute('UPDATE activity_reservations SET last_seen=? WHERE id=?',(now_ts(),row['id']))
        from .calendar_image import reservation_counts
        post=self.store.get(row['post_id']);now=datetime.fromtimestamp(now_ts(),JST)
        try:year=int(request.query.get('year',now.year));month=int(request.query.get('month',now.month));datetime(year,month,1)
        except (ValueError,OverflowError):raise web.HTTPBadRequest(text='年月が不正です。')
        counts,unknown=reservation_counts(self.store.posts(),year,month)
        job=self.store.job(row['job_id']) if row['job_id'] else None
        return web.json_response({'id':row['id'],'title':post['title'],'due':post['due'],'now':now_ts(),
            'revision':row['revision'],'year':year,'month':month,'counts':counts,'unknown':unknown,
            'state':row['state'],'job':{'id':job['id'],'state':job['state'],'result':job['result']} if job else None})

    def notify_cancel(self,c,row):
        body={'ok':True,'action':'schedule_cancel','message':'予約日時の選択を取り消しました。予約は変更していません。','url':''}
        c.execute('INSERT OR IGNORE INTO notifications(id,body) VALUES(?,?)',('activity-cancel:'+row['id'],json.dumps(body,ensure_ascii=False)))

    async def cancel(self,request):
        row=self.authorize(request)
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("UPDATE activity_reservations SET state='cancelled' WHERE id=? AND state='editing'",(row['id'],)).rowcount:
                self.notify_cancel(c,row)
        return web.json_response({'ok':True})

    async def commit(self,request):
        body=await request.json();row=self.authorize(request)
        # Durable request key makes a lost HTTP response safe to retry.
        with self.store.connect() as c:
            existing=c.execute('SELECT id FROM jobs WHERE request_key=?',('activity:'+row['id'],)).fetchone()
        if existing:
            with self.store.connect() as c:c.execute("UPDATE activity_reservations SET state='submitted',job_id=? WHERE id=?",(existing['id'],row['id']))
            return web.json_response({'job_id':existing['id']})
        if row['state']!='editing':raise web.HTTPGone(text='予約画面は閉じられています。')
        from .remote import submit
        try:
            jid=submit(self.store,'schedule',row['post_id'],{'due':body.get('due'),'confirmed_updated':row['revision']},
                'activity:'+row['id'],ui_session=row['ui_session'])
        except ValueError as exc:
            message=str(exc) or '予約画面の接続が更新されました。記事から開き直してください。'
            self.store.notification(False,message,key='activity-rejected:'+row['id'])
            raise web.HTTPConflict(text=message)
        with self.store.connect() as c:
            c.execute("UPDATE activity_reservations SET state='submitted',job_id=?,due=? WHERE id=?",(jid,body.get('due'),row['id']))
        return web.json_response({'job_id':jid})

    def expire(self):
        stamp=now_ts()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=c.execute("SELECT * FROM activity_reservations WHERE state IN ('opening','editing') AND (expires<=? OR (last_seen>0 AND last_seen<?))",(stamp,stamp-35)).fetchall()
            for row in rows:
                self.finish_disconnected(c,row)

    def current_launch(self):
        with self.store.connect() as c:
            row=c.execute("SELECT * FROM activity_reservations WHERE state='editing' ORDER BY rowid DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def closed(self,launch_id):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute("SELECT * FROM activity_reservations WHERE id=? AND state='editing'",(launch_id,)).fetchone()
            if row:
                self.finish_disconnected(c,row)

    def finish_disconnected(self,c,row):
        c.execute("UPDATE activity_reservations SET state='cancelled',session_hash=NULL WHERE id=?",(row['id'],))
        current=c.execute("SELECT value FROM settings WHERE key='discord_active_ui'").fetchone()
        if not current or json.loads(current[0])!=row['ui_session']:
            return
        opened=bool(row['last_seen'])
        message=('予約画面との接続が切れました。' if opened else '予約画面の本人確認を完了せずに終了しました。')
        body={'ok':not opened,'action':'schedule_disconnect','message':message+' 予約は変更していません。','url':''}
        c.execute('INSERT OR IGNORE INTO notifications(id,body) VALUES(?,?)',
                  ('activity-disconnect:'+row['id'],json.dumps(body,ensure_ascii=False)))

def register(app,store):
    api=service(store)
    async def health(request):
        return web.json_response({'instance':api.probe_id,'protocol':1})
    app.router.add_get('/activity-health',health)
    for method,path,handler in [('GET','intent',api.intent),('POST','auth',api.auth),('GET','state',api.state),('POST','commit',api.commit),('POST','cancel',api.cancel)]:
        app.router.add_route(method,'/api/activity/'+path,handler)

def probe_relay(store,url):
    endpoint=url+'/activity-health'
    with urlopen(endpoint,timeout=8) as response:
        if response.geturl()!=endpoint or json.loads(response.read(2048))!={'instance':service(store).probe_id,'protocol':1}:
            raise ValueError('HTTPS中継がこのPCの予約サービスを指していません。')

def ensure_host(store, *, configuring=False):
    """Reuse installed recovery listener, or run the same origin as a reservation-only host."""
    from .relogin import manager,public_url
    if manager(store).plugin is not None:return
    key=str(store.root.resolve())
    if key in _hosts:return
    config=store.setting('remote_relogin',{})
    if not store.setting('discord_enabled',False) or not (configuring or config.get('activity_confirmed')):return
    public_url(config.get('https_url',''))
    host=ActivityHost(store);host.start();_hosts[key]=host

def stop_host(store):
    host=_hosts.pop(str(store.root.resolve()),None)
    if host:host.stop()

class ActivityHost:
    def __init__(self,store):self.store=store;self.ready=threading.Event();self.error=None
    def start(self):
        self.thread=threading.Thread(target=self.run,name='note-reservation-activity',daemon=True);self.thread.start()
        if not self.ready.wait(10) or self.error:raise RuntimeError('予約画面の待受を開始できません。ポート設定を確認してください。')
    def run(self):
        self.loop=asyncio.new_event_loop();asyncio.set_event_loop(self.loop)
        async def start():
            @web.middleware
            async def guards(request,handler):
                app_id=str(self.store.setting('discord',{}).get('application',''))
                origin=request.headers.get('Origin')
                if request.path.startswith('/api/') and (request.method=='POST' or origin) and (
                    not app_id.isdigit() or origin!='https://'+app_id+'.discordsays.com'):
                    raise web.HTTPForbidden()
                try:response=await handler(request)
                except web.HTTPException as exc:response=exc
                response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff',
                    'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; frame-ancestors https://discord.com https://*.discord.com https://*.discordsays.com"})
                return response
            app=web.Application(middlewares=[guards],client_max_size=32768);register(app,self.store)
            web_root=Path(__file__).resolve().parents[1]/'extensions/remote_relogin/plugin/web'
            for route,name,kind in [('/','index.html','text/html'),('/app.js','app.js','text/javascript'),('/style.css','style.css','text/css')]:
                async def static(request,n=name,k=kind):return web.Response(body=(web_root/n).read_bytes(),content_type=k)
                app.router.add_get(route,static)
            self.runner=web.AppRunner(app,access_log=None);await self.runner.setup()
            await web.TCPSite(self.runner,'127.0.0.1',self.store.setting('remote_relogin',{}).get('port',18764)).start()
        try:self.loop.run_until_complete(start());self.ready.set();self.loop.run_forever()
        except Exception as exc:self.error=type(exc).__name__;self.ready.set()
        finally:
            if hasattr(self,'runner'):self.loop.run_until_complete(self.runner.cleanup())
            self.loop.close()
    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop);self.thread.join(10)
