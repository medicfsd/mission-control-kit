/* Mission Control: Done buttons (quick closeout) + "Go to approval". Used by index.html (main and per-bot views).
   POST ./api/closeout (behind the same sign-in) queues {job, owner, answer, requires_answer} on the server;
   the box pulls the queue (watch_log.py), logs the item done, and the next data refresh shows it green.
   Needs-owner (red) items: ask=approval -> Approve/Deny required (+ optional note) and a "Go to approval" button;
   ask=question -> a typed answer is required. Other items: confirm + optional note. */
(function(){
const API=location.pathname.replace(/[^/]*$/,'')+'api/';
const LS='tmc_closeouts';
const e=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const key=(o,j)=>o+'\u0001'+String(j).toLowerCase().slice(0,80);
let srv=[],reg=[],cfg={},anyPending=false,pollT=null,pollUntil=0;
const local=()=>{try{const a=JSON.parse(localStorage.getItem(LS)||'[]');return a.filter(x=>Date.now()-new Date(x.ts)<3*3600e3)}catch(_){return[]}};
const saveLocal=a=>{try{localStorage.setItem(LS,JSON.stringify(a.slice(-100)))}catch(_){}};
function sentFor(j){ // newest closeout for this job sent at/after the job's latest log entry
  const k=key(j.owner,j.job),t=new Date(j.ts).getTime()-1000;let best=null;
  for(const c of [...srv,...local(),...((cfg.data&&cfg.data().closeouts)||[])])
    if(key(c.owner,c.job)===k&&new Date(c.ts).getTime()>=t&&(!best||c.ts>best.ts))best=c;
  return best}
const needsAnswer=j=>j.cat==='red';
const isApproval=j=>j.cat==='red'&&j.ask==='approval';
const t12=iso=>new Date(iso).toLocaleString('en-US',{timeZone:(typeof AZ!=='undefined'?AZ:'UTC'),hour:'numeric',minute:'2-digit'});
const CO={
 init(o){cfg=o;CO.fetch()},
 async fetch(){try{const r=await fetch(API+'closeouts?_='+Date.now(),{cache:'no-store',credentials:'same-origin'});if(r.ok){srv=(await r.json()).closeouts||[];cfg.rerender&&cfg.rerender()}}catch(_){}},
 reset(){reg=[];anyPending=false},
 pending:j=>j&&j.state!=='done'&&!!sentFor(j),
 /* buttons for one open job: opts.label (e.g. owner name when an item has several owners), opts.gb (Open in Grok Bot html for non-approval items) */
 btns(j,opts={}){
  if(!j||j.state==='done')return'';
  const s=sentFor(j);
  if(s){anyPending=true;return `<span class="co-closing" title="Sent ${e(t12(s.ts))} ${e(typeof TZL!=='undefined'?TZL:'')}; turns green after the next refresh">⏳ Closing…${opts.label?' ('+e(opts.label)+')':''}</span>`}
  const i=reg.push(j)-1,h=[];
  if(isApproval(j))h.push(`<a class="co-appr" href="${e(cfg.dl(j.owner))}" onclick="event.stopPropagation()">✅ Go to approval</a>`);
  h.push(`<button type="button" class="co-done${needsAnswer(j)?' need':''}" onclick="event.stopPropagation();CO.open(${i})">✓ Done${opts.label?' · '+e(opts.label):''}</button>`);
  if(!isApproval(j)&&opts.gb)h.push(opts.gb);
  (j.urls||[]).slice(0,2).forEach((u,n)=>h.push(`<a class="co-view" href="${e(u)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">View item${n?' '+(n+1):''} ↗</a>`));
  return `<div class="co-acts">${h.join('')}</div>`},
 after(load){ // while something is "Closing…", re-check every 30s (max 10 min), then fall back to the page's normal timer
  clearTimeout(pollT);if(!anyPending)return;if(!pollUntil)pollUntil=Date.now()+600e3;
  if(Date.now()<pollUntil)pollT=setTimeout(()=>{CO.fetch();load()},30000);else pollUntil=0},
 open(i){const j=reg[i];if(!j)return;const ap=isApproval(j),q=needsAnswer(j)&&!ap;
  const m=document.createElement('div');m.className='co-modal';m.innerHTML=`<div class="co-sheet" role="dialog" aria-modal="true" aria-label="Close out item">
   <div class="co-t">${ap?'Approve or deny, then close':q?'Answer to close this item':'Mark this item done?'}</div>
   <div class="co-j">${e(j.job)}</div><div class="co-o">${e(j.owner)}${j.why?' · '+e(j.why):''}</div>
   ${ap?`<div class="co-l">Your answer / decision <b>(required)</b></div><div class="co-ch"><button type="button" data-v="Approved">👍 Approve</button><button type="button" data-v="Denied">👎 Deny</button></div>
     <label class="co-l" for="co-n">Note (optional)</label><textarea id="co-n" maxlength="1800" rows="2" placeholder="Anything the bot should know"></textarea>`
    :q?`<label class="co-l" for="co-n">Your answer / decision <b>(required)</b></label><textarea id="co-n" maxlength="1990" rows="4" placeholder="Type your answer. ${e(j.owner)} gets it and acts on it."></textarea>`
    :`<label class="co-l" for="co-n">Note (optional)</label><textarea id="co-n" maxlength="1990" rows="2" placeholder="Optional note for ${e(j.owner)}"></textarea>`}
   <div class="co-err" role="alert"></div>
   <div class="co-btns"><button type="button" class="co-cancel">Cancel</button><button type="button" class="co-go" ${needsAnswer(j)?'disabled':''}>${ap?'Send & close':q?'Send answer & close':'Mark done'}</button></div></div>`;
  document.body.appendChild(m);document.body.classList.add('co-lock');
  const ta=m.querySelector('textarea'),go=m.querySelector('.co-go'),er=m.querySelector('.co-err');let choice='';
  const val=()=>ap?(choice?(choice+(ta.value.trim()?': '+ta.value.trim():'')):''):ta.value.trim();
  const upd=()=>{go.disabled=needsAnswer(j)&&!val()};
  ta.addEventListener('input',upd);
  m.querySelectorAll('.co-ch button').forEach(b=>b.onclick=()=>{choice=b.dataset.v;m.querySelectorAll('.co-ch button').forEach(x=>x.classList.toggle('on',x===b));upd()});
  const close=()=>{m.remove();document.body.classList.remove('co-lock')};
  m.querySelector('.co-cancel').onclick=close;m.onclick=ev=>{if(ev.target===m)close()};
  go.onclick=async()=>{const answer=val();if(needsAnswer(j)&&!answer){upd();return}
   go.disabled=true;go.textContent='Sending…';er.textContent='';
   try{const r=await fetch(API+'closeout',{method:'POST',credentials:'same-origin',cache:'no-store',
     headers:{'Content-Type':'application/json','X-Dashboard':'1'},
     body:JSON.stringify({job:j.job,owner:j.owner,answer,requires_answer:needsAnswer(j)})});
    const b=await r.json().catch(()=>({}));if(!r.ok)throw new Error(b.error||('HTTP '+r.status));
    const a=local();a.push({id:b.id,ts:b.ts||new Date().toISOString(),owner:j.owner,job:j.job});saveLocal(a);
    close();pollUntil=0;cfg.rerender&&cfg.rerender()}
   catch(x){er.textContent='Not sent: '+x.message;go.disabled=false;go.textContent='Try again'}};
  setTimeout(()=>{if(!ap)ta.focus()},50)}
};
window.CO=CO;
const st=document.createElement('style');st.textContent=`
.co-acts{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;align-items:center}
.co-done,.co-appr,.co-view{font:600 13px/1 -apple-system,BlinkMacSystemFont,sans-serif;border-radius:10px;padding:9px 12px;min-height:36px;display:inline-flex;align-items:center;gap:4px;text-decoration:none;cursor:pointer;-webkit-tap-highlight-color:transparent}
.co-done{background:#22c55e1f;color:#86efac;border:1px solid #22c55e88}.co-done.need{background:#22c55e14}
.co-appr{background:linear-gradient(180deg,#ef4444,#dc2626);color:#fff;border:1px solid #fca5a5;box-shadow:0 4px 14px -6px #ef4444}
.co-view{background:transparent;color:#c4b5fd;border:1px dashed #8b5cf688;padding:7px 10px;min-height:32px;font-size:12px}
.co-closing{display:inline-block;margin-top:6px;font-size:12.5px;font-weight:600;color:#fde68a;background:#facc151a;border:1px solid #facc1566;border-radius:10px;padding:6px 10px}
.co-modal{position:fixed;inset:0;background:#000a;z-index:1000;display:flex;align-items:flex-end;justify-content:center}
.co-sheet{background:#17142d;border:1px solid #3b3566;border-radius:18px 18px 0 0;width:100%;max-width:520px;padding:16px 16px max(16px,env(safe-area-inset-bottom));color:#ece9ff;box-shadow:0 -10px 40px #0008}
@media(min-width:700px){.co-modal{align-items:center}.co-sheet{border-radius:18px}}
.co-t{font-weight:700;font-size:16px;margin-bottom:6px}.co-j{font-size:14px;font-weight:600}.co-o{color:#9a94c4;font-size:12px;margin:2px 0 10px}
.co-l{display:block;font-size:12px;color:#c4b5fd;margin:8px 0 4px}
.co-sheet textarea{width:100%;box-sizing:border-box;background:#0f0d1d;color:#ece9ff;border:1px solid #3b3566;border-radius:10px;padding:10px;font:16px/1.35 -apple-system,BlinkMacSystemFont,sans-serif;resize:vertical}
.co-ch{display:flex;gap:8px}.co-ch button{flex:1;min-height:44px;border-radius:12px;border:1px solid #3b3566;background:#0f0d1d;color:#ece9ff;font:600 15px -apple-system,sans-serif}
.co-ch button.on[data-v=Approved]{background:#22c55e;color:#0b0a14;border-color:#22c55e}.co-ch button.on[data-v=Denied]{background:#ef4444;color:#fff;border-color:#ef4444}
.co-err{color:#fca5a5;font-size:12.5px;min-height:16px;margin-top:6px}
.co-btns{display:flex;gap:8px;margin-top:6px}.co-btns button{flex:1;min-height:46px;border-radius:12px;font:700 15px -apple-system,sans-serif;border:1px solid #3b3566}
.co-cancel{background:transparent;color:#c4b5fd}.co-go{background:#22c55e;color:#0b0a14;border-color:#22c55e!important}.co-go:disabled{background:#2a2645;color:#6f6a96;border-color:#2a2645!important}
body.co-lock{overflow:hidden}`;document.head.appendChild(st);
})();
