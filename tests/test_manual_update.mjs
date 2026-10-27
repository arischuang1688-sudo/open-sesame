import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import handler from '../vercel/api/status.js';

const fixture=JSON.parse(readFileSync(new URL('./fixtures/manual_no_new_data_20261027.json',import.meta.url)));
const origin='https://arischuang1688-sudo.github.io';
const requestId=fixture.run.display_title.replace('Open Sesame Update ','');
const validate='80% Strict same-trading-day validation',rank='85% Finalize ranking',publish='95% Publish dashboard data';
const marker='No newer trading day - keep current data';
const updated_at='2026-10-27T02:57:00Z';
const oldData={updated_at:'2026-10-22T17:57:00Z',stocks:[],market:{quote:{date:'2026-10-22'}}};
const newData={...oldData,updated_at};
const json=(data,status=200)=>new Response(JSON.stringify(data),{status});
const copy=()=>structuredClone(fixture);
const step=(f,name,conclusion)=>Object.assign(f.jobs[0].steps.find(s=>s.name===name),{status:'completed',conclusion});
function publishing(){
  const f=copy();step(f,marker,'skipped');
  for(const name of [validate,rank,publish])step(f,name,'success');
  return f;
}
function response(){return {headers:{},setHeader(k,v){this.headers[k]=v},status(n){this.statusCode=n;return this},json(b){this.body=b;return this},end(){return this}}}
async function status(t,f=copy(),{page=oldData,query={request_id:requestId},jobs=f.jobs,request={},upstreamStatus=200}={}){
  const calls=[];
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    calls.push(url);
    assert.equal(options.method||'GET','GET','status must never dispatch or publish');
    if(url.includes('github.io')){
      assert.equal(options.headers?.Authorization,undefined);
      if(page instanceof Error)throw page;
      return json(page,page===null?503:200);
    }
    if(upstreamStatus!==200)return json({},upstreamStatus);
    if(url.includes('/jobs?')){assert.match(url,/filter=latest/);return json({jobs})}
    if(url.includes('/workflows/main.yml/runs?'))return json({workflow_runs:f.run?[f.run]:[]});
    assert.ok(url.endsWith(`/actions/runs/${f.run.id}`));return json(f.run);
  });
  const res=response();
  await handler({method:'GET',headers:{origin},query,...request},res);
  assert.equal(res.headers['Cache-Control'],'no-store');
  return {...res,calls};
}

test('real Oct 27 run is terminal no_new_data without polling Pages or claiming publication',async t=>{
  const res=await status(t);
  assert.equal(res.statusCode,200);
  assert.equal(res.body.outcome,'no_new_data');assert.equal(res.body.reason,'no_newer_trading_day');
  assert.equal(res.body.done,true);assert.equal(res.body.failed,false);assert.equal(res.body.published,false);
  assert.equal(res.body.run_id,40441287252);
  assert.match(res.body.message,/目前尚無新的完整交易日資料，已保留最近有效行情/);
  assert.equal(res.body.updated_at,undefined);assert.equal(res.calls.length,2);
});

test('success still means no_new_data when another run has a fresh Pages timestamp',async t=>{
  const res=await status(t,copy(),{page:newData});
  assert.equal(res.body.outcome,'no_new_data');assert.ok(!res.calls.some(u=>u.includes('github.io')));
});

for(const [name,guard,reason] of [
  ['Market closed - keep current data','12% Offline TWSE trading day guard','market_closed'],
  ['Cooldown active - reuse current data','15% Cooldown guard','cooldown'],
  ['Scheduled snapshot already complete','18% Scheduled snapshot guard','snapshot_complete']
])test(`explicit ${reason} skip is terminal without claiming new data`,async t=>{
  const f=copy();step(f,marker,'skipped');step(f,name,'success');step(f,guard,'success');
  const res=await status(t,f);
  assert.equal(res.body.outcome,'no_new_data');assert.equal(res.body.reason,reason);assert.equal(res.body.published,false);
});

test('validated publication completes only after Pages serves the new timestamp',async t=>{
  const res=await status(t,publishing(),{page:newData});
  assert.equal(res.body.outcome,'published');assert.equal(res.body.done,true);assert.equal(res.body.published,true);
  assert.equal(res.body.updated_at,updated_at);
});

for(const [name,page] of [['old snapshot',oldData],['timestamp 30 seconds before run',{updated_at:'2026-10-27T02:55:33Z'}],['invalid timestamp',{updated_at:'invalid'}],['HTTP failure',null],['network failure',new Error('offline')]]){
  test(`publication waits for Pages: ${name}`,async t=>{
    const res=await status(t,publishing(),{page});
    assert.equal(res.body.outcome,'publishing');assert.equal(res.body.done,false);assert.equal(res.body.published,false);
  });
}

test('reruns compare the snapshot against run_started_at, not original created_at',async t=>{
  const f=publishing();f.run.run_started_at='2026-10-27T03:00:00Z';
  assert.equal((await status(t,f,{page:newData})).body.outcome,'publishing');
});

