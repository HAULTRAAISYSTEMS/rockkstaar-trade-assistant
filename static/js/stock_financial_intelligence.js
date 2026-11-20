/* Source-backed Company Overview intelligence. No inferred valuation or trading signals. */
(function(){
'use strict';
const root=document.getElementById('sd-sec-intelligence');
if(!root)return;
const content=document.getElementById('sd-sec-intelligence-content');
const ticker=(root.dataset.ticker||'').trim().toUpperCase();
if(!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker)){content.textContent='Ticker unavailable';return;}
const number=new Intl.NumberFormat('en-US',{maximumFractionDigits:2});
const usd=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0});
const add=(parent,tag,text,cls)=>{const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;parent.append(el);return el;};
const reported=['revenue','net_income','diluted_eps','operating_cash_flow','capital_expenditures','cash','long_term_debt'];
const derived=['free_cash_flow','gross_margin_pct','operating_margin_pct','net_margin_pct','debt_to_equity','identified_debt_components','roic_proxy_pct'];
const labels={free_cash_flow:'Free cash flow',gross_margin_pct:'Gross margin',operating_margin_pct:'Operating margin',net_margin_pct:'Net margin',debt_to_equity:'Noncurrent debt / equity',identified_debt_components:'Identified debt components',roic_proxy_pct:'ROIC proxy'};
const format=m=>m.value==null?'Not verified':m.unit==='USD'?usd.format(m.value):m.unit==='USD/shares'?'$'+number.format(m.value):number.format(m.value)+(m.unit==='%'?'%':'');
function card(grid,key,m,calculated){
 const el=add(grid,'article','', 'wa-financial-evidence-card');
 add(el,'strong',m.label||labels[key]||key.replaceAll('_',' '));
 add(el,'p',m.status===(calculated?'calculated':'reported')?format(m):'Not verified');
 if(m.period_end)add(el,'small','Period end: '+m.period_end);
 if(m.period_start)add(el,'small','Period start: '+m.period_start);
 if(m.filed)add(el,'small','Filed: '+m.filed+' · '+m.form);
 if(m.freshness?.status)add(el,'small','Freshness: '+m.freshness.status);
 if(m.formula)add(el,'small','Formula: '+m.formula);
 if(m.warning)add(el,'small',m.warning);
 grid.append(el);
}
(async()=>{
 try{
  const response=await fetch('/api/wealth/intelligence-3/'+encodeURIComponent(ticker),{credentials:'same-origin'});
  const data=await response.json();
  if(!response.ok||!data.contract?.ok)throw new Error('Financial evidence unavailable');
  const intel=data.intelligence||{};
  const financial=data.contract;
  data.metrics=financial.metrics;
  data.derived_metrics=financial.derived_metrics;
  data.source=financial.source;
  data.retrieved_at=financial.retrieved_at;
  content.replaceChildren();
  add(content,'p','Source: '+(data.source||'SEC EDGAR')+' · Retrieved: '+(data.retrieved_at||'unknown'),'wa-note');
  add(content,'h4','SEC-reported fundamentals');
  const reportedGrid=add(content,'div','', 'wa-financial-evidence-grid');
  for(const key of reported)card(reportedGrid,key,(data.metrics||{})[key]||{},false);
  add(content,'h4','Calculated financial metrics');
  const derivedGrid=add(content,'div','', 'wa-financial-evidence-grid');
  for(const key of derived)card(derivedGrid,key,(data.derived_metrics||{})[key]||{},true);
  add(content,'h4','Verify · Financial evidence and risks');
  add(content,'p','Integrity: '+(intel.integrity||'unknown')+' · M.C.F.F. supplemental evidence: '+(intel.mcff?.eligible?'eligible':'insufficient'),'wa-note');
  const findings=add(content,'ul','', 'wa-financial-integrity-list');
  for(const item of (intel.verify||[]).slice(0,12))add(findings,'li',(item.type==='risk'?'Risk: ':'Data gap: ')+item.text);
  add(content,'h4','Discover · Verified observations');
  const observations=add(content,'ul','', 'wa-financial-integrity-list');
  for(const item of (intel.discover||[]))add(observations,'li',item);
  if(!(intel.discover||[]).length)add(content,'p','No fully verified financial-quality observations yet.','wa-note');
  add(content,'p','Debt components are not necessarily total debt. ROIC is a proxy, not a full reconciliation. Existing M.C.F.F. scores are unchanged.','wa-note');
 }catch(err){content.textContent='Verified SEC financial intelligence is unavailable for '+ticker+'. Other company research remains accessible.';}
})();
})();
