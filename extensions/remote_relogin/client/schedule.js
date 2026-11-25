export function monthCells(year,month){
 const start=new Date(Date.UTC(year,month-1,1)).getUTCDay();
 const total=new Date(Date.UTC(year,month,0)).getUTCDate();
 return Array.from({length:Math.ceil((start+total)/7)*7},(_,i)=>i>=start&&i<start+total?i-start+1:null);
}
const jstParts=stamp=>{
 const d=new Date(stamp*1000+9*3600000);
 return {year:d.getUTCFullYear(),month:d.getUTCMonth()+1,day:d.getUTCDate(),hour:d.getUTCHours(),minute:d.getUTCMinutes()};
};
export const dueFor=s=>Date.UTC(s.year,s.month-1,s.day,s.hour-9,s.minute)/1000;
export const dateLabel=s=>`${s.year}年${s.month}月${s.day}日 ${String(s.hour).padStart(2,'0')}:${String(s.minute).padStart(2,'0')}（日本時間）`;

export async function openSchedule(sdk,normalCloseCode=1000){
 const main=document.querySelector('main');
 document.querySelector('header').hidden=true;document.title='予約日時 — note管理';main.hidden=false;
 main.replaceChildren();main.className='reservation';
 main.innerHTML=`<h1>予約日時</h1><p id="reservation-title"></p><p id="reservation-status" role="status">Discordの本人認証を確認しています…</p>
 <section id="reservation-form" hidden><div class="month-nav"><button id="month-prev" aria-label="前月">‹</button><h2 id="month-label"></h2><button id="month-next" aria-label="翌月">›</button></div>
 <p id="reservation-count"></p><div class="calendar" id="calendar" role="group" aria-label="日付を選択"></div>
 <div class="time-fields"><label>時<select id="reservation-hour"></select></label><label>分<select id="reservation-minute"></select></label></div>
 <p id="selected-date" class="selected-date" aria-live="polite"></p><button id="reservation-review" class="primary">予約内容を確認</button><button id="reservation-cancel">やめる</button></section>
 <section id="reservation-confirm" hidden><h2>この日時で予約しますか？</h2><p id="confirm-title"></p><p id="confirm-date"></p><button id="reservation-save" class="primary">予約を確定</button><button id="reservation-edit">日時を変更</button><p id="reservation-error" role="alert" hidden></p></section>
 <section id="reservation-result" hidden><p id="result-message"></p><button id="reservation-close">Discordへ戻る</button></section>`;
 const $=id=>document.getElementById(id),say=text=>$('reservation-status').textContent=text;
 let session='',snapshot,selected,shown,busy=false,terminal=false,submitted=false,commitDue=null,pollTimer,heartbeat;
 const api=async(path,options={})=>{
  const response=await fetch('/api/activity/'+path,{cache:'no-store',...options,headers:{'Content-Type':'application/json',...(session?{Authorization:'Bearer '+session}:{}),...options.headers}});
  if(!response.ok)throw Object.assign(Error((await response.text())||'PCへ接続できません。'),{status:response.status});
  return response.json();
 };
 const locked=new Map();
 const lock=value=>{busy=value;if(value){for(const b of main.querySelectorAll('button,select')){locked.set(b,b.disabled);b.disabled=true;}}else{for(const [b,disabled] of locked)if(b.isConnected)b.disabled=disabled;locked.clear();}};
 const selectOptions=(id,count)=>{for(let n=0;n<count;n++){const option=document.createElement('option');option.value=n;option.textContent=String(n).padStart(2,'0');$(id).append(option);}};
 selectOptions('reservation-hour',24);selectOptions('reservation-minute',60);
 function update(){
  $('selected-date').textContent=dateLabel(selected);
  $('reservation-review').disabled=dueFor(selected)<=Date.now()/1000;
  $('reservation-hour').value=selected.hour;$('reservation-minute').value=selected.minute;
 }
 async function renderMonth(){
  const data=await api(`state?year=${shown.year}&month=${shown.month}`);
  $('month-label').textContent=`${shown.year}年${shown.month}月`;
  $('reservation-count').textContent='今月の予約：'+Object.values(data.counts).reduce((a,b)=>a+b,0)+'件';
  const grid=$('calendar');grid.replaceChildren();
  for(const day of ['日','月','火','水','木','金','土']){const label=document.createElement('span');label.className='weekday';label.textContent=day;grid.append(label);}
  const today=jstParts(Date.now()/1000);
  for(const day of monthCells(shown.year,shown.month)){
   if(day===null){const empty=document.createElement('span');empty.className='empty-day';grid.append(empty);continue;}
   const button=document.createElement('button');button.className='calendar-day';
   const chosen=selected.year===shown.year&&selected.month===shown.month&&selected.day===day;
   button.classList.toggle('selected',chosen);button.setAttribute('aria-pressed',String(chosen));
   const count=data.counts[day]||0;button.setAttribute('aria-label',`${shown.month}月${day}日${count?'・予約'+count+'件':''}`);
   const number=document.createElement('span');number.textContent=day;button.append(number);
   if(count){const badge=document.createElement('small');badge.textContent=count+'件';button.append(badge);}
   const d=shown.year*10000+shown.month*100+day;
   button.disabled=d<today.year*10000+today.month*100+today.day;
   if(d===today.year*10000+today.month*100+today.day)button.classList.add('today');
   button.onclick=()=>{selected={...selected,...shown,day};update();for(const other of grid.querySelectorAll('button')){other.classList.remove('selected');other.setAttribute('aria-pressed','false');}button.classList.add('selected');button.setAttribute('aria-pressed','true');};
   grid.append(button);
  }
 }
 async function moveMonth(delta){if(busy)return;lock(true);try{const d=new Date(Date.UTC(shown.year,shown.month-1+delta,1));shown={year:d.getUTCFullYear(),month:d.getUTCMonth()+1};await renderMonth();say('日付と時刻を選んでください。');}catch(e){say(e.message);}finally{lock(false);update();}}
 $('month-prev').onclick=()=>moveMonth(-1);$('month-next').onclick=()=>moveMonth(1);
 for(const [id,key] of [['reservation-hour','hour'],['reservation-minute','minute']])$(id).onchange=()=>{selected[key]=Number($(id).value);update();};
 $('reservation-review').onclick=()=>{if(dueFor(selected)<=Date.now()/1000){say('現在より後の日時を選んでください。');return;}$('confirm-title').textContent=snapshot.title;$('confirm-date').textContent=dateLabel(selected);$('reservation-form').hidden=true;$('reservation-confirm').hidden=false;};
 $('reservation-edit').onclick=()=>{$('reservation-confirm').hidden=true;$('reservation-form').hidden=false;};
 const save=$('reservation-save'),edit=$('reservation-edit'),error=$('reservation-error');
 function showCommitProgress(text){
  save.disabled=true;edit.disabled=true;save.textContent=text;
  save.classList.add('saving');error.hidden=true;say(text);
 }
 function result(text){terminal=true;clearTimeout(pollTimer);clearInterval(heartbeat);$('reservation-form').hidden=true;$('reservation-confirm').hidden=true;$('reservation-result').hidden=false;$('result-message').textContent=text;say('');}
 async function poll(){
  try{
   const data=await api('state',{signal:AbortSignal.timeout(12000)});
   if(data.job&&['done','failed'].includes(data.job.state)){
    result(data.job.result.message+'\nDiscordの管理チャンネルに実行記録と操作メニューを表示します。');return;
   }
   showCommitProgress('予約を処理中…');pollTimer=setTimeout(poll,1000);
  }catch(e){
   if([401,403,410].includes(e.status)){
    result('この画面で予約結果を取得できません。Discordの管理チャンネルで実行記録を確認してください。');return;
   }
   showCommitProgress('接続が戻るのを待っています…');pollTimer=setTimeout(poll,3000);
  }
 }
 save.onclick=async()=>{
  if(busy||submitted)return;
  busy=true;
  commitDue??=dueFor(selected);
  showCommitProgress('予約を送信中…');
  try{
   await api('commit',{method:'POST',body:JSON.stringify({due:commitDue}),signal:AbortSignal.timeout(12000)});
   submitted=true;showCommitProgress('予約を処理中…');await poll();
  }catch(e){
   const rejected=[400,401,403,409,410].includes(e.status);
   if(rejected)commitDue=null;
   save.classList.remove('saving');
   save.textContent=rejected?'予約を確定':'受付結果を再確認';
   save.disabled=false;edit.disabled=!rejected;
   error.textContent=rejected?e.message:'受付を確認できません。同じ日時の受付結果を再確認できます。';
   error.hidden=false;say('');
  }finally{busy=false;}
 };
 $('reservation-cancel').onclick=async()=>{if(busy)return;lock(true);try{await api('cancel',{method:'POST',body:'{}'});result('予約の操作を取り消しました。予約は変更していません。');}catch(e){say(e.message);}finally{lock(false);}};
 const close=$('reservation-close');
 close.onclick=()=>{
  close.disabled=true;close.textContent='Discordへ戻っています…';
  try{sdk.close(normalCloseCode,'予約画面を終了しました');}
  catch{close.textContent='Discordへ戻る';close.disabled=false;say('Discordの「退出」から戻れます。');}
 };
 const abandon=()=>{clearTimeout(pollTimer);clearInterval(heartbeat);if(session&&!terminal&&commitDue===null&&!submitted)fetch('/api/activity/cancel',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json',Authorization:'Bearer '+session},body:'{}'}).catch(()=>{});};
 window.addEventListener('pagehide',abandon,{once:true});
 try{
  const base64url=raw=>btoa(String.fromCharCode(...new Uint8Array(raw))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
  const verifier=base64url(crypto.getRandomValues(new Uint8Array(48)));
  const challenge=base64url(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier)));
  const {code}=await sdk.commands.authorize({client_id:sdk.clientId,response_type:'code',code_challenge:challenge,code_challenge_method:'S256',state:crypto.randomUUID(),prompt:'none',scope:['identify']});
  const auth=await api('auth',{method:'POST',body:JSON.stringify({code,verifier,instance:sdk.instanceId})});session=auth.session;
  await sdk.commands.authenticate({access_token:auth.access_token});
  snapshot=await api('state');$('reservation-title').textContent=snapshot.title;
  heartbeat=setInterval(()=>{if(!terminal&&!submitted&&!busy)api('state').catch(()=>{if(!busy&&!submitted)say('PCへ接続できません。接続が戻ってから確定してください。');});},10000);
  if(snapshot.state==='submitted'){submitted=true;await poll();return;}
  if(snapshot.state==='cancelled'){result('予約の操作は取り消されています。記事から開き直してください。');return;}
  selected=jstParts(snapshot.due&&snapshot.due>snapshot.now?snapshot.due:snapshot.now+3600);shown={year:selected.year,month:selected.month};
  await renderMonth();update();$('reservation-form').hidden=false;say('日付と時刻を選んでください。');
 }catch(e){say('予約画面を開けませんでした。'+e.message);}
}
