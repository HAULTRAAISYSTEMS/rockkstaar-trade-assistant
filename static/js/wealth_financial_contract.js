/* Shared SEC financial evidence panel for Tradestaar research surfaces.
 * All panels read the same versioned financial-contract API.
 */
(function () {
  'use strict';
  const names = ['revenue','net_income','operating_cash_flow','assets','liabilities'];
  const money = new Intl.NumberFormat('en-US', {style:'currency',currency:'USD',maximumFractionDigits:0});
  function node(tag, text, cls) {
    const el=document.createElement(tag);
    if(text!==undefined) el.textContent=String(text);
    if(cls) el.className=cls;
    return el;
  }
  async function render(root, ticker) {
    if(!root || !ticker)return;
    root.replaceChildren(node('p','Checking verified SEC financial evidence…','wa-note'));
    let data;
    try {
      const response=await fetch('/api/wealth/financial-contract/'+encodeURIComponent(ticker.toUpperCase()),{credentials:'same-origin'});
      data=await response.json();
      if(!response.ok || !data.ok)throw new Error(data.error||'No verified SEC issuer mapping');
    } catch(err) {
      root.replaceChildren(node('p','Verified SEC financial data unavailable for '+ticker.toUpperCase()+'. Existing research remains accessible.','wa-note'));
      return;
    }
    root.replaceChildren();
    const heading=node('h3','SEC Financial Evidence · '+data.ticker);
    const provenance=node('p','Source: '+(data.source||'SEC EDGAR')+' · retrieved '+(data.retrieved_at||'unknown')+' · schema '+data.schema_version,'wa-note');
    root.append(heading,provenance);
    const grid=node('div',undefined,'wa-financial-evidence-grid');
    for(const key of names){
      const m=(data.metrics||{})[key]||{};
      const card=node('article',undefined,'wa-financial-evidence-card');
      card.append(node('strong',m.label||key.replaceAll('_',' ')));
      card.append(node('p',m.status==='reported'&&m.value!==null?money.format(m.value):'Not verified'));
      if(m.status==='reported'){
        card.append(node('small','Period '+m.period_end+' · Filed '+m.filed+' · '+m.form));
        card.append(node('small','Freshness: '+(m.freshness?.status||'unknown')+' · '+m.tag));
        if(m.warning)card.append(node('small',m.warning));
      } else card.append(node('small','No verified SEC fact available'));
      grid.append(card);
    }
    root.append(grid,node('p','Reported figures are not a valuation or an M.C.F.F. buy signal.','wa-note'));
  }
  function boot(){
    document.querySelectorAll('[data-sec-contract-ticker]').forEach(root=>{
      render(root,root.dataset.secContractTicker);
    });
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);
  else boot();
})();
