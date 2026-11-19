/* Wealth Lab 2027: read-only scenarios; no brokerage orders. */
(function(){
'use strict';
const form=document.getElementById('lab-form'),pick=document.getElementById('lab-pick'),out=document.getElementById('lab-results');
if(!form||!out)return;
const H=JSON.parse(document.getElementById('lab-holdings').textContent||'[]');
const $=id=>document.getElementById(id),money=n=>Number.isFinite(+n)?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(+n):'Unavailable';
const fmt=n=>Number.isFinite(+n)?Number(n).toLocaleString('en-US',{maximumFractionDigits:5}):'—';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const numeric=(name)=>{const v=form.elements[name]?.value;return v===''||v==null?null:Number(v)};
let last=null,requestId=0;
const storeKey='tradestaar.lab.scenarios.v1';
function saved(){try{const a=JSON.parse(localStorage.getItem(storeKey)||'[]');return Array.isArray(a)?a.slice(0,20):[]}catch(e){return []}}
function store(a){try{localStorage.setItem(storeKey,JSON.stringify(a.slice(0,20)));return true}catch(e){return false}}
function selectHolding(i){
 const h=H[+i];if(!h)return;
 form.elements.ticker.value=h.ticker;form.elements.existing_shares.value=h.shares;
 form.elements.existing_avg_cost.value=h.avg_cost??'';
 if(h.price!=null&&Number(h.price)>0){form.elements.current_price.value=(+h.price).toFixed(2);form.elements.purchase_price.value=(+h.price).toFixed(2);const slider=$('lab-price-range');slider.max=Math.max(Number(slider.max),Math.ceil(Number(h.price)*1.5));slider.value=Number(h.price)}else{form.elements.current_price.value='';form.elements.purchase_price.value=''}
 $('lab-provenance').textContent='Position from '+(h.source==='schwab_snapshot'?'Schwab snapshot':'ledger')+' · price from '+(h.price_source||'available data')+(h.price_as_of?' · '+h.price_as_of:'')+'. Broker cost basis may not be tax-lot verified.';
 run();
}
if(pick){pick.addEventListener('change',()=>{if(pick.value!=='')selectHolding(pick.value)});if(pick.value!=='')selectHolding(pick.value)}
function tile(label,value,sub,cls=''){return '<div class="lab-tile"><span>'+esc(label)+'</span><strong class="'+cls+'">'+esc(value)+'</strong><small>'+esc(sub||'')+'</small></div>'}
function chart(r){
 const p=r.old_avg_cost==null?null:Number(r.old_avg_cost),n=Number(r.new_avg_cost),cur=numeric('current_price'),buy=numeric('purchase_price');
 const values=[p,n,cur,buy].filter(v=>v!=null&&Number.isFinite(v)&&v>0);if(!values.length)return '';
 const min=Math.min(...values)*.94,max=Math.max(...values)*1.06,scale=v=>Math.round(35+235*(v-min)/Math.max(.01,max-min));
 const lines=[['Old avg',p,'#f3b967'],['New avg',n,'#c9fa76'],['Market',cur,'#69e0ee'],['Buy',buy,'#b5a4ff']].filter(x=>x[1]!=null&&Number.isFinite(x[1])&&x[1]>0);
 return '<svg class="lab-chart" viewBox="0 0 320 165" role="img" aria-label="Price markers comparing existing average cost, new average cost, current price, and proposed buy price">'+
 '<line x1="35" y1="122" x2="270" y2="122" stroke="#456073"/>'+
 lines.map((x,i)=>'<line x1="'+scale(x[1])+'" y1="25" x2="'+scale(x[1])+'" y2="122" stroke="'+x[2]+'" stroke-width="2" stroke-dasharray="'+(i>1?'5 4':'0')+'"/><circle cx="'+scale(x[1])+'" cy="122" r="4" fill="'+x[2]+'"/>').join('')+
 '<text x="35" y="145" fill="#91a9b8" font-size="11">'+money(min)+'</text><text x="270" y="145" text-anchor="end" fill="#91a9b8" font-size="11">'+money(max)+'</text></svg>'+
 '<div class="lab-chart-key">'+lines.map(x=>'<span><i class="lab-dot" style="background:'+x[2]+'"></i>'+esc(x[0])+': '+money(x[1])+'</span>').join('')+'</div>';
}
function brief(r){
 const old=Number(r.old_avg_cost),avg=Number(r.new_avg_cost),weight=r.weight_after_pct;
 const clauses=['This is a hypothetical purchase, not a trade recommendation.'];
 if(r.old_avg_cost!=null&&Number.isFinite(old)&&old>0)clauses.push(avg<old?'Your break-even cost decreases by '+money(old-avg)+' per share.':avg>old?'Your break-even cost increases by '+money(avg-old)+' per share.':'Your break-even cost is unchanged.');
 if(weight!=null)clauses.push('Your simulated position weight becomes '+Number(weight).toFixed(1)+'%'+(weight>25?', above a 25% concentration watch threshold.':'.'));
 else clauses.push('Portfolio concentration cannot be calculated without a reliable portfolio value.');
 const down=(r.scenarios||[]).find(s=>Number(s.move_pct)===-30);
 if(down)clauses.push('At the modeled −30% price scenario, the whole position would show '+money(down.position_pl)+' unrealized P/L.');
 clauses.push('Verify your investment thesis, broker buying power, share eligibility, and the M.C.F.F. research before making any decision.');
 return clauses.join(' ');
}
function compare(r){
 const amount=numeric('invest_amount')||0,buy=numeric('purchase_price'),cur=numeric('current_price');
 const h=H.find(x=>x.ticker.toUpperCase()===(form.elements.ticker.value||'').toUpperCase());
 const alternatives=H.filter(x=>x!==h&&Number(x.price)>0).slice(0,3);
 const box=$('lab-compare');box.replaceChildren();
 if(!alternatives.length){box.textContent='Connect more priced holdings to compare where the same capital could go.';return}
 const cards=alternatives.map(x=>{
 const shares=amount/Number(x.price),basis=Number(x.avg_cost),oldShares=Number(x.shares);
 const nextAvg=x.avg_cost!=null&&Number.isFinite(basis)&&basis>0?(oldShares*basis+amount)/(oldShares+shares):null;
 return '<div class="lab-comparison"><b>'+esc(x.ticker)+'</b><p>'+fmt(shares)+' additional shares at '+money(x.price)+'</p><p>Hypothetical new average: '+(nextAvg==null?'Unknown':money(nextAvg))+'</p><small>Same '+money(amount)+' budget. Different company and risk.</small></div>';
 });
 box.innerHTML=cards.join('');
}
async function run(){
 const d=Object.fromEntries(new FormData(form));d.fractional=form.elements.fractional.checked;
 if(!d.purchase_price||!d.invest_amount||!d.ticker){out.textContent='Select a holding, enter a buy price and an investment amount to begin.';last=null;$('lab-kpis').replaceChildren();$('lab-chart-wrap').replaceChildren();$('lab-scenarios').replaceChildren();return}
 if(Number(d.existing_shares)>0&&(!d.existing_avg_cost||Number(d.existing_avg_cost)<=0)){out.textContent='Average cost is unavailable for this position. Enter your verified Schwab average cost before modeling existing-position profit and loss.';$('lab-kpis').replaceChildren();$('lab-chart-wrap').replaceChildren();$('lab-scenarios').replaceChildren();$('lab-brief').textContent='Brokerage positions are read-only; missing cost basis cannot be assumed to be zero.';return}
 if(Number(d.purchase_price)<=0||Number(d.invest_amount)<=0){out.textContent='Enter a positive purchase price and investment amount.';return}
 const seq=++requestId;last=null;
 try{
 const r=await WA.api('/api/wealth/lab','POST',d);if(seq!==requestId)return;
 if(!r.ok){out.textContent=(r.errors||[r.error||'Simulation unavailable']).join(' · ');return}
 last=r;
 const tiles=[
 tile('New shares',fmt(r.new_shares),'for '+money(r.purchase_cost)),
 tile('Total shares',fmt(r.total_shares),'Total basis '+money(r.total_basis)),
 tile('New average cost',money(r.new_avg_cost),'Previously '+(r.old_avg_cost==null?'not applicable':money(r.old_avg_cost))),
 tile('Break-even price',money(r.breakeven_price),'After hypothetical purchase'),
 tile('P/L at market',money(r.pl_now_after),'Before '+(r.pl_now_before==null?'not applicable':money(r.pl_now_before)),r.pl_now_after>=0?'lab-positive':'lab-negative'),
 tile('Portfolio weight',r.weight_after_pct==null?'Not available':Number(r.weight_after_pct).toFixed(1)+'%','Based on supplied portfolio value')];
 $('lab-kpis').innerHTML=tiles.join('');
 $('lab-chart-wrap').innerHTML=chart(r);
 $('lab-brief').textContent=brief(r);
 const scenarios=r.scenarios||[];
 $('lab-scenarios').innerHTML=scenarios.map(s=>'<tr><td>'+money(s.price)+'</td><td>'+Number(s.move_pct).toFixed(0)+'%</td><td>'+money(s.new_money_pl)+'</td><td>'+money(s.position_pl)+'</td><td>'+money(s.without_purchase_pl)+'</td></tr>').join('');
 compare(r);out.textContent='';
 }catch(e){if(seq===requestId)out.textContent='Simulation temporarily unavailable. Check your inputs and connection.'}
}
let timer;form.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(run,180)});
$('lab-price-range').addEventListener('input',e=>{form.elements.purchase_price.value=e.target.value;run()});
form.elements.purchase_price.addEventListener('input',e=>{const v=Number(e.target.value);if(v>0){const slider=$('lab-price-range');slider.max=Math.max(Number(slider.max),Math.ceil(v*1.5));slider.value=v}});
function refreshSaved(){const list=saved(),el=$('lab-saved-list');el.replaceChildren();list.forEach((x,i)=>{const opt=document.createElement('option');opt.value=i;opt.textContent=x.name+' · '+x.ticker;el.append(opt)})}
$('lab-save').addEventListener('click',()=>{
 if(!last){$('lab-save-status').textContent='Run a valid simulation first.';return}
 const name=prompt('Name this hypothetical scenario (for example: SPCX at $140)');if(!name)return;
 const data=Object.fromEntries(new FormData(form));data.fractional=form.elements.fractional.checked;
 const list=saved();list.unshift({name:name.slice(0,70),ticker:data.ticker,date:new Date().toISOString(),data});
 $('lab-save-status').textContent=store(list)?'Saved in this browser only. No trade was placed.':'Browser storage unavailable.';
 refreshSaved();
});
$('lab-load').addEventListener('click',()=>{const item=saved()[Number($('lab-saved-list').value)];if(!item)return;for(const [k,v]of Object.entries(item.data)){const field=form.elements[k];if(!field)continue;if(field.type==='checkbox')field.checked=!!v;else field.value=v}if(pick)pick.value='';run()});
$('lab-delete').addEventListener('click',()=>{const i=Number($('lab-saved-list').value),list=saved();if(!list[i])return;list.splice(i,1);store(list);refreshSaved();$('lab-save-status').textContent='Scenario removed from this browser.'});
refreshSaved();if(!pick||pick.value==='')run();
})();
