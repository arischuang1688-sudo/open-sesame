const OWNER='arischuang1688-sudo';
const REPO='open-sesame';
const WORKFLOW='main.yml';
const ANALYTICS_ISSUE=2;
const ALLOWED_ORIGIN=process.env.ALLOWED_ORIGIN || 'https://arischuang1688-sudo.github.io';
const COOLDOWN_MS=15*60*1000;

function cors(res){
  res.setHeader('Access-Control-Allow-Origin',ALLOWED_ORIGIN);
  res.setHeader('Access-Control-Allow-Methods','POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers','Content-Type');
  res.setHeader('Cache-Control','no-store');
  res.setHeader('Vary','Origin');
}
function ghHeaders(token){return {
  'Authorization':`Bearer ${token}`,
  'Accept':'application/vnd.github+json',
  'X-GitHub-Api-Version':'2022-11-28',
  'Content-Type':'application/json',
  'User-Agent':'open-sesame-vercel-trigger'
}}
async function logEvent(token,event,extra={}){
  try{
    const body=JSON.stringify({v:1,event,ts:new Date().toISOString(),...extra});
    await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/issues/${ANALYTICS_ISSUE}/comments`,{method:'POST',headers:ghHeaders(token),body:JSON.stringify({body})});
  }catch{}
}
async function currentDashboard(){
  try{
    const r=await fetch(`https://raw.githubusercontent.com/${OWNER}/${REPO}/main/data/dashboard.json?ts=${Date.now()}`,{cache:'no-store'});
    if(!r.ok)return null;
    return await r.json();
  }catch{return null}
}
async function activeRun(token){
  try{
    const u=`https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=10`;
    const r=await fetch(u,{headers:ghHeaders(token),cache:'no-store'});
    if(!r.ok)return null;
    const j=await r.json();
    return (j.workflow_runs||[]).find(x=>x.status==='queued'||x.status==='in_progress')||null;
  }catch{return null}
}

export default async function handler(req,res){
  cors(res);
  if(req.method==='OPTIONS')return res.status(204).end();
  if(req.method!=='POST')return res.status(405).json({error:'Method not allowed'});
  if(req.headers.origin && req.headers.origin!==ALLOWED_ORIGIN)return res.status(403).json({error:'Origin not allowed'});
  const token=process.env.GITHUB_TOKEN;
  if(!token)return res.status(500).json({error:'GITHUB_TOKEN is not configured'});
  const requestId=String(req.body?.request_id||'').trim();
  const visitorId=String(req.body?.visitor_id||'').trim().slice(0,80);
  if(!/^[A-Za-z0-9_-]{8,80}$/.test(requestId))return res.status(400).json({error:'Invalid request_id'});

  const d=await currentDashboard();
  const updated=d?.updated_at?Date.parse(d.updated_at):0;
  const age=updated?Date.now()-updated:Infinity;
  if(age>=0 && age<COOLDOWN_MS){
    await logEvent(token,'update_cooldown',{request_id:requestId,visitor_id:visitorId,updated_at:d.updated_at,age_seconds:Math.round(age/1000)});
    return res.status(200).json({ok:true,action:'cooldown',request_id:requestId,updated_at:d.updated_at,retry_after_seconds:Math.max(0,Math.ceil((COOLDOWN_MS-age)/1000))});
  }

  const active=await activeRun(token);
  if(active){
    await logEvent(token,'update_joined',{request_id:requestId,visitor_id:visitorId,run_id:active.id});
    return res.status(202).json({ok:true,action:'joined',request_id:requestId,run_id:active.id});
  }

  const r=await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,{
    method:'POST',headers:ghHeaders(token),body:JSON.stringify({ref:'main',inputs:{request_id:requestId}})
  });
  if(!r.ok){
    const text=await r.text();
    await logEvent(token,'update_dispatch_failed',{request_id:requestId,visitor_id:visitorId,status:r.status});
    return res.status(r.status).json({error:'GitHub dispatch failed',detail:text.slice(0,500)});
  }
  await logEvent(token,'update_dispatched',{request_id:requestId,visitor_id:visitorId});
  return res.status(202).json({ok:true,action:'dispatched',request_id:requestId});
}
