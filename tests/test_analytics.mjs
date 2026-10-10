import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import eventHandler from '../vercel/api/event.js';
import statsHandler from '../vercel/api/stats.js';
import triggerHandler from '../vercel/api/trigger.js';
import {writeEvent} from '../vercel/lib/analytics.js';

const origin='https://arischuang1688-sudo.github.io';
const json=(data,status=200,headers={})=>new Response(JSON.stringify(data),{status,headers});
function response(){return {headers:{},setHeader(k,v){this.headers[k]=v},status(n){this.statusCode=n;return this},json(b){this.body=b;return this},end(){return this}}}
function setup(t,fetcher){process.env.GITHUB_TOKEN='test-secret-never-log';t.after(()=>delete process.env.GITHUB_TOKEN);t.mock.method(globalThis,'fetch',fetcher);return t.mock.method(console,'error',()=>{})}
const req=body=>({method:'POST',headers:{origin},body});

test('event -> Issue comment -> stats totals, anonymous IDs and Taipei day boundary',async t=>{
  const comments=[];
  setup(t,async(url,options)=>{
    assert.match(url,/issues\/2\/comments/);
    if(options.method==='POST'){comments.push({id:comments.length+1,body:JSON.parse(options.body).body});return json(comments.at(-1),201)}
    return json(comments);
  });
  const res=response();
  await eventHandler(req({event:'page_view',visitor_id:'v_1!',session_id:'s_1',email:'not-stored',ip:'not-stored'}),res);
  assert.equal(res.statusCode,202);assert.equal(res.body.comment_id,1);
  assert.deepEqual(Object.keys(JSON.parse(comments[0].body)).sort(),['event','session_id','ts','v','visitor_id']);
  assert.equal(JSON.parse(comments[0].body).visitor_id,'v_1');
  for(const event of ['page_view','manual_update_click','update_dispatched','update_joined','update_cooldown','manual_update_success'])await writeEvent(process.env.GITHUB_TOKEN,event,{visitor_id:'v_1'});
  await eventHandler(req({event:'analytics_probe',visitor_id:'ignored'}),response());
  comments.push({body:'not JSON'},{body:JSON.stringify({event:'page_view',ts:'invalid'})});
  const stats=response();await statsHandler({method:'GET'},stats);
  assert.equal(stats.statusCode,200);assert.equal(stats.body.events_scanned,7);
  assert.equal(stats.body.unique_visitors,1);assert.equal(stats.body.today_unique_visitors,1);
  for(const key of ['manual_update_clicks','actual_dispatches','joined_existing','cooldown_blocked','success_ui','diagnostic_events'])assert.equal(stats.body[key],1,key);
  assert.equal(stats.body.page_views,2);assert.equal(stats.body.storage_status,'recorded');
  comments.length=0;
  comments.push({body:JSON.stringify({event:'page_view',ts:'2026-10-09T16:00:00Z',visitor_id:'v_2'})});
  const day=response();await statsHandler({method:'GET'},day);assert.equal(day.body.daily[0].date,'2026-10-10');
});

test('GitHub permission failure is traceable without logging secret, payload or IDs',async t=>{
  const log=setup(t,async()=>json({message:'Resource not accessible by personal access token'},403,{'x-github-request-id':'GH-TRACE','x-accepted-github-permissions':'issues=write'}));
  const res=response();await eventHandler(req({event:'page_view',visitor_id:'private-random-id'}),res);
  assert.equal(res.statusCode,502);assert.equal(res.body.code,'GITHUB_PERMISSION_DENIED');assert.equal(res.body.github_status,403);assert.equal(res.body.github_request_id,'GH-TRACE');assert.ok(res.body.error_id);
  const logged=log.mock.calls[0].arguments[0];assert.match(logged,/issues=write/);assert.ok(!logged.includes('test-secret'));assert.ok(!logged.includes('private-random-id'));
});

test('rate limits are distinct from denied permissions',async t=>{
  setup(t,async()=>json({},403,{'x-ratelimit-remaining':'0','retry-after':'60'}));
  assert.equal((await writeEvent(process.env.GITHUB_TOKEN,'page_view')).code,'GITHUB_RATE_LIMITED');
});

test('network failures and missing token return explicit errors',async t=>{
  setup(t,async()=>{throw Error('secret must not leak')});
  const res=response();await eventHandler(req({event:'page_view'}),res);assert.equal(res.body.code,'GITHUB_NETWORK_ERROR');
  delete process.env.GITHUB_TOKEN;const missing=response();await eventHandler(req({event:'page_view'}),missing);
  assert.equal(missing.statusCode,500);assert.equal(missing.body.code,'GITHUB_TOKEN_MISSING');
});

test('CORS, methods and invalid events never write',async t=>{
  setup(t,async()=>{throw Error('must not fetch')});
  for(const [request,status] of [[{method:'OPTIONS'},204],[{method:'GET'},405],[{...req({event:'page_view'}),headers:{origin:'https://other.example'}},403],[req({event:'update_dispatched'}),400]]){
    const res=response();await eventHandler(request,res);assert.equal(res.statusCode,status);
  }
  assert.equal(fetch.mock.callCount(),0);
});

