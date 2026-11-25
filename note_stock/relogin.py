"""Optional remote-login extension boundary. No remote listener in the base app."""
import hashlib
import importlib.util
import ipaddress
import json
from pathlib import Path,PurePosixPath
import secrets
import shutil
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid
import zipfile
from .store_time import timestamp

EXTENSION_ID='note.remote-relogin'
API_VERSION=1
_managers={}
_lock=threading.RLock()


class RecoveryStopped(RuntimeError):
    """A normal, explicit end of a recovery wait, not a publication failure."""


def record_disconnect(store,recovery_id):
    """One durable transition/outbox record, shared by Activity and RPC loss."""
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        def read(key):
            row=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else {}
        ticket=read('remote_relogin_ticket');activity=read('remote_relogin_activity')
        if ticket.get('id')!=recovery_id or ticket.get('cancelled') or ticket.get('expires',0)<=timestamp(): return False
        if activity.get('recovery_id')!=recovery_id:
            activity={'recovery_id':recovery_id,'event_id':ticket.get('connection_id',recovery_id)}
        if activity.get('disconnected'): return False
        activity['disconnected']=True;ticket['phase']='waiting'
        for key,value in [('remote_relogin_ticket',ticket),('remote_relogin_activity',activity)]:
            c.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',(key,json.dumps(value)))
        body={'ok':True,'message':'再ログイン画面との接続を切断しました。PCを再接続待ちに戻しました。','url':'','action':'relogin_disconnect'}
        c.execute('INSERT OR IGNORE INTO notifications(id,body) VALUES(?,?)',
            ('recovery-disconnect:'+activity['event_id'],json.dumps(body,ensure_ascii=False)))
    return True


def public_url(value):
    parsed=urlsplit(value.strip())
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('公開HTTPS URLを入力してください。認証情報やクエリは指定できません。')
    if parsed.port not in (None,443) or parsed.path not in ('','/'):
        raise ValueError('公開HTTPS URLは標準ポートのドメインだけを指定してください。')
    host=parsed.hostname.lower()
    if '.' not in host or host.endswith(('.localhost','.local','.internal')) or host=='localhost':
        raise ValueError('PC内部やLANではなく、利用者本人の公開HTTPSドメインを指定してください。')
    try:
        address=ipaddress.ip_address(host)
    except ValueError:
        address=None
    if address is not None:
        raise ValueError('公開HTTPSのドメイン名を指定してください。')
    return 'https://'+host.encode('idna').decode('ascii')


def manager(store):
    with _lock:
        return _managers.setdefault(str(store.root),ExtensionManager(store))


def available(store):
    return bool(store.setting('remote_relogin',{}).get('enabled') and manager(store).plugin is not None)


