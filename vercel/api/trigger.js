const OWNER='arischuang1688-sudo';
const REPO='open-sesame';
const WORKFLOW='main.yml';
const ALLOWED_ORIGIN=process.env.ALLOWED_ORIGIN || 'https://arischuang1688-sudo.github.io';

function cors(res){
  res.setHeader('Access-Control-Allow-Origin', ALLOWED_ORIGIN);
  res.setHeader('Access-Control-Allow-Methods','POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers','Content-Type');
  res.setHeader('Vary','Origin');
}

export default async function handler(req,res){
  cors(res);
  if(req.method==='OPTIONS') return res.status(204).end();
  if(req.method!=='POST') return res.status(405).json({error:'Method not allowed'});
  if(req.headers.origin && req.headers.origin!==ALLOWED_ORIGIN) return res.status(403).json({error:'Origin not allowed'});
  const token=process.env.GITHUB_TOKEN;
  if(!token) return res.status(500).json({error:'GITHUB_TOKEN is not configured'});
  const requestId=String(req.body?.request_id||'').trim();
  if(!/^[A-Za-z0-9_-]{8,80}$/.test(requestId)) return res.status(400).json({error:'Invalid request_id'});
  const r=await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,{
    method:'POST',
    headers:{
      'Authorization':`Bearer ${token}`,
      'Accept':'application/vnd.github+json',
      'X-GitHub-Api-Version':'2022-11-28',
      'Content-Type':'application/json',
      'User-Agent':'open-sesame-vercel-trigger'
    },
    body:JSON.stringify({ref:'main',inputs:{request_id:requestId}})
  });
  if(!r.ok){
    const text=await r.text();
    return res.status(r.status).json({error:'GitHub dispatch failed',detail:text.slice(0,500)});
  }
  return res.status(202).json({ok:true,request_id:requestId});
}
