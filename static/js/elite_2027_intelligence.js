/* Reusable SEC evidence panel: never infer an investment score from missing data. */
(function(){
'use strict';
const money=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',notation:'compact',maximumFractionDigits:2});
const num=new Intl.NumberFormat('en-US',{maximumFractionDigits:2});
const labels={revenue:'Revenue',net_income:'Net income',operating_cash_flow:'Operating cash flow',free_cash_flow:'Free cash flow',gross_margin_pct:'Gross margin',operating_margin_pct:'Operating margin',net_margin_pct:'Net margin',roic_proxy_pct:'ROIC proxy',identified_debt_components:'Identified debt'};
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function fmt(m){if(!m||m.value==null)return 'Unavailable';if(m.unit==='USD')return money.format(m.value);if(m.unit==='USD/shares')return '$'+num.format(m.value);return num.format(m.value)+(m.unit==='%'?'%':'');}
async function render(root,ticker){
 if(!root||!ticker||!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker))return;
 root.replaceChildren(el('div','Loading verified SEC evidence…','te27-intel__sub'));
 try{
  const r=await fetch('/api/wealth/intelligence-3/'+encodeURIComponent(ticker),{credentials:'same-origin'});
  const d=await r.json();if(!r.ok||!d.contract?.ok)throw Error('Unavailable');
  const c=d.contract,info=d.intelligence||{};
  root.replaceChildren();
  root.append(el('div','TRADESTAAR / FINANCIAL INTELLIGENCE 3.0','te27-intel__eyebrow'));
  root.append(el('h3','Evidence Command · '+ticker,'te27-intel__heading'));
  root.append(el('p','SEC filings · '+(c.retrieved_at||'Retrieval time unavailable')+' · Reported and calculated data are identified separately.','te27-intel__sub'));
  const status=el('span',info.integrity||'unavailable','te27-intel__state');status.dataset.status=info.integrity||'unavailable';root.append(status);
  const grid=el('div',undefined,'te27-intel__grid');
  for(const key of ['revenue','net_income','operating_cash_flow','free_cash_flow','operating_margin_pct','roic_proxy_pct']){
   const m=(c.metrics||{})[key]||(c.derived_metrics||{})[key]||{};
   const tile=el('article',undefined,'te27-intel__tile');
   tile.append(el('strong',labels[key]),el('output',m.status==='reported'||m.status==='calculated'?fmt(m):'Unavailable'));
   tile.append(el('small',(m.period_end?'Period '+m.period_end+' · ':'')+(m.form||m.formula||'No period-compatible evidence')));
   grid.append(tile);
  }
  root.append(grid);
  const list=el('ul',undefined,'te27-intel__list');
  const observations=(info.discover||[]).slice(0,3).map(x=>'Discover: '+x);
  const warnings=(info.verify||[]).slice(0,5).map(x=>(x.type==='risk'?'Risk: ':'Verify: ')+x.text);
  for(const item of [...observations,...warnings])list.append(el('li',item));
  if(!list.children.length)list.append(el('li','No complete financial observations; M.C.F.F. scores remain unchanged.'));
  root.append(list);
 }catch(err){root.replaceChildren(el('div','SEC intelligence unavailable for '+ticker+'. No unverified score will be shown.','te27-intel__sub'));}
}
window.TradestaarElite2027=Object.freeze({render});
function boot(){document.querySelectorAll('[data-te27-ticker]').forEach(root=>render(root,(root.dataset.te27Ticker||'').trim().toUpperCase()));}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();