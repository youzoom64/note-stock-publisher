import asyncio
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import zipfile
import pytest
from aiohttp.test_utils import TestClient,TestServer
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from note_stock.store import Store,now_ts
from note_stock.relogin import ExtensionManager,public_url,available
from extensions.remote_relogin.plugin.server import Extension
from extensions.remote_relogin.plugin.secure_channel import Handshake,Channel,PROTOCOL,encode,decode,nonce


class TestWindow:
    __test__=False
    def __init__(self): self.inputs=[]; self.released=0
    def frame(self): return b'test-frame','fixture-window',800,600
    def text(self,frame,text): self.inputs.append((frame,text))
    def release(self): self.released+=1


def client_keys(hello,code):
    private=ec.generate_private_key(ec.SECP256R1())
    public=private.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    server=decode(hello['public'])
    shared=private.exchange(ec.ECDH(),ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),server))
    context=PROTOCOL+hello['id'].encode()+server+public
    keys=HKDF(algorithm=hashes.SHA256(),length=64,salt=hashlib.sha256(code.encode()).digest(),info=context).derive(shared)
    proof=encode(AESGCM(keys[:32]).encrypt(nonce(0),b'pair',context))
    return public,proof,Channel(keys[32:],keys[:32],context),keys[32:],context


@pytest.mark.parametrize('url',['http://example.test','https://127.0.0.1','https://localhost','https://machine.local','https://user:pass@example.test','https://example.test?q=secret','https://example.test:8443','https://example.test/path'])
def test_relay_address_rejects_credentials_lan_and_non_https(url):
    with pytest.raises(ValueError): public_url(url)


def test_base_app_has_no_listener_until_extension_is_added_and_enabled(tmp_path):
    store=Store(tmp_path)
    assert not available(store)
    manager=ExtensionManager(store); manager.start()
    assert manager.plugin is None and not manager.folder.exists()
    assert public_url('https://LOGIN.example.test/')=='https://login.example.test'


def test_package_traversal_is_rejected_without_damaging_previous_install(tmp_path):
    manager=ExtensionManager(Store(tmp_path/'data')); manager.folder.mkdir(parents=True)
    marker=manager.folder/'keep.txt'; marker.write_text('previous')
    path='plugin/../../../outside.py'; content=b'not executed'
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z:
        manifest={'id':'note.remote-relogin','api':1,'files':{path:hashlib.sha256(content).hexdigest(),'plugin/__init__.py':hashlib.sha256(b'').hexdigest()}}
        z.writestr('manifest.json',json.dumps(manifest));z.writestr(path,content);z.writestr('plugin/__init__.py',b'')
    with pytest.raises(ValueError): manager.install(archive)
    assert marker.read_text()=='previous' and not (tmp_path/'outside.py').exists()


def test_authenticated_encryption_binds_both_public_keys_and_rejects_replay():
    code='random test pairing value'; handshake=Handshake('recovery',hashlib.sha256(code.encode()).hexdigest())
    hello={'id':'recovery','public':encode(handshake.public)}
    public,proof,client,_,_=client_keys(hello,code)
    server=handshake.accept(encode(public),proof)
    request=client.pack(1,{'action':'text','text':'not-for-the-relay'})
    assert 'not-for-the-relay' not in json.dumps(request)
    number,value=server.unpack(request)
    assert value['text']=='not-for-the-relay'
    assert client.unpack(server.pack(number,{'ok':True}))[1]=={'ok':True}
    with pytest.raises(ValueError): server.unpack(request)
    other=Handshake('recovery',hashlib.sha256(code.encode()).hexdigest())
    with pytest.raises(Exception): other.accept(encode(public),proof)


