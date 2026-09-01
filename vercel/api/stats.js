const OWNER='arischuang1688-sudo';
const REPO='open-sesame';
const ANALYTICS_ISSUE=2;
const ALLOWED_ORIGIN=process.env.ALLOWED_ORIGIN || 'https://arischuang1688-sudo.github.io';
function cors(res){res.setHeader('Access-Control-Allow-Origin',ALLOWED_ORIGIN);res.setHeader('Access-Control-Allow-Methods','GET,OPTIONS');res.setHeader('Access-Control-Allow-Headers','Content-Type');res.setHeader('Cache-Control','no-store');res.setHeader('Vary','Origin')}
function headers(token){return {'Authorization':`Bearer ${token}`,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'open-sesame-analytics-stats'}}
function dayKey(ts){try{return new Date(ts).toLocaleDateString('sv-SE',{timeZone:'Asia/Taipei'})}catch{return''}}
export default async function handler(req,res){
  cors(res); if(req.method==='OPTIONS')return res.status(204).end();
  if(req.method!=='GET')return res.status(405).json({error:'Method not allowed'});
  const token=process.env.GITHUB_TOKEN; if(!token)return res.status(500).json({error:'GITHUB_TOKEN is not configured'});
  let comments=[];
  for(let page=1;page<=10;page++){
    const r=await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/issues/${ANALYTICS_ISSUE}/comments?per_page=100&page=${page}`,{headers:headers(token),cache:'no-store'});
    if(!r.ok)return res.status(502).json({error:'analytics read failed'});
    const a=await r.json(); comments=comments.concat(a); if(a.length<100)break;
  }
  const events=[];
  for(const c of comments){try{const e=JSON.parse(c.body);if(e&&e.event&&e.ts)events.push(e)}catch{}}
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
  return res.status(200).json({ok:true,generated_at:new Date().toISOString(),events_scanned:events.length,unique_visitors:visitors.size,today_unique_visitors:todayVisitors.size,page_views:counts.page_view||0,manual_update_clicks:counts.manual_update_click||0,actual_dispatches:counts.update_dispatched||0,joined_existing:counts.update_joined||0,cooldown_blocked:counts.update_cooldown||0,success_ui:counts.manual_update_success||0,timeouts_ui:counts.manual_update_timeout||0,errors_ui:counts.manual_update_error||0,daily:days,note:'Anonymous aggregate statistics; visitor IDs are random browser identifiers and no names or email addresses are stored.'});
}
