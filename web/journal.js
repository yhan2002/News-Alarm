"use strict";
// Local pilot: export JSON for backup; shared storage requires an authenticated journal API.
const CSQJournal = (() => {
 const KEY='csq-investment-journal-v1', clone=v=>JSON.parse(JSON.stringify(v));
 function thesis(input){
  const symbol=String(input.symbol||'').trim().toUpperCase(),weight=input.weight===''?null:Number(input.weight);
  if(!/^[A-Z0-9.^-]{1,15}$/.test(symbol))throw Error('티커를 확인하세요.');
  if(weight!==null&&(!Number.isFinite(weight)||weight<0||weight>100))throw Error('보유 비중은 0~100%로 입력하세요.');
  for(const k of ['thesis','watch','invalidate'])if(!String(input[k]||'').trim()||String(input[k]).length>2000)throw Error('투자 논리·관찰 지표·반증 조건을 각각 2,000자 이내로 입력하세요.');
  return {symbol,weight,thesis:input.thesis.trim(),watch:input.watch.trim(),invalidate:input.invalidate.trim()};
 }
 function review(position,article,decision,note,asOf,now=new Date().toISOString()){
  if(!['지지','반박','판단 유보'].includes(decision)||!note.trim()||note.length>3000)throw Error('판단과 근거를 입력하세요.');
  if(!(article.links||[]).some(l=>l.symbol===position.symbol))throw Error('선택한 종목과 연결된 기사가 아닙니다.');
  return clone({id:crypto.randomUUID(),recorded_at:now,data_as_of:asOf,position,decision,note,
   article:{id:article.id,title:article.title,url:article.url,published_at:article.published_at,first_collected_at:article.first_collected_at||null,
    analysis_updated_at:article.analysis_updated_at||article.qwen?.at||null,summary:article.summary,sentiment:article.sentiment,importance:article.importance,
    facts:article.qwen?.facts||null},outcomes:[]});
 }
 function outcome(input,now=new Date().toISOString()){
  const values={};for(const k of ['stock','market','sector','cost']){if(input[k]===''||input[k]==null)throw Error('수익률과 왕복 거래비용을 모두 입력하세요.');values[k]=Number(input[k]);if(!Number.isFinite(values[k])||Math.abs(values[k])>10000)throw Error('유효한 숫자를 입력하세요.');}
  if(values.cost<0||!['1','3','5'].includes(String(input.days)))throw Error('기간 또는 비용을 확인하세요.');
  if(!input.source?.trim()||!input.confounders?.trim())throw Error('가격 출처·측정 구간과 동시 발생 사건을 기록하세요.');
  return {...values,days:Number(input.days),source:input.source,confounders:input.confounders,recorded_at:now,
   market_excess:values.stock-values.market-values.cost,sector_excess:values.stock-values.sector-values.cost};
 }
 return {KEY,thesis,review,outcome};
})();
if(typeof module!=='undefined')module.exports=CSQJournal;
if(typeof document!=='undefined') {
 const dialog=document.createElement('dialog');dialog.id='investment-journal';
 dialog.innerHTML=`<div class="panel-heading"><div><span class="eyebrow">INVESTMENT REVIEW · PILOT</span><h2>투자 논리 점검</h2></div><button id="journal-close" class="quiet">닫기</button></div>
 <p>이 기기·브라우저에만 저장됩니다. 팀 공유 DB와 연결되지 않습니다. JSON을 정기적으로 백업하세요.</p>
 <p id="journal-status" role="status"></p><div class="journal-layout"><section><h3>1. 투자 논리 등록</h3>
 <form id="thesis-form"><label>티커<input name="symbol" required maxlength="15" placeholder="예: MSFT"></label><label>보유 비중 % · 비워두면 미입력<input name="weight" type="number" min="0" max="100" step="any"></label>
 <label>투자 논리<textarea name="thesis" required maxlength="2000" placeholder="어떤 변화로 기업 가치가 높아진다고 보는가?"></textarea></label>
 <label>핵심 관찰 지표<textarea name="watch" required maxlength="2000" placeholder="예: 클라우드 성장률, 영업이익률"></textarea></label>
 <label>반증 조건<textarea name="invalidate" required maxlength="2000" placeholder="어떤 사실이 확인되면 기존 판단을 재검토할 것인가?"></textarea></label><button class="primary">투자 논리 저장</button></form><div id="journal-positions"></div></section>
 <section><h3>2. 근거 검토</h3><p>현재 화면의 기간·데이터 기준입니다. 중요도는 검토 순서이며 매수 신호가 아닙니다. 센티는 기사 전체의 논조입니다.</p><label>검토 종목<select id="journal-symbol"></select></label><div id="journal-thesis"></div><label>관련 기사<select id="journal-article"></select></label><div id="journal-evidence"></div>
 <form id="review-form"><label>기존 투자 논리에 대한 판단<select name="decision"><option>판단 유보</option><option>지지</option><option>반박</option></select></label><label>판단 근거·새로운 사실·기대 대비 변화<textarea name="note" required maxlength="3000" placeholder="출처가 없는 예상치·컨센서스는 미확인으로 남겨주세요."></textarea></label><button class="primary">현재 근거로 판단 확정 기록</button></form></section></div>
 <section><h3>3. 판단 기록과 사후 검증</h3><p>확정 기록은 덮어쓰지 않습니다. 수익률은 기록 이후 실제 거래 가능 시점부터 측정하세요. 초과수익은 뉴스의 인과 효과를 증명하지 않습니다.</p><button id="journal-export" class="quiet">JSON 백업 다운로드</button><div id="journal-history"></div>
 <form id="outcome-form"><label>검증할 판단<select name="review" id="outcome-review"></select></label><label>거래일<select name="days"><option>1</option><option>3</option><option>5</option></select></label><div class="journal-metrics"><label>종목 수익률 %<input name="stock" type="number" step="any" required></label><label>시장 수익률 %<input name="market" type="number" step="any" required></label><label>섹터 수익률 %<input name="sector" type="number" step="any" required></label><label>왕복 거래비용 %<input name="cost" type="number" step="any" min="0" required></label></div><label>가격 출처·진입/종료 시각·벤치마크<textarea name="source" required maxlength="2000"></textarea></label><label>다른 공시·매크로 등 동시 발생 사건<textarea name="confounders" required maxlength="2000" placeholder="없음 또는 확인하지 못함도 명시하세요."></textarea></label><button class="primary">사후 결과 추가</button></form></section>`;
 document.body.append(dialog);
 const button=document.createElement('button');button.className='nav-item';button.textContent='◉ 투자 논리 점검';document.querySelector('nav').append(button);
 const q=s=>dialog.querySelector(s),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let book,available=[],selected,evidenceAsOf;
 function status(message){q('#journal-status').textContent=message;}
 function persist(next){localStorage.setItem(CSQJournal.KEY,JSON.stringify(next));book=next;}
 function feed(){return typeof data!=='undefined'?data:null;}
 function evidence(){selected=available.find(a=>a.id===q('#journal-article').value);const a=selected;if(!a){q('#journal-evidence').textContent='현재 기간에 연결된 기사가 없습니다.';return;}
  let url='';try{const u=new URL(a.url);if(['https:','http:'].includes(u.protocol)&&!u.username&&!u.password)url=u.href;}catch{}
  q('#journal-evidence').innerHTML=`<h4>${esc(a.title)}</h4><p>${esc(a.summary||'요약 없음')}</p><p>중요도 ${esc(a.importance?.score??'미평가')} · 센티 ${esc(a.sentiment?.score??'미평가')} · ${esc(a.sentiment?.label||'')}</p><p>발표 ${esc(a.published_at)} · 분석 ${esc(a.analysis_updated_at||a.qwen?.at||'미확인')}</p>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">원문 확인 ↗</a>`:''}<details><summary>저장된 API 분석 근거</summary><pre>${esc(JSON.stringify(a.qwen?.facts||{status:'본문 분석 근거 없음'},null,2))}</pre></details>`;
 }
 function articles(){evidenceAsOf=feed()?.as_of;const symbol=q('#journal-symbol').value,p=book.positions.find(p=>p.symbol===symbol);q('#journal-thesis').textContent=p?`${p.thesis} / 관찰: ${p.watch} / 반증: ${p.invalidate}`:'투자 논리를 먼저 등록하세요.';
  available=(feed()?.articles||[]).filter(a=>(a.links||[]).some(l=>l.symbol===symbol)).sort((a,b)=>(b.importance?.score||0)-(a.importance?.score||0)||b.published_at.localeCompare(a.published_at));
  q('#journal-article').innerHTML=available.map(a=>`<option value="${esc(a.id)}">${esc(a.title)}</option>`).join('');evidence();
 }
 function render(){const previous=q('#journal-symbol').value;q('#journal-symbol').innerHTML=book.positions.map(p=>`<option>${esc(p.symbol)}</option>`).join('');if(book.positions.some(p=>p.symbol===previous))q('#journal-symbol').value=previous;
  q('#journal-positions').textContent=book.positions.map(p=>`${p.symbol} ${p.weight==null?'비중 미입력':p.weight+'%'}`).join(' · ');
  q('#outcome-review').innerHTML=book.reviews.map(r=>`<option value="${esc(r.id)}">${esc(r.position.symbol+' / '+r.recorded_at+' / '+r.decision)}</option>`).join('');
  q('#journal-history').innerHTML=[...book.reviews].reverse().map(r=>`<article><strong>${esc(r.position.symbol)} · ${esc(r.decision)}</strong><p>${esc(r.recorded_at)} · ${esc(r.article.title)}</p><p>${esc(r.note)}</p>${r.outcomes.map(o=>`<p>${o.days}일 · 비용 차감 시장 대비 ${o.market_excess.toFixed(2)}%p / 섹터 대비 ${o.sector_excess.toFixed(2)}%p<br>${esc(o.source)} · ${esc(o.confounders)}</p>`).join('')}</article>`).join('')||'<p>확정된 판단이 없습니다.</p>';articles();
 }
 button.onclick=()=>{dialog.showModal();try{book=JSON.parse(localStorage.getItem(CSQJournal.KEY)||'{"version":1,"positions":[],"reviews":[]}');if(book.version!==1||!Array.isArray(book.positions)||!Array.isArray(book.reviews))throw Error('저장 형식 오류');render();status('판단은 사람이 기록합니다. 등록 정보와 확정 기록을 구분해 보관합니다.');}catch(e){book=null;status('저장소를 읽지 못했습니다. 기존 데이터를 덮어쓰지 않습니다. '+e.message);}};
 q('#journal-close').onclick=()=>dialog.close();q('#journal-symbol').onchange=articles;q('#journal-article').onchange=evidence;
 function submit(id,action){q(id).onsubmit=e=>{e.preventDefault();try{if(!book)throw Error('저장소를 먼저 확인하세요.');const next=JSON.parse(JSON.stringify(book));action(Object.fromEntries(new FormData(e.target)),next);persist(next);render();e.target.reset();status('저장 완료');}catch(error){status('저장 실패: '+error.message);}};}
 submit('#thesis-form',(input,next)=>{const p=CSQJournal.thesis(input);if(!(feed()?.stocks||[]).some(s=>s.symbol===p.symbol))throw Error('현재 유니버스에 등록된 종목을 입력하세요.');next.positions=next.positions.filter(v=>v.symbol!==p.symbol);next.positions.push(p);});
 submit('#review-form',(input,next)=>{if(feed()?.mode!=='saved')throw Error('저장 뉴스에서만 판단을 기록할 수 있습니다.');const p=book.positions.find(v=>v.symbol===q('#journal-symbol').value);if(!p||!selected)throw Error('종목과 기사를 선택하세요.');next.reviews.push(CSQJournal.review(p,selected,input.decision,input.note,evidenceAsOf));});
 submit('#outcome-form',(input,next)=>{const r=next.reviews.find(r=>r.id===input.review);if(!r)throw Error('판단 기록을 선택하세요.');r.outcomes.push(CSQJournal.outcome(input));});
 q('#journal-export').onclick=()=>{if(!book)return;const url=URL.createObjectURL(new Blob([JSON.stringify(book,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='csq-investment-journal-'+new Date().toISOString().slice(0,10)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
}