def test_http_owner_pairing_expiry_revocation_and_no_plaintext_input(tmp_path):
    async def run():
        store=Store(tmp_path);store.set_setting('discord',{'application':'1234567890'})
        store.set_setting('account_verified',False)
        code='a high entropy one-time fixture code'
        ticket={'id':'case','code_hash':hashlib.sha256(code.encode()).hexdigest(),'expires':now_ts()+600,'claimed':False}
        store.set_setting('remote_relogin_ticket',ticket)
        store.set_setting('remote_relogin_window',{'recovery_id':'case','ready':True})
        window=TestWindow(); extension=Extension(store,window)
        extension.new_recovery(ticket)
        client=TestClient(TestServer(extension.app()));await client.start_server()
        origin={'Origin':'https://1234567890.discordsays.com'}
        try:
            assert (await client.post('/api/pair',json={})).status==403
            assert (await client.post('/api/rpc',json={},headers=origin)).status==401
            hello=await (await client.get('/api/hello')).json()
            public,bad_proof,_,_,_=client_keys(hello,'wrong secret')
            assert (await client.post('/api/pair',json={'public':encode(public),'proof':bad_proof},headers=origin)).status==401
            public,proof,channel,key,context=client_keys(hello,code)
            pair=await client.post('/api/pair',json={'public':encode(public),'proof':proof},headers=origin)
            assert pair.status==200
            welcome=await pair.json()
            token=json.loads(AESGCM(key).decrypt(nonce(0),decode(welcome['box']),context))['session']
            assert (await client.post('/api/pair',json={'public':encode(public),'proof':proof},headers=origin)).status==401
            headers={**origin,'Authorization':'Bearer '+token}
            request=channel.pack(1,{'action':'text','frame':'fixture-window','text':'secret fixture password'})
            assert 'secret fixture password' not in json.dumps(request)
            response=await client.post('/api/rpc',json=request,headers=headers)
            assert response.status==200
            assert 'secret fixture password' not in await response.text()
            assert channel.unpack(await response.json())[1]['ok']
            assert window.inputs==[('fixture-window','secret fixture password')]
            assert (await client.post('/api/rpc',json=request,headers=headers)).status==401
            store.set_setting('account_verified',True)
            response=await client.post('/api/rpc',json=channel.pack(2,{'action':'text','text':'MUST NOT TYPE'}),headers=headers)
            assert channel.unpack(await response.json())[1]['connected']
            assert len(window.inputs)==1
            assert (await client.get('/api/hello')).status==401
            assert (await client.post('/api/rpc',json=channel.pack(3,{'action':'frame'}),headers=headers)).status==401
            assert not extension.sessions
        finally:
            await client.close();extension.stop()
    asyncio.run(run())


def test_expired_ticket_never_exposes_a_frame(tmp_path):
    store=Store(tmp_path);window=TestWindow();extension=Extension(store,window)
    ticket={'id':'old','code_hash':hashlib.sha256(b'x').hexdigest(),'expires':now_ts()-1,'claimed':False}
    store.set_setting('remote_relogin_ticket',ticket);extension.new_recovery(ticket)
    with pytest.raises(Exception): extension.execute({'action':'frame'})
    assert not window.inputs and window.released


def test_pointer_release_works_even_when_browser_is_not_ready(tmp_path):
    store=Store(tmp_path);window=TestWindow();extension=Extension(store,window)
    ticket={'id':'release','code_hash':hashlib.sha256(b'x').hexdigest(),'expires':now_ts()+600,'claimed':False}
    store.set_setting('remote_relogin_ticket',ticket);extension.new_recovery(ticket)
    before=window.released
    assert extension.execute({'action':'release'})=={'ok':True}
    assert window.released==before+1 and not window.inputs


def test_recovery_job_can_run_while_regular_browser_jobs_are_blocked(tmp_path):
    store=Store(tmp_path);store.enqueue('sync_articles')
    jid=store.enqueue('relogin_note',data={'recovery_id':'test'})
    assert store.next_job(browser_allowed=False)['id']==jid


