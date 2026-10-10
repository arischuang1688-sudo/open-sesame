import {randomUUID} from 'node:crypto';

export const COMMENTS_URL='https://api.github.com/repos/arischuang1688-sudo/open-sesame/issues/2/comments';
export const ANALYTICS_VERSION='2026-10-10.1';
export function analyticsHeaders(token){return {
  Authorization:`Bearer ${token}`,Accept:'application/vnd.github+json',
  'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json',
  'User-Agent':'open-sesame-analytics'
}}

// Log only bounded diagnostic fields, never credentials, payloads or visitor IDs.
export function analyticsFailure(operation,{response,event,code,github_message}={}){
  const status=response?.status||0;
  const remaining=response?.headers?.get('x-ratelimit-remaining');
  const retryAfter=response?.headers?.get('retry-after');
  code=code||(status===401?'GITHUB_AUTH_FAILED':
    status===429||(status===403&&(remaining==='0'||retryAfter))?'GITHUB_RATE_LIMITED':
    status===403?'GITHUB_PERMISSION_DENIED':status===404?'GITHUB_ISSUE_NOT_ACCESSIBLE':
    status?'GITHUB_HTTP_ERROR':'GITHUB_NETWORK_ERROR');
  const result={ok:false,code,error_id:randomUUID(),github_status:status,
    github_request_id:response?.headers?.get('x-github-request-id')||null};
  console.error(JSON.stringify({component:'analytics',version:ANALYTICS_VERSION,operation,event,
    ...result,github_message,required_permissions:response?.headers?.get('x-accepted-github-permissions')||null,
    retry_after:retryAfter,rate_limit_remaining:remaining}));
  return result;
}

export async function writeEvent(token,event,extra={}){
  if(!token)return analyticsFailure('write',{event,code:'GITHUB_TOKEN_MISSING'});
  try{
    const body=JSON.stringify({v:1,event,ts:new Date().toISOString(),...extra});
    const response=await fetch(COMMENTS_URL,{method:'POST',headers:analyticsHeaders(token),
      body:JSON.stringify({body}),signal:AbortSignal.timeout(8000)});
    if(!response.ok){
      const detail=await response.json().catch(()=>({}));
      const safeMessages=new Set(['Resource not accessible by personal access token','Resource not accessible by integration','Bad credentials','Not Found','Validation Failed']);
      const github_message=safeMessages.has(detail.message)?detail.message:undefined;
      return analyticsFailure('write',{response,event,github_message});
    }
    const saved=await response.json();
    if(!saved.id)return analyticsFailure('write',{event,code:'GITHUB_INVALID_RESPONSE'});
    return {ok:true,comment_id:saved.id};
  }catch{return analyticsFailure('write',{event})}
}
