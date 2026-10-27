const OWNER='arischuang1688-sudo';
const REPO='open-sesame';
const WORKFLOW='main.yml';
const ALLOWED_ORIGIN=process.env.ALLOWED_ORIGIN || 'https://arischuang1688-sudo.github.io';

function cors(res){
  res.setHeader('Access-Control-Allow-Origin', ALLOWED_ORIGIN);
  res.setHeader('Access-Control-Allow-Methods','GET,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers','Content-Type');
  res.setHeader('Cache-Control','no-store');
  res.setHeader('Vary','Origin');
}

const VALIDATE='80% Strict same-trading-day validation';
const RANK='85% Finalize ranking';
const PUBLISH='95% Publish dashboard data';
const NO_PUBLICATION=[
  ['No newer trading day - keep current data','65% New trading day guard','no_newer_trading_day'],
  ['Market closed - keep current data','12% Offline TWSE trading day guard','market_closed'],
  ['Cooldown active - reuse current data','15% Cooldown guard','cooldown'],
  ['Scheduled snapshot already complete','18% Scheduled snapshot guard','snapshot_complete']
];
function stepIs(steps,name,conclusion){
  return steps.some(s=>s.name===name && s.status==='completed' && s.conclusion===conclusion);
}
function noPublicationReason(steps){
  // A successful run alone (including the final echo step) proves neither a
  // no-op nor a publication. Require the explicit guard marker and skipped work.
  if(![VALIDATE,RANK,PUBLISH].every(name=>stepIs(steps,name,'skipped'))) return null;
  const match=NO_PUBLICATION.find(([marker,guard])=>stepIs(steps,marker,'success') && stepIs(steps,guard,'success'));
  if(match?.[2]==='no_newer_trading_day' && !stepIs(steps,'60% Generate latest date-specific TWSE data','success')) return null;
  return match?.[2] || null;
}

function stageFromSteps(steps=[]){
  const exact=[
    ['100% Update workflow finished',100,'GitHub Actions 完成'],
    ['95% Publish dashboard data',95,'發布 Dashboard 資料'],
    ['85% Finalize ranking',85,'完成個股排名'],
    ['80% Strict same-trading-day validation',80,'嚴格同交易日驗證'],
    ['60% Generate latest date-specific TWSE data',60,'取得 TWSE 最新資料'],
    ['20% Setup Python',20,'準備分析環境'],
    ['15% Cooldown guard',15,'檢查更新冷卻時間'],
    ['10% Checkout source',10,'載入程式碼']
  ];
  for(const [name,pct,label] of exact){
    const s=steps.find(x=>x.name===name);
    if(s?.status==='in_progress') return {percent:pct,label};
    if(s?.status==='completed' && s?.conclusion==='failure'){
      if(name==='80% Strict same-trading-day validation'){
        return {percent:80,label:'核心資料日期尚未完全同步',failed:true,validation_pending:true};
      }
      return {percent:pct,label:`${label}失敗`,failed:true};
    }
  }
  return {percent:5,label:'等待 GitHub Actions 啟動'};
}

async function gh(url){
  const headers={
    'Accept':'application/vnd.github+json',
    'X-GitHub-Api-Version':'2022-11-28',
    'User-Agent':'open-sesame-vercel-status'
  };
  if(process.env.GITHUB_TOKEN) headers.Authorization=`Bearer ${process.env.GITHUB_TOKEN}`;
  const r=await fetch(url,{headers,cache:'no-store'});
  if(!r.ok) throw new Error(`GitHub ${r.status}`);
  return r.json();
}

