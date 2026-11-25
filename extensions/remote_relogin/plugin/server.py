"""Loopback Activity service. Frames and input use an authenticated encrypted RPC."""
import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
from aiohttp import web
from note_stock.store_time import timestamp
from note_stock.relogin import record_disconnect
from .secure_channel import Handshake,encode
from .windows_browser import BrowserWindow


class Extension:
    def __init__(self,store,window=None):
        self.store=store; self.window=window or BrowserWindow(store)
        self.thread=None; self.loop=None; self.runner=None; self.ready=threading.Event()
        self.probe_ok=False; self.start_error=None; self.health=secrets.token_hex(24)
        self.handshake=None; self.recovery_id=''; self.sessions={}; self.attempts=[]
        self.closed=False; self.guard=threading.RLock()

    def start(self):
        self.thread=threading.Thread(target=self.run,name='note-remote-relogin',daemon=True); self.thread.start()
        if not self.ready.wait(10): raise RuntimeError('遠隔再ログインの待受開始を確認できませんでした。')
        if self.start_error: raise RuntimeError(self.start_error)
        threading.Thread(target=self.initial_probe,daemon=True).start()

    def initial_probe(self):
        try: self.probe()
        except Exception: self.probe_ok=False

    def run(self):
        self.loop=asyncio.new_event_loop(); asyncio.set_event_loop(self.loop)
        async def start():
            self.runner=web.AppRunner(self.app(),access_log=None)
            await self.runner.setup()
            port=self.store.setting('remote_relogin',{}).get('port',18764)
            if type(port) is not int or not 1024<=port<=65535: raise ValueError('待受ポートが不正です。')
            await web.TCPSite(self.runner,'127.0.0.1',port).start()
        try:
            self.loop.run_until_complete(start()); self.ready.set(); self.loop.run_forever()
        except Exception as exc:
            self.start_error='待受を開始できません（'+type(exc).__name__+'）。ポートの重複を確認してください。'
            self.ready.set()
        finally:
            if self.runner: self.loop.run_until_complete(self.runner.cleanup())
            self.loop.close()

    def stop(self):
        with self.guard:
            self.closed=True; self.sessions.clear(); self.handshake=None; self.window.release()
        if self.loop and self.loop.is_running(): self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(10)

    def new_recovery(self,ticket):
        with self.guard:
            self.window.release(); self.sessions.clear(); self.attempts=[]
            self.recovery_id=ticket['id']; self.handshake=Handshake(ticket['id'],ticket['code_hash'])

    def ticket(self):
        ticket=self.store.setting('remote_relogin_ticket',{})
        if self.closed or ticket.get('id')!=self.recovery_id or ticket.get('expires',0)<=timestamp():
            self.sessions.clear(); self.window.release(); raise web.HTTPUnauthorized(text='接続は終了しています。Discordからやり直してください。')
        return ticket

    def probe(self):
        config=self.store.setting('remote_relogin',{})
        endpoint=config['https_url']+'/health'
        request=Request(endpoint,headers={'User-Agent':'NoteStockPublisher-RemoteRelogin/1'})
        with urlopen(request,timeout=8) as response:
            if response.geturl()!=endpoint: raise ValueError('Unexpected HTTPS redirect')
            result=json.loads(response.read(2048))
        self.probe_ok=hmac.compare_digest(result.get('instance',''),self.health)
        if not self.probe_ok: raise ValueError('HTTPS relay points to a different installation')
        self.store.set_setting('remote_relogin_runtime',{'https_ok':True,'at':timestamp(),'loopback_only':True})

    def origin_allowed(self,request):
        app=str(self.store.setting('discord',{}).get('application',''))
        expected='https://'+app+'.discordsays.com'
        return bool(app.isdigit() and request.headers.get('Origin')==expected)

    def app(self):
        @web.middleware
        async def guards(request,handler):
            # Only fixed static routes exist. No arbitrary file path or desktop target.
            if request.method=='POST' and not self.origin_allowed(request): raise web.HTTPForbidden(text='許可されたDiscord内の画面から操作してください。')
            try:
                response=await handler(request)
            except web.HTTPException as exc: response=exc
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
            response.headers['Referrer-Policy']='no-referrer'
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors https://discord.com https://*.discord.com https://*.discordsays.com"
            return response
        app=web.Application(client_max_size=32_768,middlewares=[guards])
        from note_stock.schedule_activity import register
        register(app,self.store)
        async def expiry(application):
            async def sweep():
                while True:
                    await asyncio.sleep(2)
                    with self.guard:
                        ticket=self.store.setting('remote_relogin_ticket',{})
                        expired=ticket.get('expires',0)<=timestamp()
                        if any(time.monotonic()-s['last']>3 for s in self.sessions.values()):
                            self.window.release()
                            if ticket.get('phase')!='waiting':
                                ticket['phase']='waiting';self.store.set_setting('remote_relogin_ticket',ticket)
                        if any(time.monotonic()-s['last']>15 for s in self.sessions.values()):
                            self.sessions.clear();record_disconnect(self.store,self.recovery_id)
                        if expired or any(time.monotonic()-s['last']>120 for s in self.sessions.values()):
                            self.sessions.clear(); self.window.release()
                            if expired: self.handshake=None
            task=asyncio.create_task(sweep())
            yield
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
        app.cleanup_ctx.append(expiry)
        app.router.add_get('/health',self.health_response)
        app.router.add_get('/api/hello',self.hello)
        app.router.add_post('/api/pair',self.pair)
        app.router.add_post('/api/rpc',self.rpc)
        for route,file,content_type in [('/','index.html','text/html'),('/app.js','app.js','text/javascript'),('/style.css','style.css','text/css')]:
            async def static(request,name=file,kind=content_type):
                return web.Response(body=(Path(__file__).parent/'web'/name).read_bytes(),content_type=kind)
            app.router.add_get(route,static)
        return app

    async def health_response(self,request): return web.json_response({'protocol':1,'instance':self.health})

    async def hello(self,request):
        with self.guard:
            self.ticket()
            return web.json_response({'id':self.recovery_id,'public':encode(self.handshake.public),'protocol':1})

    async def pair(self,request):
        try: body=await request.json()
        except Exception: raise web.HTTPBadRequest(text='接続要求の形式が不正です。')
        with self.guard:
            ticket=self.ticket()
            current=time.monotonic(); self.attempts=[at for at in self.attempts if current-at<60]
            if len(self.attempts)>=5: raise web.HTTPTooManyRequests(text='Discordから新しい接続コードを発行してください。')
            if ticket.get('claimed'): raise web.HTTPUnauthorized(text='このコードは使用済みです。')
            self.attempts.append(current)
            try: channel=self.handshake.accept(body['public'],body['proof'])
            except Exception: raise web.HTTPUnauthorized(text='接続コードを確認してください。')
            # Claim atomically; two clients cannot reuse the same code.
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                active=json.loads(c.execute("SELECT value FROM settings WHERE key='remote_relogin_ticket'").fetchone()[0])
                if active.get('id')!=ticket['id'] or active.get('claimed') or active['expires']<=timestamp():
                    raise web.HTTPUnauthorized(text='このコードは使用できません。')
                active['claimed']=True;active['phase']='connected'
                c.execute("UPDATE settings SET value=? WHERE key='remote_relogin_ticket'",(json.dumps(active),))
            token=secrets.token_urlsafe(32)
            self.sessions={token:{'channel':channel,'last':time.monotonic()}}
            return web.json_response({'box':channel.welcome({'session':token,'expires':ticket['expires']})})

    async def rpc(self,request):
        token=request.headers.get('Authorization','').removeprefix('Bearer ')
        with self.guard:
            self.ticket()
            session=self.sessions.get(token)
            if not session or time.monotonic()-session['last']>120:
                self.sessions.pop(token,None); self.window.release(); raise web.HTTPUnauthorized(text='接続が失効しました。')
        try: body=await request.json()
        except Exception: raise web.HTTPBadRequest(text='接続要求の形式が不正です。')
        with self.guard:
            try: number,command=session['channel'].unpack(body)
            except Exception: raise web.HTTPUnauthorized(text='無効な操作要求です。')
            session['last']=time.monotonic()
            try: result=await asyncio.to_thread(self.execute,command)
            except Exception as exc:
                # Do not echo typed input, URLs, or exception messages to logs/HTTP errors.
                result={'ok':False,'message':'専用ブラウザーを操作できません（'+type(exc).__name__+'）。画面を更新して確認してください。'}
            response=web.json_response(session['channel'].pack(number,result))
            if result.get('connected') or result.get('ended'):
                self.sessions.clear(); self.handshake=None; self.window.release()
                if result.get('connected'): self.store.set_setting('remote_relogin_ticket',{})
            return response

    def execute(self,command):
        self.ticket()
        action=command.get('action')
        if action=='release':
            self.window.release(); return {'ok':True}
        if action=='suspend':
            self.window.release()
            record_disconnect(self.store,self.recovery_id)
            return {'ok':True,'waiting':True}
        if action=='status': return {'ok':True,'connected':bool(self.store.setting('account_verified',False)),'paused':True}
        if action=='end':
            self.window.release()
            ticket=self.ticket();ticket.update(cancelled=True,expires=0,phase='cancelled');self.store.set_setting('remote_relogin_ticket',ticket)
            return {'ok':True,'ended':True}
        if self.store.setting('account_verified',False):
            self.window.release(); return {'ok':True,'connected':True,'paused':True}
        state=self.store.setting('remote_relogin_window',{})
        if state.get('recovery_id')!=self.recovery_id or not state.get('ready'):
            return {'ok':False,'message':'PCで再ログイン用の専用ブラウザーを開いています。少し待ってください。'}
        if action=='frame':
            ticket=self.ticket()
            if ticket.get('phase')!='connected':
                ticket['phase']='connected';self.store.set_setting('remote_relogin_ticket',ticket)
            raw,frame,width,height=self.window.frame()
            return {'ok':True,'image':encode(raw),'frame':frame,'width':width,'height':height}
        frame=command.get('frame','')
        if action=='pointer': self.window.pointer(frame,command.get('x'),command.get('y'),command.get('phase'))
        elif action=='text': self.window.text(frame,command.get('text'))
        elif action=='key': self.window.key(frame,command.get('key'))
        elif action=='scroll': self.window.scroll(frame,command.get('delta'))
        elif action=='finish': self.window.finish_login()
        else: raise ValueError('Unsupported action')
        return {'ok':True}
