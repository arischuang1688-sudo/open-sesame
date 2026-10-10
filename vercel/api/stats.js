import {COMMENTS_URL,ANALYTICS_VERSION,analyticsHeaders,analyticsFailure} from '../lib/analytics.js';
const ALLOWED_ORIGIN=process.env.ALLOWED_ORIGIN || 'https://arischuang1688-sudo.github.io';
function cors(res){res.setHeader('Access-Control-Allow-Origin',ALLOWED_ORIGIN);res.setHeader('Access-Control-Allow-Methods','GET,OPTIONS');res.setHeader('Access-Control-Allow-Headers','Content-Type');res.setHeader('Cache-Control','no-store');res.setHeader('Vary','Origin')}
function dayKey(ts){try{return new Date(ts).toLocaleDateString('sv-SE',{timeZone:'Asia/Taipei'})}catch{return''}}
export default async function handler(req,res){
  cors(res); if(req.method==='OPTIONS')return res.status(204).end();
  if(req.method!=='GET')return res.status(405).json({error:'Method not allowed'});
  const token=process.env.GITHUB_TOKEN; if(!token)return res.status(500).json({error:'analytics read failed',...analyticsFailure('read',{code:'GITHUB_TOKEN_MISSING'})});
  let comments=[];
  try{
    for(let page=1;page<=10;page++){
      const r=await fetch(`${COMMENTS_URL}?per_page=100&page=${page}`,{headers:analyticsHeaders(token),cache:'no-store',signal:AbortSignal.timeout(8000)});
      if(!r.ok)return res.status(502).json({error:'analytics read failed',...analyticsFailure('read',{response:r})});
      const a=await r.json();
      if(!Array.isArray(a))throw Error('Invalid comments response');
      comments=comments.concat(a);
      if(a.length<100)break;
      // Never present a silently truncated first 1,000 events as all-time totals.
      if(page===10)return res.status(502).json({error:'analytics scan limit reached',...analyticsFailure('read',{code:'ANALYTICS_SCAN_LIMIT'})});
    }
  }catch{return res.status(502).json({error:'analytics read failed',...analyticsFailure('read')})}
  const events=[]; let probes=0;
  const known=new Set(['page_view','manual_update_click','manual_update_success','manual_update_timeout','manual_update_error','update_dispatched','update_joined','update_cooldown','update_dispatch_failed']);
  for(const c of comments){try{
    const e=JSON.parse(c.body);
    if(!e||!Number.isFinite(Date.parse(e.ts)))continue;
    if(e.event==='analytics_probe'){probes++;continue}
    if(known.has(e.event))events.push(e);
  }catch{}}
  const counts={}; const visitors=new Set(); const todayVisitors=new Set(); const daily={};
  const today=dayKey(new Date().toISOString());
  for(const e of events){
    counts[e.event]=(counts[e.event]||0)+1;
    if(e.visitor_id)visitors.add(e.visitor_id);
    const d=dayKey(e.ts); if(!daily[d])daily[d]={page_view:0,manual_update_click:0,update_dispatched:0,update_joined:0,update_cooldown:0};
    if(e.event in daily[d])daily[d][e.event]++;
    if(d===today&&e.visitor_id)todayVisitors.add(e.visitor_id);
  }
  const days=Object.keys(daily).sort().slice(-14).map(date=>({date,...daily[date]}));
  return res.status(200).json({ok:true,version:ANALYTICS_VERSION,storage_status:events.length?'recorded':'no_records',diagnostic_events:probes,first_event_at:events.length?events.map(e=>e.ts).sort()[0]:null,generated_at:new Date().toISOString(),events_scanned:events.length,unique_visitors:visitors.size,today_unique_visitors:todayVisitors.size,page_views:counts.page_view||0,manual_update_clicks:counts.manual_update_click||0,actual_dispatches:counts.update_dispatched||0,joined_existing:counts.update_joined||0,cooldown_blocked:counts.update_cooldown||0,success_ui:counts.manual_update_success||0,timeouts_ui:counts.manual_update_timeout||0,errors_ui:counts.manual_update_error||0,daily:days,note:'Anonymous aggregate statistics; visitor IDs are random browser identifiers and no names or email addresses are stored.'});
}
