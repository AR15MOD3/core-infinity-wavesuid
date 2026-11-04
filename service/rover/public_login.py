"""Owned external page-login checks. Never submits credentials or recreates a sent link."""
import asyncio,functools,re,time
import httpx
from rover.login_receipts import current_job,qq_job

REASONS=frozenset(('public_service_unavailable','public_session_expired','public_page_unconfirmed','public_result_timeout'))
def page_state(response):
 if response.status_code in (404,410):return 'expired'
 if response.status_code!=200:return 'unavailable'
 if len(response.content)>1048576:return 'unconfirmed'
 body=response.text
 if re.search(r'<input\b[^>]*\bid\s*=\s*["\x27]phone["\x27]',body,re.I):return 'ready'
 if any(v in body for v in ('登录已过期','登录链接已过期','链接已过期','会话已过期')):return 'expired'
 return 'unconfirmed'

def install(module,replace_refs):
 original=module.page_login_other
 if getattr(original,'__owned_external_login__',False):return
 @functools.wraps(original)
 async def guarded(bot,event,url):
  job=current_job.get()
  if not qq_job(job):return await original(bot,event,url)
  return await run(module,bot,event,url,job)
 guarded.__owned_external_login__=True
 replace_refs(original,guarded)

async def run(native,bot,event,url,job,*,client_factory=httpx.AsyncClient,clock=time.monotonic,sleep=asyncio.sleep):
 at_sender=bool(event.group_id)
 # Only fixed phase/status/count labels; never persist user/phone/token/URL/body.
 job.login_diagnostics={'stage':'creating','polls':0,'page_checks':0}
 def stage(value,code=None):
  job.login_diagnostics['stage']=value
  if code is not None:job.login_diagnostics['HTTP']=int(code)
 async def fail(reason):
  assert reason in REASONS
  stage(reason);job.login_outcome={'state':'timeout' if reason=='public_result_timeout' else 'failure','saved':False,'reason':reason}
  text={
   'public_service_unavailable':'外置登录服务暂时不可用，本次未确认登录保存成功；不要重复提交验证码。',
   'public_session_expired':'外置网页登录会话已失效，本次未确认登录保存成功；不会自动重新登录。请保留报错，不要重复提交验证码。',
   'public_page_unconfirmed':'外置登录页面状态无法确认，本次不发出可能失效的入口；请稍后检查服务。',
   'public_result_timeout':'网页登录等待已结束，尚未确认新的登录保存成功；不会自动重新登录。',
  }[reason]
  return await bot.send(text,at_sender=at_sender)
 try:
  async with client_factory(timeout=10,follow_redirects=False) as client:
   response=await client.post(url+'/waves/token',json={'bot_id':event.bot_id,'user_id':event.user_id},headers={'Content-Type':'application/json'})
   stage('created',response.status_code)
   if response.status_code!=200:return await fail('public_service_unavailable')
   token=response.json().get('token','')
   if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}',token):return await fail('public_service_unavailable')
   page_url=url+'/waves/i/'+token
   async def check_page():
    response=await client.get(page_url,headers={'Cache-Control':'no-cache','Pragma':'no-cache'})
    job.login_diagnostics['page_checks']+=1;stage('checking_page',response.status_code)
    return page_state(response)
   state=await check_page()
   if state!='ready':return await fail({'expired':'public_session_expired','unavailable':'public_service_unavailable','unconfirmed':'public_page_unconfirmed'}[state])
   await native.send_login(bot,event,page_url)
   stage('waiting_browser');deadline=clock()+180;next_check=clock()+15;errors=0;ck=did=''
   while clock()<deadline:
    response=await client.post(url+'/waves/get',json={'token':token})
    job.login_diagnostics['polls']+=1;stage('polling',response.status_code)
    if response.status_code!=200:
     errors+=1
     if errors>=3:return await fail('public_service_unavailable')
     await sleep(5);continue
    data=response.json()
    if not isinstance(data,dict):return await fail('public_service_unavailable')
    if data.get('ck'):
     ck,did=data['ck'],data.get('did','');break
    if clock()>=next_check:
     state=await check_page();next_check=clock()+15
     if state=='expired':return await fail('public_session_expired')
     if state=='unavailable':return await fail('public_service_unavailable')
     # An unfamiliar page is uncertain, not proof of expiry or success.
     if state=='unconfirmed':stage('page_unconfirmed')
    stage('waiting_browser');await sleep(1)
   if not ck:return await fail('public_result_timeout')
 except asyncio.CancelledError:raise
 except (httpx.HTTPError,ValueError,KeyError,TypeError):return await fail('public_service_unavailable')
 stage('saving')
 waves_user,bind_msg=await native.add_cookie(event,ck,did)
 if '成功' in bind_msg:await bot.send((' ' if at_sender else '')+bind_msg.rstrip('\n'),at_sender)
 if waves_user and isinstance(waves_user,native.WavesUser):
  stage('saved');return await native.login_success_msg(bot,event,waves_user)
 if '成功' in bind_msg:return
 return await bot.send(bind_msg or native.msg_error,at_sender=at_sender)