def test_discord_pairing_code_only_edits_the_private_deferred_response(tmp_path):
    from note_stock.discord_bot import NoteClient
    async def run():
        store=Store(tmp_path);store.set_setting('discord',{'owner':'1','guild':'2','channel':'3','menu':'99'})
        client=NoteClient(store);client.http.get_message=AsyncMock(return_value={'content':'note管理'})
        client.http.delete_message=AsyncMock()
        message=SimpleNamespace(id=100,content='',embeds=[],delete=AsyncMock(),edit=AsyncMock())
        interaction=SimpleNamespace(id=12,user=SimpleNamespace(id=1),guild_id=2,channel_id=3,
            response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock(),edit_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock(return_value=message)),edit_original_response=AsyncMock(return_value=message))
        fake=SimpleNamespace(issue=lambda _: 'private-pairing-fixture')
        with patch('note_stock.relogin.manager',return_value=fake): await client.begin_relogin(interaction)
        assert interaction.response.defer.call_args.kwargs=={'ephemeral':True,'thinking':True}
        assert 'private-pairing-fixture' in interaction.edit_original_response.call_args.kwargs['content']
        interaction.followup.send.assert_not_awaited()
        interaction.response.edit_message.assert_not_awaited()
        await client.close()
    asyncio.run(run())


def recovery_manager(tmp_path):
    import threading
    store=Store(tmp_path)
    store.set_setting('remote_relogin',{'activity_confirmed':True})
    store.set_setting('account','fixture-account')
    extension=Extension(store,TestWindow());extension.probe_ok=True
    manager=ExtensionManager(store);manager.plugin=extension
    return store,manager


@pytest.mark.parametrize('start_worker',[False,True])
def test_reopen_without_code_returns_same_offer_and_never_adds_a_job(tmp_path,start_worker):
    store,manager=recovery_manager(tmp_path)
    first=manager.issue('first');ticket=store.setting('remote_relogin_ticket',{})
    if start_worker: store.next_job(browser_allowed=False)
    for n in range(10): assert manager.issue('reopen:'+str(n))==first
    assert store.setting('remote_relogin_ticket',{})==ticket
    assert len(manager.active_jobs())==1
    assert ticket['phase']=='waiting'


def test_reopen_after_claim_rotates_code_but_keeps_same_browser_job(tmp_path):
    store,manager=recovery_manager(tmp_path)
    first=manager.issue('first');job=store.next_job(browser_allowed=False)
    ticket=store.setting('remote_relogin_ticket',{});ticket['claimed']=True
    store.set_setting('remote_relogin_ticket',ticket)
    window={'recovery_id':ticket['id'],'ready':True};store.set_setting('remote_relogin_window',window)
    second=manager.issue('reopen')
    assert first!=second and store.setting('remote_relogin_ticket',{})['id']==ticket['id']
    assert store.setting('remote_relogin_window',{})==window
    assert manager.active_jobs()[0]['id']==job['id'] and len(manager.active_jobs())==1
    assert manager.issue('third')==second


def test_cancel_waiting_offer_completes_once_and_can_restart(tmp_path):
    store,manager=recovery_manager(tmp_path)
    first=manager.issue('first');old=manager.active_jobs()[0]['id']
    assert manager.cancel()
    assert not manager.active_jobs() and store.job(old)['result']['ok']
    manager.finished(store.setting('remote_relogin_ticket',{})['id'])
    assert not manager.cancel()
    assert manager.issue('restart')!=first
    assert len(manager.active_jobs())==1


def test_suspend_releases_mouse_and_returns_to_waiting_without_ending_browser(tmp_path):
    store,manager=recovery_manager(tmp_path);manager.issue('start')
    ticket=store.setting('remote_relogin_ticket',{});ticket.update(claimed=True,phase='connected')
    store.set_setting('remote_relogin_ticket',ticket)
    assert manager.plugin.execute({'action':'suspend'})=={'ok':True,'waiting':True}
    assert store.setting('remote_relogin_ticket',{})['phase']=='waiting'
    assert len(manager.active_jobs())==1