class ExtensionManager:
    def __init__(self,store):
        self.store=store
        self.folder=store.root/'extensions'/'remote-relogin'
        self.plugin=None
        self.mutex=threading.RLock()
        self._offer=None  # Secret only in process memory; never persist plaintext.

    def install(self,archive):
        """Install a separately supplied source extension, without executing it."""
        if self.plugin is not None: raise ValueError('拡張機能を停止してから追加してください。')
        base=self.folder.parent
        base.mkdir(parents=True,exist_ok=True)
        staging=base/('install-'+uuid.uuid4().hex)
        staging.mkdir()
        try:
            with zipfile.ZipFile(archive) as package:
                names=package.namelist()
                if len(names)!=len(set(names)) or len(names)>50:
                    raise ValueError('拡張パッケージのファイル一覧が不正です。')
                if any(i.file_size>3_000_000 or i.external_attr>>16 & 0o170000==0o120000 for i in package.infolist()):
                    raise ValueError('拡張パッケージのファイル形式が不正です。')
                if sum(i.file_size for i in package.infolist())>10_000_000:
                    raise ValueError('拡張パッケージのサイズが上限を超えています。')
                manifest=json.loads(package.read('manifest.json'))
                if manifest.get('id')!=EXTENSION_ID or manifest.get('api')!=API_VERSION:
                    raise ValueError('この本体に対応していない拡張機能です。')
                entries=manifest.get('files',{})
                if set(entries)|{'manifest.json'}!=set(names) or 'plugin/__init__.py' not in entries:
                    raise ValueError('拡張パッケージの構成が不正です。')
                for name,digest in entries.items():
                    path=PurePosixPath(name)
                    if '\\' in name or path.is_absolute() or any(p in ('..','.') or ':' in p for p in path.parts) or not name.startswith('plugin/'):
                        raise ValueError('拡張パッケージに不正な保存先があります。')
                    raw=package.read(name)
                    if hashlib.sha256(raw).hexdigest()!=digest:
                        raise ValueError('拡張パッケージの整合性確認に失敗しました。')
                    target=staging.joinpath(*path.parts)
                    target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(raw)
                (staging/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
            old=base/('previous-'+uuid.uuid4().hex)
            if self.folder.exists(): self.folder.rename(old)
            try: staging.rename(self.folder)
            except Exception:
                if old.exists(): old.rename(self.folder)
                raise
            if old.exists(): self._remove(old)
        finally:
            if staging.exists(): self._remove(staging)
        return manifest['version']

    def _remove(self,path):
        resolved=path.resolve()
        if not resolved.is_relative_to(self.store.root/'extensions') or resolved==self.store.root/'extensions':
            raise ValueError('拡張機能の保存先を確認できません。')
        shutil.rmtree(resolved)

    def start(self):
        with self.mutex:
            if self.plugin is not None: return
            config=self.store.setting('remote_relogin',{})
            if not config.get('enabled'): return
            from .schedule_activity import stop_host
            stop_host(self.store)
            public_url(config.get('https_url',''))
            if not config.get('consent'): raise ValueError('拡張機能の操作範囲への同意が必要です。')
            manifest=json.loads((self.folder/'manifest.json').read_text(encoding='utf-8'))
            if manifest.get('api')!=API_VERSION or manifest.get('id')!=EXTENSION_ID:
                raise ValueError('拡張機能の形式が不正です。')
            for name,digest in manifest['files'].items():
                if hashlib.sha256((self.folder/name).read_bytes()).hexdigest()!=digest:
                    raise ValueError('追加後に拡張機能が変更されています。追加し直してください。')
            module_name='_note_relogin_'+hashlib.sha256(str(self.folder).encode()).hexdigest()[:16]
            spec=importlib.util.spec_from_file_location(module_name,self.folder/'plugin/__init__.py',submodule_search_locations=[str(self.folder/'plugin')])
            module=importlib.util.module_from_spec(spec); sys.modules[module_name]=module
            spec.loader.exec_module(module)
            plugin=module.Extension(self.store)
            try: plugin.start()
            except Exception:
                plugin.stop(); raise
            self.plugin=plugin

    def stop(self):
        with self.mutex:
            if self.plugin is not None:
                self.plugin.stop(); self.plugin=None
            self.store.set_setting('remote_relogin_ticket',{})
            self.store.set_setting('remote_relogin_runtime',{})
            self._offer=None

    def active_jobs(self):
        with self.store.connect() as c:
            return [dict(row) for row in c.execute("SELECT id,action,state,data FROM jobs WHERE state IN ('waiting','running')")]

    def activity_opened(self,instance_id):
        with self.mutex:
            ticket=self.store.setting('remote_relogin_ticket',{})
            if not ticket.get('id') or ticket.get('expires',0)<=timestamp(): return
            previous=self.store.setting('remote_relogin_activity',{})
            if previous.get('instance_id')==instance_id and not previous.get('disconnected'): return
            self.store.set_setting('remote_relogin_activity',{'recovery_id':ticket['id'],'instance_id':instance_id,
                'event_id':uuid.uuid4().hex,'seen':False,'deadline':timestamp()+20,'disconnected':False})

    def disconnected(self,recovery_id):
        with self.mutex:
            # Do not let an old Activity's late exit revoke a newer recovery.
            if self.store.setting('remote_relogin_ticket',{}).get('id')!=recovery_id: return False
            if self.plugin is not None:
                with self.plugin.guard:
                    self.plugin.window.release();self.plugin.sessions.clear()
            return record_disconnect(self.store,recovery_id)

    def cancel(self):
        with self.mutex:
            ticket=self.store.setting('remote_relogin_ticket',{})
            if not ticket.get('id'): return False
            ticket.update(cancelled=True,expires=0)
            self.store.set_setting('remote_relogin_ticket',ticket)
            self._offer=None
            if self.plugin is not None:
                with self.plugin.guard:
                    self.plugin.sessions.clear();self.plugin.handshake=None;self.plugin.window.release()
            for job in self.active_jobs():
                if job['action']=='relogin_note' and job['state']=='waiting' and json.loads(job['data']).get('recovery_id')==ticket['id']:
                    self.store.complete_job(job['id'],True,'再ログインを中止しました。')
            return True

    def finished(self,recovery_id):
        with self.mutex:
            current=self.store.setting('remote_relogin_ticket',{}).get('id')
            if current and current!=recovery_id: return
            if self.plugin is not None:
                with self.plugin.guard:
                    self.plugin.sessions.clear();self.plugin.handshake=None;self.plugin.window.release()
            self._offer=None
            self.store.set_setting('remote_relogin_ticket',{})
            self.store.set_setting('remote_relogin_activity',{})
            if self.store.setting('remote_relogin_window',{}).get('recovery_id')==recovery_id:
                self.store.set_setting('remote_relogin_window',{})

    def issue(self,request_key):
        with self.mutex:
            if self.plugin is None: raise ValueError('PCの設定から遠隔再ログイン拡張を有効にしてください。')
            config=self.store.setting('remote_relogin',{})
            if not config.get('activity_confirmed') or not self.plugin.probe_ok:
                raise ValueError('PCでHTTPS接続試験とDiscord Activityの初回設定を完了してください。')
            if not self.store.setting('account',''): raise ValueError('PCの設定から投稿先のnote IDを登録してください。')
            jobs=self.active_jobs()
            ticket=self.store.setting('remote_relogin_ticket',{})
            recovery=[job for job in jobs if job['action']=='relogin_note']
            if recovery and (ticket.get('expires',0)<=timestamp() or ticket.get('cancelled')):
                # The worker owns its browser. Request its normal stop, then
                # wait for its durable completion instead of spawning a rival.
                self.cancel()
                deadline=time.monotonic()+8
                while any(job['action']=='relogin_note' for job in self.active_jobs()) and time.monotonic()<deadline:
                    time.sleep(.05)
                jobs=self.active_jobs();recovery=[job for job in jobs if job['action']=='relogin_note']
                if recovery: raise ValueError('期限切れの接続を終了しています。少し待ってから開き直してください。')
            if any(job['state']=='running' and job['action']!='relogin_note' for job in jobs):
                raise ValueError('noteの別の処理が実行中です。完了後に再ログインを開いてください。')
            existing=next((job for job in recovery if json.loads(job['data']).get('recovery_id')==ticket.get('id')),None)
            if recovery and existing is None:
                raise ValueError('前の接続を終了しています。少し待ってから開き直してください。')
            if existing and not ticket.get('claimed') and self._offer and self._offer[0]==ticket['id']:
                # Returning without entering a code is navigation, not a second
                # operation. The same offer and browser remain available.
                return self._offer[1]
            code=secrets.token_urlsafe(24)
            recovery_id=ticket['id'] if existing else uuid.uuid4().hex
            self.store.set_setting('paused',True)
            self.store.set_setting('account_verified',False)
            ticket={'id':recovery_id,'code_hash':hashlib.sha256(code.encode()).hexdigest(),
                    'expires':timestamp()+600,'claimed':False,'phase':'waiting'}
            with self.plugin.guard:
                if not existing: self.store.set_setting('remote_relogin_window',{})
                self.store.set_setting('remote_relogin_ticket',ticket)
                try:
                    self.plugin.new_recovery(ticket)
                    if not existing:
                        self.store.enqueue('relogin_note',data={'recovery_id':recovery_id},request_key='relogin:'+str(request_key))
                    self._offer=(recovery_id,code)
                except Exception:
                    self.store.set_setting('remote_relogin_ticket',{}); raise
            return code
