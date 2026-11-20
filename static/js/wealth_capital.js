/* Capital Deployment Phase 4. Never executes brokerage trades. */
(function(){
'use strict';
const form=document.getElementById('capital-form');if(!form)return;
const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=n=>n==null?'—':Number(n).toLocaleString('en-US',{style:'currency',currency:'USD'});
let latest=null;
$('capital-add-button').addEventListener('click',()=>{
 const input=$('capital-add'),t=input.value.trim().toUpperCase();
 if(!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(t))return;
 const existing=[...form.querySelectorAll('input[name=candidate]')];
 if(existing.some(x=>x.value===t)||existing.length>=12)return;
 const label=document.createElement('label');label.style.color='#d4e7ef';
 const cb=document.createElement('input');cb.type='checkbox';cb.name='candidate';cb.value=t;cb.checked=true;
 label.append(cb,document.createTextNode(t));existing[0]?.parentElement?.parentElement?.append(label);input.value='';
});
form.addEventListener('submit',async e=>{
 e.preventDefault();
 const status=$('capital-status');status.textContent='Analyzing connected holdings and M.C.F.F. research…';
 const tickers=[...form.querySelectorAll('input[name=candidate]:checked')].map(x=>x.value).slice(0,12);
 const data={amount:form.elements.amount.value,position_limit:form.elements.position_limit.value,
 sector_limit:form.elements.sector_limit.value,cash_reserve:form.elements.cash_reserve.value,
 existing_cash:form.elements.existing_cash.checked,tickers};
 try{
 const r=await WA.api('/api/wealth/capital/rank','POST',data);
 if(!r.ok){status.textContent=r.error||'Analysis unavailable';return}
 latest=r;status.textContent=r.verdict;
 $('capital-results').innerHTML='<p class="lab-muted">Portfolio value: '+money(r.portfolio_value)+' · '+(r.assumed_new_money?'New contribution':'Existing cash')+'</p>'+
 (r.warnings||[]).map(x=>'<p class="lab-negative">'+esc(x)+'</p>').join('')+
 r.rows.map(x=>'<article class="lab-comparison" style="margin:10px 0"><div style="display:flex;justify-content:space-between;gap:12px"><b>'+esc(x.ticker)+'</b><b>'+ (x.score==null?'Unscored':x.score+'/100')+'</b></div>'+
 '<p>'+esc(x.score_label||'Research score')+' · '+esc(x.status||'Unverified')+' · '+(x.eligible?'ELIGIBLE':'NOT ELIGIBLE')+' · Evidence coverage '+x.coverage_pct+'%</p>'+
 '<p>Position weight: '+(x.weight_before_pct==null?'Unknown':x.weight_before_pct+'%')+' → '+(x.weight_after_pct==null?'Unknown':x.weight_after_pct+'%')+' · Sector after: '+(x.sector_after_pct==null?'Unknown':x.sector_after_pct+'%')+'</p>'+
 '<p>Valuation: '+(x.valuation_verified?esc(x.zone||'Not available'):'UNVERIFIED — open M.C.F.F. to verify valuation')+' · Quote date: '+esc(x.valuation_as_of||'unknown')+'</p>'+ '<p>Maximum deployment within configured concentration limits: '+money(x.max_allocation)+'</p>'+
 '<p class="lab-muted">Moat '+(x.components.quality??'—')+' · Financials '+(x.components.financials??'—')+' · Growth '+(x.components.growth??'—')+' · Valuation '+(x.components.valuation??'—')+' · Diversification '+(x.components.diversification??'—')+'</p>'+
 '<details><summary style="cursor:pointer;color:#ff9da4">'+(x.blocks||[]).length+' eligibility checks need attention</summary>'+(x.blocks||[]).map(b=>'<p class="lab-negative">'+esc(b)+'</p>').join('')+'</details>'+
 '<button class="lab-button" type="button" data-journal-ticker="'+esc(x.ticker)+'">Journal this candidate</button></article>').join('');
 const exposure=r.exposure||{};
 const section=document.createElement('section');section.className='lab-comparison';section.style.margin='16px 0';
 const heading=document.createElement('h3');heading.textContent='Portfolio Intelligence 2.0';section.append(heading);
 const description=document.createElement('p');description.className='lab-muted';description.textContent='Verified sector coverage: '+(exposure.coverage_pct??0)+'%. Unverified ETFs and industries are not assigned to a sector.';section.append(description);
 (exposure.holdings||[]).forEach(h=>{const line=document.createElement('div');line.style.margin='8px 0';const label=document.createElement('span');label.textContent=h.ticker+' · '+(h.weight_pct??'—')+'% · '+(h.industry||'Industry unverified');line.append(label);const track=document.createElement('div');track.style.cssText='height:7px;background:#183543;border-radius:9px;overflow:hidden;margin-top:4px';const fill=document.createElement('div');fill.style.cssText='height:100%;background:#68dce7;border-radius:9px;width:'+Math.max(0,Math.min(100,h.weight_pct||0))+'%';track.append(fill);line.append(track);section.append(line)});
 const plans=document.createElement('h4');plans.textContent='Capital deployment alternatives';section.append(plans);
 (r.allocation_plans||[]).forEach(p=>{const el=document.createElement('p');el.textContent=p.name+': '+Object.entries(p.allocations||{}).map(([t,a])=>t+' '+money(a)).join(' + ')+(Object.keys(p.allocations||{}).length?' · ':'')+'Cash remaining '+money(p.cash_remaining);section.append(el)});
 const look=r.etf_lookthrough||{};
 const lt=document.createElement('details');const ltTitle=document.createElement('summary');
 ltTitle.textContent='ETF holdings look-through · '+(look.portfolio_lookthrough_coverage_pct||0)+'% of portfolio covered';lt.append(ltTitle);
 const ltNote=document.createElement('p');ltNote.className='lab-muted';ltNote.textContent=look.configured?'Dated issuer/provider holdings snapshots; not a live feed.':'No verified ETF holdings source configured. Sector exposures remain unverified.';lt.append(ltNote);
 (look.funds||[]).forEach(f=>{const line=document.createElement('p');line.textContent=f.ticker+' · '+f.source+' · '+f.as_of+' · '+f.coverage_pct+'% of fund holdings covered';lt.append(line)});
 Object.entries(look.sector_exposure_pct||{}).forEach(([sector,pct])=>{const line=document.createElement('p');line.textContent=sector+': '+pct+'% of whole portfolio (verified ETF portion only)';lt.append(line)});
 (look.identity_warnings||[]).forEach(w=>{const p=document.createElement('p');p.textContent='Identity check: '+w;lt.append(p)});
 (look.overlap||[]).forEach(o=>{const p=document.createElement('p');p.textContent='Overlap '+o.ticker+': '+money(o.direct_value)+' direct + '+money(o.indirect_value)+' via ETFs ('+o.combined_weight_pct+'% combined)';lt.append(p)});
 Object.entries(look.feed_errors||{}).forEach(([t,e])=>{const p=document.createElement('p');p.textContent=t+' issuer feed unavailable: '+e;lt.append(p)});
 const refresh=document.createElement('p');refresh.textContent=look.refresh_policy||'Issuer refresh status unavailable';lt.append(refresh);
 Object.entries(look.feed_statuses||{}).forEach(([t,status])=>{const p=document.createElement('p');p.textContent=t+': '+status.replaceAll('_',' ');lt.append(p)});
 const spcx=r.security_identity&&r.security_identity.SPCX;
 if(spcx){const p=document.createElement('p');p.textContent='SPCX · '+spcx.issuer+' · '+spcx.security+' · '+(spcx.verified?'Broker CUSIP verified':'Broker CUSIP not verified');lt.append(p)}
 section.append(lt);
 const monitor=document.createElement('details');const title=document.createElement('summary');title.textContent='Data reliability monitor · '+(r.data_quality||[]).filter(c=>c.status!=='current').length+' items need review';monitor.append(title);
 (r.data_quality||[]).forEach(c=>{const el=document.createElement('p');el.className='lab-muted';el.textContent=(c.ticker?c.ticker+' · ':'')+c.category+': '+c.detail;monitor.append(el)});section.append(monitor);
 $('capital-results').prepend(section);
 $('capital-results').querySelectorAll('[data-journal-ticker]').forEach(b=>b.addEventListener('click',()=>{
 $('capital-journal').elements.ticker.value=b.dataset.journalTicker;
 $('capital-journal').elements.amount.value=r.amount;
 $('capital-journal').scrollIntoView({behavior:'smooth',block:'center'});
 }));
 }catch(err){status.textContent='Analysis temporarily unavailable.'}
});
async function loadJournal(){
 try{const r=await WA.api('/api/wealth/capital/journal');if(!r.ok)return;
 $('journal-list').innerHTML=(r.decisions||[]).map(x=>'<article class="lab-comparison" style="margin:10px 0"><b>'+esc(x.ticker)+' · '+esc(x.decision)+' · '+money(x.amount)+'</b><p>'+esc(x.thesis)+'</p><p class="lab-muted">Risk: '+esc(x.risk)+' · Invalidation: '+esc(x.invalidation)+'</p><small>Recorded '+esc(x.created_at)+' · Review '+esc(x.review_date||'not scheduled')+'</small></article>').join('');
 }catch(err){$('journal-status').textContent='Journal temporarily unavailable.'}
}
$('capital-journal').addEventListener('submit',async e=>{
 e.preventDefault();const f=e.target,data=Object.fromEntries(new FormData(f));
 const row=latest?.rows?.find(x=>x.ticker===data.ticker.toUpperCase());
 data.evidence={mode:'balanced',analysis_at:new Date().toISOString(),candidate:row||null,
 portfolio_value:latest?.portfolio_value??null,assumed_new_money:latest?.assumed_new_money??null};
 try{const r=await WA.api('/api/wealth/capital/journal','POST',data);
 $('journal-status').textContent=r.ok?'Decision recorded. No trade placed.':r.error||'Save failed';
 if(r.ok)loadJournal()}catch(err){$('journal-status').textContent='Unable to save decision.'}
});
const secButton=$('sec-dashboard-load');
if(secButton)secButton.addEventListener('click',async()=>{
 const status=$('sec-dashboard-status'),root=$('sec-dashboard-results');
 status.textContent='Loading SEC evidence…';root.replaceChildren();secButton.disabled=true;
 try{
  const response=await fetch('/api/wealth/sec-dashboard/SPCX',{credentials:'same-origin'});
  if(!response.ok)throw new Error('SEC endpoint unavailable');
  const data=await response.json();
  status.textContent='SEC evidence: '+(data.status||'unavailable')+' · '+(data.source||'')+'. Valuation scoring is disabled.';
  Object.entries(data.metrics||{}).forEach(([name,metric])=>{
   const card=document.createElement('article');card.className='lab-comparison';
   const h=document.createElement('h4');h.textContent=name.replaceAll('_',' ');card.append(h);
   const p=document.createElement('p');const v=metric.latest;
   p.textContent=v?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(v.value):'Not reported';card.append(p);
   const source=document.createElement('small');
   source.textContent=v?'Period end '+v.period_end+' · Filed '+v.filed+' · '+v.form+' · '+v.tag:'No verified XBRL figure available';
   card.append(source);
   if(metric.balance_sheet_change_pct!==null&&metric.balance_sheet_change_pct!==undefined){
    const change=document.createElement('p');change.textContent='Change versus previous reported balance: '+metric.balance_sheet_change_pct+'%';card.append(change);
   }
   root.append(card);
  });
  if(!Object.keys(data.metrics||{}).length)status.textContent+=' No structured financial facts available.';
  const filings=document.createElement('div');filings.className='lab-comparison';
  const heading=document.createElement('h4');heading.textContent='Latest SEC filings';filings.append(heading);
  (data.filings||[]).slice(0,8).forEach(row=>{
   const p=document.createElement('p'),a=document.createElement('a');
   a.href=row.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=row.form+' · '+row.filed;p.append(a);filings.append(p);
  });root.append(filings);
 }catch(e){status.textContent='SEC evidence could not be loaded. No figures were inferred.'}
 finally{secButton.disabled=false}
});
const explainButton=$('sec-explanations-load');
if(explainButton)explainButton.addEventListener('click',async()=>{
 const status=$('sec-explanations-status'),root=$('sec-explanations-results');
 status.textContent='Comparing SEC filing evidence…';root.replaceChildren();explainButton.disabled=true;
 try{
  const response=await fetch('/api/wealth/sec-explanations/SPCX',{credentials:'same-origin'});
  if(!response.ok)throw new Error('Unavailable');
  const data=await response.json();
  status.textContent=(data.explanations||[]).length?'Evidence-based comparisons · not investment recommendations':'No comparable SEC facts available';
  (data.explanations||[]).forEach(item=>{
   const article=document.createElement('article');article.className='lab-comparison';
   const h=document.createElement('h4');h.textContent=item.metric.replaceAll('_',' ');article.append(h);
   const p=document.createElement('p');p.textContent=item.text;article.append(p);
   if(item.reason){const note=document.createElement('small');note.textContent=item.reason;article.append(note)}
   (item.evidence||[]).forEach(e=>{
    const note=document.createElement('small');note.style.display='block';
    note.textContent='SEC '+e.form+' · filed '+e.filed+' · period '+e.period_end+' · '+e.tag+' · '+(e.accession||'accession unavailable');
    article.append(note);
   });root.append(article);
  });
 }catch(err){status.textContent='Unable to retrieve SEC comparisons; no explanations inferred.'}
 finally{explainButton.disabled=false}
});
loadJournal();
})();
