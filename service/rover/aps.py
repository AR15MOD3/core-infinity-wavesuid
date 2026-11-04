"""定时任务调度器。插件通过 `@scheduler.scheduled_job` / `scheduler.add_job` 注册。"""

import asyncio
from rover.work_owner import owner as work_owner
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from rover.logger import logger

# default 执行器保持 AsyncIOExecutor（协程任务必须在主事件循环跑），
# 阻塞型任务显式 add_job(..., executor="threadpool")
executor = ThreadPoolExecutor(10)
def _misfire_grace_time() -> int:
    from rover.config import core_config

    try:
        return int(core_config.get_config("misfire_grace_time") or 90)
    except Exception:
        return 90


job_defaults = {"misfire_grace_time": _misfire_grace_time(), "coalesce": True}
options = {
    "executors": {"threadpool": executor},
    "job_defaults": job_defaults,
    "timezone": "Asia/Shanghai",
}


class _Scheduler(AsyncIOScheduler):
    def add_job(self,func,*args,**kwargs):
        if isinstance(func,str):
            from apscheduler.util import ref_to_obj
            func=ref_to_obj(func)
        if not callable(func):raise TypeError('scheduled callable required')
        return super().add_job(work_owner.wrap_job(func),*args,**kwargs)

    def scheduled_job(self, *args, **kwargs):
        kwargs.pop("replace_existing", None)
        return super().scheduled_job(*args, **kwargs)


scheduler = _Scheduler()
scheduler.configure(**options)


async def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.start()
        logger.info("[调度器] 启动完成")


async def stop_scheduler() -> None:
    if scheduler.running:
        pool=getattr(executor,'_pool',None)
        workers=getattr(pool,'_threads',None)
        scheduler.shutdown(wait=False)
        if type(workers) is not set:raise RuntimeError('scheduled worker identities unavailable')
        workers=tuple(workers)
        deadline=asyncio.get_running_loop().time()+5
        while any(t.is_alive() for t in workers) and asyncio.get_running_loop().time()<deadline:
            await asyncio.sleep(.01)
        if any(t.is_alive() for t in workers):raise RuntimeError('scheduled workers still alive')
        logger.info("[调度器] 已关闭定时任务")
