import {DiscordSDK,RPCCloseCodes} from '@discord/embedded-app-sdk';
import {ScreenGestures} from './screen-gestures.js';
import {openSchedule} from './schedule.js';
const $=id=>document.getElementById(id), status=$('status');
const bytes=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
const base64=b=>{let s='';for(const x of new Uint8Array(b))s+=String.fromCharCode(x);return btoa(s)};
const utf=s=>new TextEncoder().encode(s);
const concat=(...parts)=>{let out=new Uint8Array(parts.reduce((n,p)=>n+p.length,0)),offset=0;for(const p of parts){out.set(p,offset);offset+=p.length}return out};
const nonce=n=>{const a=new Uint8Array(12);new DataView(a.buffer).setBigUint64(4,BigInt(n));return a};
let session='',sendKey,receiveKey,context,count=0,frame='',active=false,chain=Promise.resolve(),objectURL='',timer;
const say=text=>status.textContent=text;
async function fetchJSON(path,options){const r=await fetch(path,{cache:'no-store',...options});if(!r.ok)throw Object.assign(Error(await r.text()),{status:r.status});return r.json()}
async function connect(code){
 const hello=await fetchJSON('/api/hello');
 const server=bytes(hello.public);
 const pair=await crypto.subtle.generateKey({name:'ECDH',namedCurve:'P-256'},true,['deriveBits']);
 const publicKey=new Uint8Array(await crypto.subtle.exportKey('raw',pair.publicKey));
 const peer=await crypto.subtle.importKey('raw',server,{name:'ECDH',namedCurve:'P-256'},false,[]);
 const shared=await crypto.subtle.deriveBits({name:'ECDH',public:peer},pair.privateKey,256);
 context=concat(utf('note-remote-login-v1'),utf(hello.id),server,publicKey);
 const salt=await crypto.subtle.digest('SHA-256',utf(code));
 const material=await crypto.subtle.importKey('raw',shared,'HKDF',false,['deriveBits']);
 const keys=new Uint8Array(await crypto.subtle.deriveBits({name:'HKDF',hash:'SHA-256',salt,info:context},material,512));
 sendKey=await crypto.subtle.importKey('raw',keys.slice(0,32),'AES-GCM',false,['encrypt']);
 receiveKey=await crypto.subtle.importKey('raw',keys.slice(32),'AES-GCM',false,['decrypt']); keys.fill(0);
 const proof=await crypto.subtle.encrypt({name:'AES-GCM',iv:nonce(0),additionalData:context},sendKey,utf('pair'));
 const welcome=await fetchJSON('/api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({public:base64(publicKey),proof:base64(proof)})});
 const raw=await crypto.subtle.decrypt({name:'AES-GCM',iv:nonce(0),additionalData:context},receiveKey,bytes(welcome.box));
 session=JSON.parse(new TextDecoder().decode(raw)).session;
 count=0;active=true;$('pair').hidden=true;$('controls').hidden=false;say('接続しました。専用ブラウザーの表示を待っています。');update();
}
function rpc(command){
 const operation=chain.then(async()=>{
  if(!active)throw Error('接続は終了しています。');
  const n=++count,aad=concat(context,utf('rpc'),nonce(n));
  const box=await crypto.subtle.encrypt({name:'AES-GCM',iv:nonce(n),additionalData:aad},sendKey,utf(JSON.stringify(command)));
  const response=await fetchJSON('/api/rpc',{method:'POST',headers:{Authorization:'Bearer '+session,'Content-Type':'application/json'},body:JSON.stringify({n,box:base64(box)})});
  if(response.n!==n)throw Error('応答の確認に失敗しました。');
  const data=JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:nonce(n),additionalData:aad},receiveKey,bytes(response.box))));
  if(data.connected){closeLocal();say('noteへの接続が復旧しました。Discordの管理メニューへ戻って操作してください。自動投稿は停止したままです。')}
  if(!data.ok)throw Error(data.message||'操作できませんでした。');
  return data;
 });
 chain=operation.catch(()=>{});return operation;
}
function closeLocal(){if(!$('screen'))return;gestures.reset();active=false;clearTimeout(timer);session='';sendKey=null;receiveKey=null;frame='';$('controls').hidden=true;$('screen').removeAttribute('src');if(objectURL)URL.revokeObjectURL(objectURL);objectURL=''}
async function update(){
 if(!active)return;
 try{
  const data=await rpc({action:'frame'});
  if(data.image){const url=URL.createObjectURL(new Blob([bytes(data.image)],{type:'image/jpeg'}));$('screen').src=url;if(objectURL)URL.revokeObjectURL(objectURL);objectURL=url;frame=data.frame;say('PCの専用ブラウザーを操作中');}
 }catch(error){
  if(error.status===401||error.status===410){closeLocal();$('pair').hidden=false;say('接続を切断しました。Discordの操作メニューから再ログインを開き、新しいコードで接続してください。')}
  else say(error.message);
 }
 if(active)timer=setTimeout(update,650);
}
$('pair').addEventListener('submit',async e=>{e.preventDefault();const code=$('code').value.trim();$('code').value='';$('connect').disabled=true;try{await connect(code)}catch(error){say(error.message)}finally{$('connect').disabled=false}});
$('type').addEventListener('submit',async e=>{e.preventDefault();const value=$('text').value;$('text').value='';try{await rpc({action:'text',frame,text:value})}catch(error){say(error.message)}});
$('reveal').addEventListener('change',()=>{$('text').type=$('reveal').checked?'text':'password'});
for(const b of document.querySelectorAll('[data-key]'))b.addEventListener('click',()=>rpc({action:'key',frame,key:b.dataset.key}).catch(e=>say(e.message)));
for(const b of document.querySelectorAll('[data-scroll]'))b.addEventListener('click',()=>rpc({action:'scroll',frame,delta:Number(b.dataset.scroll)}).catch(e=>say(e.message)));
$('finish').addEventListener('click',()=>rpc({action:'finish',frame}).then(()=>say('接続を確認しています…')).catch(e=>say(e.message)));
$('end').addEventListener('click',()=>rpc({action:'end'}).catch(()=>{}).finally(()=>{closeLocal();say('遠隔操作を終了しました。Discordの管理メニューへ戻ってください。')}));
const gestures=new ScreenGestures({screen:$('screen'),viewport:$('viewport'),ready:()=>active&&!!frame,
 send:command=>rpc({...command,frame}).catch(error=>say(error.message)),
 changed:zoom=>{
  $('zoom-level').textContent=Math.round(zoom*100)+'%';
  $('zoom-out').disabled=zoom<=1;$('zoom').disabled=zoom>=5;
 }});
