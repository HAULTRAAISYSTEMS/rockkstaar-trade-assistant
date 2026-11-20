/* Mirror existing live pulse values into the 2027 KPI tiles; no second quote feed. */
(function(){
'use strict';
function boot(){
 const pairs=[['cc-spy','te27-spy'],['cc-qqq','te27-qqq'],['cc-vix','te27-vix'],['cc-liq','te27-liq']];
 for(const [sourceId,targetId] of pairs){
  const source=document.getElementById(sourceId),target=document.getElementById(targetId);
  if(!source||!target)continue;
  const sync=()=>{const value=(source.textContent||'').trim();target.textContent=value||'—';target.classList.toggle('is-down',value.startsWith('-'));};
  sync();new MutationObserver(sync).observe(source,{subtree:true,characterData:true,childList:true});
 }
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();