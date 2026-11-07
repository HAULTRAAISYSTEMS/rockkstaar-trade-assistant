const test=require('node:test'),assert=require('node:assert/strict');
const {scenario,replay,datedEvents,quoteComparison,safeURL}=require('../../static/js/research_workbench.js');
const a={price:100,eps:4,return:10,years:5,multiple:25,dilution:0,bear:0,base:10,bull:20};
test('reverse model matches forward model and dilution',()=>{
 const r=scenario(a); assert.ok(Math.abs(r.requiredGrowth-10)<1e-10);
 assert.ok(Math.abs(r.cases[1].annualReturn-10)<1e-10);
 assert.equal(r.cases[0].price,100);
 assert.ok(Math.abs(scenario({...a,dilution:2}).requiredGrowth-12.2)<1e-10);
});
test('invalid and overflowing assumptions rejected',()=>{
 for(const patch of [{eps:0},{eps:-1},{price:''},{years:2.5},{dilution:-100},{price:1.79e308}])assert.throws(()=>scenario({...a,...patch}));
});
test('replay excludes future, undated and later edited records',()=>{
 const events=datedEvents({news:[{headline:'old',published_at:'2026-01-01',url:'https://example.com/1'},{headline:'new',published_at:'2026-03-01'},{headline:'unknown'}]});
 const records=[{created_at:'2026-01-01',updated_at:'2026-01-01',source_date:'2026-01-01'},{created_at:'2026-01-01',updated_at:'2026-03-01',source_date:'2026-01-01'}];
 const r=replay(events,records,'2026-02-01');assert.equal(r.events.length,1);assert.equal(r.records.length,1);
});
test('benchmark comparisons reject mismatched sessions and invalid quotes',()=>{
 const c={change_pct:5,quote_as_of:'2026-11-06T20:00:00Z'},b={c:102,pc:100,t:Date.parse(c.quote_as_of)/1000};
 assert.ok(Math.abs(quoteComparison(c,b).excess-3)<1e-10);
 assert.equal(quoteComparison(c,{...b,t:b.t-86400}),null);
 assert.equal(quoteComparison(c,{...b,t:'bad'}),null);
 assert.equal(quoteComparison(c,{...b,c:0}),null);
});
test('unsafe source protocols never become links',()=>{
 assert.equal(safeURL('javascript:alert(1)'),'');assert.equal(safeURL('https://user:secret@example.com'),'');
 assert.equal(safeURL('https://www.sec.gov/'),'https://www.sec.gov/');
});
test('workspace renders independently loaded sources and escapes provider text',async()=>{
 const vm=require('node:vm'),fs=require('node:fs');
 const elements=new Map();
 const element=id=>{if(!elements.has(id))elements.set(id,{id,value:'',innerHTML:'',textContent:'',dataset:{},addEventListener(){},setAttribute(){}});return elements.get(id);};
 element('lab').dataset.ticker='ISRG';
 const data={
  snapshot:{profile:{company_name:'Example',description:'<img src=x onerror=bad()>'},valuation:{price:100,change_pct:2,quote_as_of:'2026-11-06T20:00:00Z'},news:[]},
  quarter:{available:true,period_end:'2026-09-30',filed:'2026-10-25',statements:[{title:'Income statement',basis:'Quarter',periods:['2026-09-30'],rows:[{label:'Revenue',values:['$1B']}]}]},
  financials:{sections:[{name:'Cash Flow',earned:8,possible:10}],history:[],statements:[]},
  benchmarks:{},earnings:{rows:[{period:'2026-09-30',actual:2,estimate:1.8}],source:'Fixture'},ttm:{computed:false}
 };
 const sandbox={module:{exports:{}},URL,console,setTimeout,clearTimeout,location:{hash:''},history:{replaceState(){}},
  document:{getElementById:element,querySelector:()=>null,querySelectorAll:()=>[]},
  fetch:async url=>({ok:true,status:200,json:async()=>url.endsWith('records')?{records:[],watchlist:[],memory:[]}:{status:'ready',data:data[url.split('/').pop()],fetched_at:'2026-11-06'}}),
  window:{addEventListener(){}}
 };
 vm.runInNewContext(fs.readFileSync(require.resolve('../../static/js/research_workbench.js'),'utf8'),sandbox);
 await new Promise(resolve=>setImmediate(resolve));
 assert.match(element('lab-status').textContent,/6 of 6 data sections loaded/);
 assert.match(element('lab-financial-content').innerHTML,/2026-09-30/);
 assert.match(element('lab-brief-content').innerHTML,/&lt;img/);
 assert.doesNotMatch(element('lab-brief-content').innerHTML,/<img/);
 assert.match(element('lab-earnings-content').innerHTML,/2/);
});