export default async function handler(req,res){
  cors(res);
  if(req.method==='OPTIONS') return res.status(204).end();
  if(req.method!=='GET') return res.status(405).json({error:'Method not allowed'});
  if(req.headers.origin && req.headers.origin!==ALLOWED_ORIGIN) return res.status(403).json({error:'Origin not allowed'});
  const requestId=String(req.query.request_id||'').trim();
  const runId=String(req.query.run_id||'').trim();
  if(runId ? !/^[1-9][0-9]{0,15}$/.test(runId) : !/^[A-Za-z0-9_-]{8,80}$/.test(requestId)) return res.status(400).json({error:'Invalid request_id or run_id'});
  try{
    let run;
    if(runId){
      // trigger.js returns this ID when joining an existing manual/scheduled run.
      run=await gh(`https://api.github.com/repos/${OWNER}/${REPO}/actions/runs/${runId}`);
      if(run.path!==`.github/workflows/${WORKFLOW}` || run.head_branch!=='main' || !['workflow_dispatch','schedule'].includes(run.event)){
        return res.status(400).json({error:'Not a main dashboard update run'});
      }
    }else{
      const runs=await gh(`https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/runs?event=workflow_dispatch&per_page=30`);
      run=(runs.workflow_runs||[]).find(x=>x.display_title===`Open Sesame Update ${requestId}`);
    }
    if(!run) return res.status(200).json({found:false,outcome:'pending',percent:5,label:'等待 GitHub Actions 啟動'});
    const jobs=await gh(`https://api.github.com/repos/${OWNER}/${REPO}/actions/runs/${run.id}/jobs?filter=latest&per_page=100`);
    const job=(jobs.jobs||[]).find(x=>x.name==='update');
    const steps=job?.steps||[];
    const stage=stageFromSteps(steps);
    const common={found:true,run_id:run.id,run_url:run.html_url};
    if(run.status==='completed' && run.conclusion!=='success'){
      if(stage.validation_pending){
        return res.status(200).json({
          ...common,outcome:'failed',published:false,done:true,failed:true,validation_pending:true,percent:80,
          label:'TWSE 核心資料日期尚未完全同步',
          message:'今日個股、大盤與投信資料已取得，但至少一項核心資料（常見為融資）尚未更新到同一交易日。本次不發布混合日期資料，網站繼續保留上一份已通過驗證的完整資料。',
          run_url:run.html_url
        });
      }
      return res.status(200).json({...common,outcome:'failed',published:false,done:true,failed:true,percent:stage.percent,label:stage.failed?stage.label:`GitHub Actions ${run.conclusion}`});
    }
    if(run.status==='completed' && run.conclusion==='success'){
      if(job?.status!=='completed' || job.conclusion!=='success' || steps.some(s=>s.conclusion==='failure' || s.conclusion==='cancelled')){
        throw new Error('Completed run has missing or inconsistent job results');
      }
      const reason=noPublicationReason(steps);
      if(reason){
        return res.status(200).json({...common,outcome:'no_new_data',reason,published:false,done:true,failed:false,percent:100,
          label:'檢查完成，保留最近有效行情',
          message:reason==='cooldown'?'15 分鐘內已更新過，本次保留最近有效行情。':'目前尚無新的完整交易日資料，已保留最近有效行情。'});
      }
      if(![VALIDATE,RANK,PUBLISH].every(name=>stepIs(steps,name,'success')) || NO_PUBLICATION.some(([marker])=>stepIs(steps,marker,'success'))){
        throw new Error('Unable to confirm publication or an explicit no-data result');
      }
      try{
        const page=await fetch(`https://arischuang1688-sudo.github.io/open-sesame/data/dashboard.json?ts=${Date.now()}`,{cache:'no-store'});
        if(page.ok){
          const d=await page.json();
          const updated=Date.parse(d.updated_at||'');
          const started=Date.parse(run.run_started_at||run.created_at||'');
          if(updated && started && updated>=started){
            return res.status(200).json({...common,outcome:'published',published:true,done:true,failed:false,percent:100,label:'GitHub Pages 更新完成',updated_at:d.updated_at});
          }
        }
      }catch{}
      return res.status(200).json({...common,outcome:'publishing',published:false,done:false,failed:false,percent:99,label:'GitHub Pages 發布中'});
    }
    return res.status(200).json({...common,outcome:'running',published:false,done:false,failed:false,percent:stage.percent,label:stage.label});
  }catch(e){
    return res.status(500).json({error:e.message||'Status lookup failed'});
  }
}