$('zoom').addEventListener('click',()=>gestures.setZoom(gestures.zoom*1.5));
$('zoom-out').addEventListener('click',()=>gestures.setZoom(gestures.zoom/1.5));
$('zoom-reset').addEventListener('click',()=>gestures.setZoom(1));
window.addEventListener('resize',()=>gestures.setZoom(gestures.zoom));
window.addEventListener('pagehide',()=>{closeLocal()});
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(timer);gestures.reset();if(active)rpc({action:'suspend'}).catch(()=>{})}else if(active)update()});
async function boot(){
 // Discord supplies frame_id/platform, but does not supply client_id when
 // launching an Activity. The proxy hostname identifies this installation's
 // application without bundling the distributor's application ID.
 const client=/^(\d+)\.discordsays\.com$/.exec(location.hostname)?.[1]||new URLSearchParams(location.search).get('client_id');
 if(window.parent!==window){
  if(!client){$('connect').disabled=true;say('Discord内から起動し直してください。');return}
  try{const sdk=new DiscordSDK(client);await sdk.ready();
   let intent;
   for(let attempt=0;attempt<10;attempt++){
    intent=await fetchJSON('/api/activity/intent?instance='+encodeURIComponent(sdk.instanceId));
    if(intent.purpose==='schedule')break;
    if(attempt<9)await new Promise(resolve=>setTimeout(resolve,300));
   }
   if(intent.purpose==='schedule'){await openSchedule(sdk,RPCCloseCodes.CLOSE_NORMAL);return;}
   if(intent.purpose!=='relogin'){say('起動元の記事を確認できません。Discordの記事から予約画面を開き直してください。');$('pair').hidden=true;return;}
   document.querySelector('main').hidden=false;document.querySelector('h1').textContent='noteへ再ログイン';document.title='noteへ再ログイン';
   say('Discordで受け取った接続コードを入力してください。')}
  catch{ $('connect').disabled=true;say('Discordとの接続に失敗しました。起動し直してください。') }
 }else say('単体プレビューです。iPhoneではDiscordの「操作画面を開く」から起動してください。');
}
boot();