test('dispatch remains successful with visible analytics failure and no duplicate dispatch',async t=>{
  let dispatches=0;
  setup(t,async(url,options)=>{
    if(url.includes('raw.githubusercontent'))return json({});
    if(url.includes('/runs?'))return json({workflow_runs:[]});
    if(url.endsWith('/dispatches')){dispatches++;assert.deepEqual(JSON.parse(options.body),{ref:'main',inputs:{request_id:'web_test_123'}});return new Response(null,{status:204})}
    return json({},403);
  });
  const res=response();await triggerHandler(req({request_id:'web_test_123',visitor_id:'v_1'}),res);
  assert.equal(res.statusCode,202);assert.equal(res.body.action,'dispatched');assert.equal(res.body.ok,true);assert.equal(res.body.analytics.code,'GITHUB_PERMISSION_DENIED');assert.equal(dispatches,1);
});

for(const action of ['cooldown','joined'])test(`${action} records the right event without a new dispatch`,async t=>{
  let logged;
  setup(t,async(url,options)=>{
    if(url.includes('raw.githubusercontent'))return json(action==='cooldown'?{updated_at:new Date().toISOString()}:{});
    if(url.includes('/runs?'))return json({workflow_runs:[{id:123,status:'in_progress'}]});
    assert.match(url,/issues\/2\/comments/);logged=JSON.parse(JSON.parse(options.body).body);return json({id:1},201);
  });
  const res=response();await triggerHandler(req({request_id:'web_test_123'}),res);
  assert.equal(res.body.action,action);assert.equal(res.body.analytics.ok,true);assert.equal(logged.event,`update_${action}`);
});

test('stats read errors do not return false zero totals',async t=>{
  setup(t,async()=>json({},401));const res=response();await statsHandler({method:'GET'},res);
  assert.equal(res.statusCode,502);assert.equal(res.body.code,'GITHUB_AUTH_FAILED');assert.equal(res.body.unique_visitors,undefined);
});

test('empty stats and diagnostic probes are not presented as verified absence of visitors',async t=>{
  setup(t,async()=>json([{body:JSON.stringify({event:'analytics_probe',ts:new Date().toISOString()})}]));
  const res=response();await statsHandler({method:'GET'},res);assert.equal(res.body.storage_status,'no_records');assert.equal(res.body.events_scanned,0);assert.equal(res.body.diagnostic_events,1);
});

test('stats pagination covers subsequent pages and does not silently truncate',async t=>{
  const batch=Array.from({length:100},()=>({body:JSON.stringify({event:'page_view',ts:new Date().toISOString(),visitor_id:'v_1'})}));
  setup(t,async url=>json(url.endsWith('page=1')?batch:[]));
  const res=response();await statsHandler({method:'GET'},res);assert.equal(fetch.mock.callCount(),2);assert.equal(res.body.page_views,100);
  fetch.mock.mockImplementation(async()=>json(batch));
  const full=response();await statsHandler({method:'GET'},full);assert.equal(full.statusCode,502);assert.equal(full.body.code,'ANALYTICS_SCAN_LIMIT');
});

const script=name=>readFileSync(new URL(`../${name}`,import.meta.url),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function storage(){const m=new Map();return {getItem:k=>m.get(k),setItem:(k,v)=>m.set(k,v)}}
test('browser only marks page view saved after API success; failed writes can retry on reload',async()=>{
  for(const ok of [false,true]){
    const sessionStorage=storage(),warnings=[];
    const context=vm.createContext({sessionStorage,localStorage:storage(),fetch:async()=>json({ok,code:'GITHUB_PERMISSION_DENIED'},ok?202:502),console:{warn:(...args)=>warnings.push(args)}});
    vm.runInContext(script('index.html').split('function condChips')[0],context);await flush();
    assert.equal(sessionStorage.getItem('os_pv'),ok?'1':undefined);assert.equal(warnings.length,ok?0:1);
  }
});

test('admin renders successful totals, missing records and traceable errors',async()=>{
  for(const kind of ['success','empty','error']){
    const elements=Object.fromEntries(['updated','coverage','cards','rows'].map(k=>[k,{textContent:'',innerHTML:''}]));
    const data=kind==='error'?{ok:false,error:'analytics read failed',code:'GITHUB_AUTH_FAILED',error_id:'trace-123'}:{ok:true,generated_at:new Date().toISOString(),events_scanned:kind==='empty'?0:8,today_unique_visitors:2,unique_visitors:3,page_views:4,manual_update_clicks:2,actual_dispatches:1,joined_existing:1,daily:[{date:'2026-10-10',page_view:4}]};
    vm.runInNewContext(script('admin.html'),{document:{getElementById:k=>elements[k]},fetch:async()=>json(data,kind==='error'?502:200)});await flush();
    if(kind==='error'){assert.match(elements.updated.textContent,/trace-123/);assert.equal(elements.cards.innerHTML,'')}
    else if(kind==='empty'){assert.match(elements.coverage.textContent,/無法據此判定/);assert.match(elements.cards.innerHTML,/—/)}
    else{assert.match(elements.cards.innerHTML,/累計不同訪客.*?3/);assert.match(elements.cards.innerHTML,/真正啟動更新.*?1/);assert.match(elements.rows.innerHTML,/2026-10-10/)}
  }
});
