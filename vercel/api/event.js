const OWNER='arischuang1688-sudo';
const REPO='open-sesame';
const ANALYTICS_ISSUE=2;
const ALLOWED_ORIGIN=process.env.ALLOWED_ORIGIN || 'https://arischuang1688-sudo.github.io';
const ALLOWED_EVENTS=new Set(['page_view','manual_update_click','manual_update_success','manual_update_timeout','manual_update_error']);
function cors(res){res.setHeader('Access-Control-Allow-Origin',ALLOWED_ORIGIN);res.setHeader('Access-Control-Allow-Methods','POST,OPTIONS');res.setHeader('Access-Control-Allow-Headers','Content-Type');res.setHeader('Cache-Control','no-store');res.setHeader('Vary','Origin')}
function headers(token){return {'Authorization':`Bearer ${token}`,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json','User-Agent':'open-sesame-analytics'}}
export default async function handler(req,res){
  cors(res); if(req.method==='OPTIONS')return res.status(204).end();
  if(req.method!=='POST')return res.status(405).json({error:'Method not allowed'});
  if(req.headers.origin&&req.headers.origin!==ALLOWED_ORIGIN)return res.status(403).json({error:'Origin not allowed'});
  const token=process.env.GITHUB_TOKEN; if(!token)return res.status(500).json({error:'GITHUB_TOKEN is not configured'});
  const event=String(req.body?.event||''); if(!ALLOWED_EVENTS.has(event))return res.status(400).json({error:'Invalid event'});
  const visitorId=String(req.body?.visitor_id||'').replace(/[^A-Za-z0-9_-]/g,'').slice(0,80);
  const sessionId=String(req.body?.session_id||'').replace(/[^A-Za-z0-9_-]/g,'').slice(0,80);
  const body=JSON.stringify({v:1,event,ts:new Date().toISOString(),visitor_id:visitorId,session_id:sessionId});
  const r=await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/issues/${ANALYTICS_ISSUE}/comments`,{method:'POST',headers:headers(token),body:JSON.stringify({body})});
  if(!r.ok)return res.status(502).json({error:'analytics write failed'});
  return res.status(202).json({ok:true});
}