def test_preflight_error_reuses_private_screen_with_menu_instead_of_adding_followups(tmp_path):
    from note_stock.discord_bot import NoteClient,Menu
    async def run():
        store=Store(tmp_path);store.set_setting('discord',{'owner':'1','guild':'2','channel':'3'})
        client=NoteClient(store)
        message=SimpleNamespace(id=100,content='',embeds=[],flags=SimpleNamespace(ephemeral=True),delete=AsyncMock(),edit=AsyncMock())
        interaction=SimpleNamespace(id=12,user=SimpleNamespace(id=1),guild_id=2,channel_id=3,message=message,
            response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(return_value=message))
        def fail(_): raise ValueError('接続できません。')
        with patch('note_stock.relogin.manager',return_value=SimpleNamespace(issue=fail)):
            await client.begin_relogin(interaction)
        assert interaction.response.defer.call_args.kwargs['thinking'] is False
        assert isinstance(interaction.edit_original_response.call_args.kwargs['view'],Menu)
        interaction.followup.send.assert_not_awaited()
        await client.close()
    asyncio.run(run())


def test_discord_exit_before_pairing_records_once_returns_to_waiting_and_can_reopen(tmp_path):
    from note_stock.discord_bot import NoteClient
    from note_stock.relogin import record_disconnect
    async def run():
        store,manager=recovery_manager(tmp_path)
        store.set_setting('discord',{'owner':'1','guild':'2','channel':'3','application':'4'})
        code=manager.issue('start');store.next_job(browser_allowed=False)
        manager.activity_opened('instance-case')
        client=NoteClient(store);client.is_ready=lambda:True
        client.http.request=AsyncMock(return_value={'users':['1'],'location':{'guild_id':'2','channel_id':'3'}})
        await client.recovery_presence.coro(client)
        assert store.setting('remote_relogin_activity',{})['seen']
        client.http.request=AsyncMock(return_value={'users':[],'location':{'guild_id':'2','channel_id':'3'}})
        with patch('note_stock.relogin.manager',return_value=manager):
            await client.recovery_presence.coro(client)
            await client.recovery_presence.coro(client)
        ticket=store.setting('remote_relogin_ticket',{})
        assert ticket['phase']=='waiting' and not ticket['claimed']
        assert not record_disconnect(store,ticket['id'])
        assert len(store.pending_notifications())==1
        assert manager.issue('reopen')==code and len(manager.active_jobs())==1
        manager.activity_opened('instance-case')
        assert not store.setting('remote_relogin_activity',{})['disconnected']
        await client.close()
    asyncio.run(run())


def test_concurrent_reopen_never_creates_multiple_recovery_jobs(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store,manager=recovery_manager(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool: codes=list(pool.map(manager.issue,map(str,range(20))))
    assert len(set(codes))==1 and len(manager.active_jobs())==1


@pytest.mark.parametrize('record,expected',[
    ({'pid':123,'created':100.0},True),
    ({'pid':456,'created':100.0},False),
    ({'pid':123,'created':101.0},False),
    ({'pid':123},False),
    ({},False),
])
def test_capture_never_uses_a_different_or_reused_browser_process(record,expected):
    from extensions.remote_relogin.plugin.windows_browser import matches_recovery_process
    assert matches_recovery_process({'pid':123,'created':100.0},record) is expected


def test_expired_running_recovery_stops_then_starts_a_new_job(tmp_path):
    import threading,time
    store,manager=recovery_manager(tmp_path);manager.issue('first');job=store.next_job(browser_allowed=False)
    ticket=store.setting('remote_relogin_ticket',{});ticket['expires']=now_ts()-1
    store.set_setting('remote_relogin_ticket',ticket)
    def finish_worker():
        while not store.setting('remote_relogin_ticket',{}).get('cancelled'): time.sleep(.01)
        store.complete_job(job['id'],True,'再ログインを中止しました。')
    worker=threading.Thread(target=finish_worker);worker.start()
    manager.issue('restart');worker.join(2)
    assert store.setting('remote_relogin_ticket',{})['id']!=ticket['id']
    assert len(manager.active_jobs())==1 and manager.active_jobs()[0]['id']!=job['id']