test('strict validation failure remains failure even with a skip marker and fresh Pages data',async t=>{
  const f=copy();f.run.conclusion=f.jobs[0].conclusion='failure';step(f,validate,'failure');
  const res=await status(t,f,{page:newData});
  assert.equal(res.body.outcome,'failed');assert.equal(res.body.failed,true);assert.equal(res.body.done,true);
  assert.equal(res.body.validation_pending,true);assert.equal(res.body.published,false);
  assert.ok(!res.calls.some(u=>u.includes('github.io')));
});

for(const conclusion of ['failure','cancelled','timed_out'])test(`${conclusion} after a no-op marker cannot become no_new_data`,async t=>{
  const f=copy();f.run.conclusion=f.jobs[0].conclusion=conclusion;
  const res=await status(t,f);
  assert.equal(res.body.outcome,'failed');assert.equal(res.body.failed,true);
});

test('a no-op marker in a still-running workflow is not terminal',async t=>{
  const f=copy();f.run.status=f.jobs[0].status='in_progress';f.run.conclusion=f.jobs[0].conclusion=null;
  const res=await status(t,f);assert.equal(res.body.done,false);assert.equal(res.body.outcome,'running');
});

for(const kind of ['missing marker','skipped marker','failed guard','missing job','other job','missing publish step','inconsistent publication','contradictory failure']){
  test(`incomplete or contradictory evidence is not reported as success: ${kind}`,async t=>{
    const f=copy();
    if(kind==='missing marker')f.jobs[0].steps=f.jobs[0].steps.filter(s=>s.name!==marker);
    if(kind==='skipped marker')step(f,marker,'skipped');
    if(kind==='failed guard')step(f,'65% New trading day guard','failure');
    if(kind==='missing job')f.jobs=[];
    if(kind==='other job')f.jobs[0].name='unrelated';
    if(kind==='missing publish step')f.jobs[0].steps=f.jobs[0].steps.filter(s=>s.name!==publish);
    if(kind==='inconsistent publication')step(f,publish,'success');
    if(kind==='contradictory failure')step(f,'98% Verify GitHub Pages publication','failure');
    const res=await status(t,f,{page:newData});
    assert.equal(res.statusCode,500);assert.ok(res.body.error);assert.equal(res.body.done,undefined);
  });
}

test('missing run and substring-only request ID matches keep waiting',async t=>{
  const f=copy();f.run.display_title+='suffix';
  const res=await status(t,f);assert.equal(res.body.found,false);assert.equal(res.body.outcome,'pending');
});

for(const event of ['workflow_dispatch','schedule'])test(`joined ${event} uses returned run ID`,async t=>{
  const f=copy();f.run.event=event;f.run.display_title='Open Sesame Update someone_else';
  const res=await status(t,f,{query:{run_id:String(f.run.id)}});
  assert.equal(res.body.outcome,'no_new_data');assert.ok(!res.calls.some(u=>u.includes('/workflows/')));
});

for(const field of ['path','head_branch','event'])test(`joined run rejects unrelated ${field}`,async t=>{
  const f=copy();f.run[field]='unrelated';
  const res=await status(t,f,{query:{run_id:String(f.run.id)}});assert.equal(res.statusCode,400);
});

test('GitHub lookup failures never manufacture a completed status',async t=>{
  const res=await status(t,copy(),{upstreamStatus:403});assert.equal(res.statusCode,500);assert.equal(res.body.done,undefined);
});

for(const [request,code] of [[{method:'OPTIONS'},204],[{method:'POST'},405],[{headers:{origin:'https://other.example'}},403],[{query:{request_id:'short'}},400],[{query:{run_id:'../123'}},400]]){
  test(`status rejects invalid request without external access: ${JSON.stringify(request)}`,async t=>{
    const res=await status(t,copy(),{request});assert.equal(res.statusCode,code);assert.equal(res.calls.length,0);
  });
}

test('fixture and API step names match the actual workflow contract',()=>{
  const workflow=readFileSync(new URL('../.github/workflows/main.yml',import.meta.url),'utf8');
  for(const name of [validate,rank,publish,marker,'65% New trading day guard','Market closed - keep current data','Cooldown active - reuse current data','Scheduled snapshot already complete']){
    assert.ok(workflow.includes(`- name: ${name}\n`),name);
  }
});

