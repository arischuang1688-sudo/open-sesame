import {ANALYTICS_VERSION,writeEvent} from '../lib/analytics.js';
const ALLOWED_ORIGIN=process.env.ALLOWED_ORIGIN || 'https://arischuang1688-sudo.github.io';
const ALLOWED_EVENTS=new Set(['page_view','manual_update_click','manual_update_success','manual_update_timeout','manual_update_error','analytics_probe']);
function cors(res){res.setHeader('Access-Control-Allow-Origin',ALLOWED_ORIGIN);res.setHeader('Access-Control-Allow-Methods','POST,OPTIONS');res.setHeader('Access-Control-Allow-Headers','Content-Type');res.setHeader('Cache-Control','no-store');res.setHeader('Vary','Origin')}
export default async function handler(req,res){
  cors(res); if(req.method==='OPTIONS')return res.status(204).end();
  if(req.method!=='POST')return res.status(405).json({error:'Method not allowed'});
  if(req.headers.origin&&req.headers.origin!==ALLOWED_ORIGIN)return res.status(403).json({error:'Origin not allowed'});
  const event=String(req.body?.event||''); if(!ALLOWED_EVENTS.has(event))return res.status(400).json({error:'Invalid event'});
  const clean=value=>String(value||'').replace(/[^A-Za-z0-9_-]/g,'').slice(0,80);
  const extra=event==='analytics_probe'?{}:{visitor_id:clean(req.body?.visitor_id),session_id:clean(req.body?.session_id)};
  const result=await writeEvent(process.env.GITHUB_TOKEN,event,extra);
  if(!result.ok)return res.status(result.code==='GITHUB_TOKEN_MISSING'?500:502).json({error:'analytics write failed',version:ANALYTICS_VERSION,...result});
  return res.status(202).json({...result,version:ANALYTICS_VERSION});
}
