"""Semantic login completion, obtained from native persistence/exception facts."""
import asyncio
import functools
import sys
import time
from rover.login_receipts import current_job,qq_job


PREFIX='plugins.XutheringWavesUID.XutheringWavesUID.'
def replace_refs(original,replacement):
 for name,module in tuple(sys.modules.items()):
  if name.startswith(PREFIX) and module is not None:
   for field,value in tuple(vars(module).items()):
    if value is original:setattr(module,field,replacement)


def install():
 module=sys.modules.get(PREFIX+'wutheringwaves_login.login')
 if module is None:raise RuntimeError('native login module missing')
 api=module.waves_api
 original_api=api.login
 if not getattr(original_api,'__login_outcome__',False):
  @functools.wraps(original_api)
  async def api_login(*args,**kwargs):
   result=await original_api(*args,**kwargs)
   job=current_job.get()
   if qq_job(job) and (getattr(result,'success',None) is False or not getattr(result,'data',None)):
    job.login_outcome={'state':'failure','saved':False}
   return result
  api_login.__login_outcome__=True
  api.login=api_login
 original=module.add_cookie
 if not getattr(original,'__login_outcome__',False):
  @functools.wraps(original)
  async def saved(event,*args,**kwargs):
   result=await original(event,*args,**kwargs)
   job=current_job.get()
   if qq_job(job) and str(event.user_id)==job.owner[0]:
    user,_=result
    # Returned native WavesUser was fetched by the exact credential that
    # deal.add_cookie just saved. Never infer success from task=completed.
    if (user is not None and str(user.user_id)==job.owner[0]
        and str(user.bot_id)==str(event.bot_id) and getattr(user,'cookie',None)
        and not getattr(user,'status',False)):
     job.login_outcome={'state':'success','saved':True}
    else:job.login_outcome={'state':'unknown','saved':False}
   return result
  saved.__login_outcome__=True;replace_refs(original,saved)
 original_page=module.page_login
 if not getattr(original_page,'__login_outcome__',False):
  @functools.wraps(original_page)
  async def page(bot,event,*args,**kwargs):
   job=current_job.get();started=time.monotonic()
   if qq_job(job):job.login_attempt=True
   try:return await original_page(bot,event,*args,**kwargs)
   except asyncio.CancelledError:raise
   finally:
    if qq_job(job) and getattr(job,'login_outcome',None) is None:
     job.login_outcome={'state':'timeout' if time.monotonic()-started>=179 else 'unknown','saved':False}
  page.__login_outcome__=True;replace_refs(original_page,page)

 from rover.public_login import install as install_public_login
 install_public_login(module,replace_refs)