const script=readFileSync(new URL('../index.html',import.meta.url),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
async function browser({statuses,action='dispatched',pages=[oldData],raw=oldData,tick=4000}){
  const elements=Object.fromEntries(['reload','progressWrap','progressBar','progressText','loadmsg','summary','sourceDates','tbody'].map(k=>[k,{textContent:'',innerHTML:'',style:{},disabled:false,classList:{add(){},remove(){}}}]));
  const calls=[],events=[];let polls=0,reads=0,clock=0,timers=0;
  class Clock extends Date{static now(){return clock}}
  const storage={getItem:k=>k==='os_pv'?'1':'test_visitor',setItem(){}};
  const context=vm.createContext({Date:Clock,console,document:{getElementById:k=>elements[k]},sessionStorage:storage,localStorage:storage,
    setTimeout(fn,ms){if(ms===4000){clock+=tick;timers++}fn()},
    fetch:async(url,options={})=>{
      calls.push(String(url));
      if(url.includes('/api/event')){events.push(JSON.parse(options.body).event);return json({ok:true})}
      if(url.includes('/api/trigger'))return json({ok:true,action,...(action==='joined'?{run_id:fixture.run.id}:{})});
      if(url.includes('/api/status')){
        const value=statuses[Math.min(polls++,statuses.length-1)];return json(value,value===null?500:200);
      }
      if(url.startsWith('https://raw.'))return json(raw);
      assert.match(url,/^data\/dashboard.json\?ts=/);
      return json(pages[Math.min(reads++,pages.length-1)]);
    }
  });
  vm.runInContext(script,context,{filename:'index.html'});
  await new Promise(resolve=>setImmediate(resolve)); // Initial page load.
  await vm.runInContext('manualUpdate()',context);
  assert.equal(elements.reload.disabled,false);assert.equal(elements.reload.textContent,'↻ 手動更新');
  return {elements,calls,events,polls,reads,timers};
}
const published={done:true,failed:false,outcome:'published',published:true,updated_at,percent:100};

test('browser + actual API fixture: no_new_data immediately ends waiting and keeps rendered data',async t=>{
  const api=await status(t);
  const ui=await browser({statuses:[api.body]});
  assert.equal(ui.polls,1);assert.equal(ui.timers,1);assert.equal(ui.reads,2);
  assert.match(ui.elements.loadmsg.textContent,/目前尚無新的完整交易日資料，已保留最近有效行情/);
  assert.doesNotMatch(ui.elements.progressText.innerHTML,/spinner/);
  assert.ok(!ui.calls.some(u=>u.startsWith('https://raw.')));
  assert.deepEqual(ui.events,['manual_update_click']);
});

test('browser joins the returned run ID and ends correctly on no-data result',async()=>{
  const ui=await browser({action:'joined',statuses:[{done:true,outcome:'no_new_data'}]});
  assert.equal(ui.polls,1);assert.ok(ui.calls.some(u=>u.includes(`/api/status?run_id=${fixture.run.id}&`)));
});

test('browser remembers the run ID after discovery',async()=>{
  const ui=await browser({statuses:[{run_id:fixture.run.id,done:false,outcome:'running'},{done:true,outcome:'no_new_data'}]});
  const urls=ui.calls.filter(u=>u.includes('/api/status'));
  assert.match(urls[0],/request_id=web_/);assert.match(urls[1],/run_id=40441287252/);
});

test('browser reports real publication only after its own Pages snapshot catches up',async()=>{
  const ui=await browser({statuses:[published],pages:[oldData,oldData,oldData,newData],raw:newData});
  assert.equal(ui.polls,2);assert.equal(ui.events.filter(e=>e==='manual_update_success').length,1);
  assert.match(ui.elements.loadmsg.textContent,/最新可用資料已重新載入/);
});

test('browser does not treat a fresh timestamp as success before this run fails validation',async()=>{
  const ui=await browser({statuses:[{done:false,outcome:'running'},{done:true,failed:true,validation_pending:true,message:'核心資料尚未同步'}],pages:[oldData,oldData,newData],raw:newData});
  assert.equal(ui.polls,2);assert.match(ui.elements.loadmsg.textContent,/核心資料尚未同步/);
  assert.ok(ui.events.includes('manual_update_error'));assert.ok(!ui.events.includes('manual_update_success'));
  assert.equal(ui.reads,2);
});

test('browser gives failure precedence over a contradictory no-data result',async()=>{
  const ui=await browser({statuses:[{done:true,failed:true,outcome:'no_new_data'}]});
  assert.ok(ui.events.includes('manual_update_error'));assert.match(ui.elements.loadmsg.textContent,/⚠/);
});

for(const outcome of ['publishing','unknown'])test(`browser still times out for unresolved ${outcome}, without claiming success`,async()=>{
  const ui=await browser({statuses:[{done:false,outcome}],pages:[oldData,oldData,newData],raw:newData,tick:60000});
  assert.equal(ui.polls,10);assert.ok(ui.events.includes('manual_update_timeout'));assert.ok(!ui.events.includes('manual_update_success'));
  assert.match(ui.elements.loadmsg.textContent,/發布尚未確認完成/);
});

test('status API outage cannot be hidden by a fresh raw or Pages timestamp',async()=>{
  const ui=await browser({statuses:[null],pages:[oldData,oldData,newData],raw:newData,tick:60000});
  assert.ok(ui.events.includes('manual_update_timeout'));assert.ok(!ui.events.includes('manual_update_success'));
});

test('15-minute cooldown response still ends without status polling',async()=>{
  const ui=await browser({action:'cooldown',statuses:[]});
  assert.equal(ui.polls,0);assert.match(ui.elements.loadmsg.textContent,/15 分鐘內已更新過/);
});
