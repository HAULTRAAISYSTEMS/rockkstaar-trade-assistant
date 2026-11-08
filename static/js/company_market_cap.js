(function(){
'use strict';
function formatCap(value,currency){
 if(!Number.isFinite(value)||value<=0||!/^[A-Z]{3}$/.test(currency))return 'Unavailable';
 const amount=new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:2}).format(value);
 return `${currency==='USD'?'$':''}${amount} ${currency}`;
}
if(typeof module!=='undefined'&&module.exports)module.exports={formatCap};
if(typeof document==='undefined')return;
let stopped=false;const timers=new Set();
window.addEventListener('pagehide',()=>{stopped=true;timers.forEach(clearTimeout);});
document.querySelectorAll('[data-market-cap-ticker]').forEach(panel=>{
 const value=panel.querySelector('[data-cap-value]'),band=panel.querySelector('[data-cap-band]'),source=panel.querySelector('[data-cap-source]');
 async function load(attempt=0){
  try{
   const response=await fetch(`/api/company-lab/${encodeURIComponent(panel.dataset.marketCapTicker)}/marketcap`,{cache:'no-store'});
   if(!response.ok)throw Error('unavailable');
   const result=await response.json(),data=result.data||{};
   if(stopped)return;
   if(result.status==='ready'){
    value.textContent=data.available?formatCap(data.value,data.currency):'Unavailable';
    band.textContent=data.size_band||'';band.hidden=!data.available||!data.size_band;
    const stamp=new Date(data.fetched_at||result.fetched_at);
    source.textContent=data.available?`${data.source||'Company profile'} · ${result.stale?'Cached · ':''}Retrieved ${Number.isFinite(stamp.getTime())?stamp.toLocaleString():'time unavailable'}. May be delayed; retrieval time is not a market quote timestamp.`:data.message||'No verified market cap is available.';
    if(!result.refreshing)return;
   }else if(!['loading','busy'].includes(result.status)){throw Error('unavailable');}
   if(attempt<20){const timer=setTimeout(()=>{timers.delete(timer);if(!stopped)load(attempt+1);},2000);timers.add(timer);}
   else if(result.status!=='ready'){value.textContent='Unavailable';source.textContent='This source is taking longer than expected. Reload to retry.';}
  }catch(_){value.textContent='Unavailable';source.textContent='Market cap could not be loaded. Reload to retry.';}
 }
 load();
});
})();
