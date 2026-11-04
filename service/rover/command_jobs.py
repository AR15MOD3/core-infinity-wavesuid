"""Bounded service-owned command work; progress receipts never re-execute a command."""
import asyncio
from rover.work_owner import owner as work_owner
from dataclasses import dataclass, field
import secrets
import time
from copy import deepcopy

MAX_JOBS = 64
TTL = 600
FIRST_WAIT = 6.0

class ReceiptSink(list):
    def __init__(self, changed):
        super().__init__(); self.changed = changed
    def extend(self, items):
        items = list(items)
        if len(self) + len(items) > 256:
            raise RuntimeError('command receipt limit')
        super().extend(items); self.changed.set()
    def append(self, item):
        self.extend([item])

@dataclass
class Job:
    owner: tuple
    request_id: str
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: object = None
    done: bool = False
    finished: float = 0
    state: str = 'pending'
    summary: str = '命令仍在处理，尚未确认完成；不要重复执行。'
    result: dict = field(default_factory=dict)
    def __post_init__(self):
        self.sink = ReceiptSink(self.changed)

class CommandJobs:
    def __init__(self):
        self.jobs = {}; self.closing = False
    def prune(self):
        now = time.monotonic()
        for key, job in list(self.jobs.items()):
            if job.done and now - job.finished > TTL:
                del self.jobs[key]
    async def submit(self, owner, request_id, execute, render):
        if self.closing:
            return self.error('服务正在停止；没有执行新命令。')
        self.prune()
        # Exact-message replay and any unresolved work for this audience reuse a ticket.
        for key, job in self.jobs.items():
            if job.owner == owner and (job.request_id == request_id or not job.done):
                reply = await self.read(key, owner, 0, render)
                reply['new_command_started'] = False
                reply['existing_task'] = True
                return reply
        if len(self.jobs) >= MAX_JOBS:
            return self.error('命令任务容量已满；没有执行新命令，请稍后再试。')
        key = secrets.token_urlsafe(24)
        job = Job(owner, request_id); self.jobs[key] = job
        async def work():
            from rover.login_receipts import current_job
            context = current_job.set(job)
            try:
                job.result = await execute(job.sink)
                if job.result.get('unmatched'):
                    job.state = 'unmatched'; job.summary = '未匹配到指令；请用 ww帮助核对格式。'
                elif not job.sink:
                    job.state = 'no_receipt'
                    job.summary = '处理已结束，但插件没有返回正文；结果未确认，不要自动重复执行账号操作。'
                else:
                    job.state = 'completed'
                    job.summary = '命令处理结束；实际结果以回执正文为准，不代表登录或绑定成功。'
            except asyncio.CancelledError:
                job.state = 'cancelled'
                job.summary = '任务因服务收尾中止；账号操作结果未确认，不要自动重试。'
                raise
            except Exception:
                job.state = 'failed'
                job.summary = '命令处理异常；已有回执保留，账号操作结果未确认，不要自动重试。'
                raise
            finally:
                current_job.reset(context)
                job.done = True; job.finished = time.monotonic(); job.changed.set()
        job.task = work_owner.spawn(work, name='waves-command-receipt')
        try:
            await asyncio.wait_for(job.changed.wait(), FIRST_WAIT)
        except asyncio.TimeoutError:
            pass
        if not job.done:
            await asyncio.wait({job.task}, timeout=0.02)
        # Cancelling an HTTP waiter does not cancel or replay owned command work.
        reply = await self.read(key, owner, 0, render)
        reply['new_command_started'] = True
        return reply
    @staticmethod
    def error(text):
        return {'ok': False, 'state': 'rejected', 'summary': text, 'segments': [],
                'new_command_started': False}
    async def read(self, key, owner, cursor, render):
        self.prune(); job = self.jobs.get(key)
        if job is None or job.owner != owner:
            return self.error('任务不存在、已过期或不属于当前用户与会话；没有执行任何命令。')
        if type(cursor) is not int or not 0 <= cursor <= len(job.sink):
            return self.error('任务回执游标无效；没有执行任何命令。')
        # Capture body, cursor and terminal state together, before render awaits.
        chunk = deepcopy(list(job.sink[cursor:])); end = len(job.sink)
        state, summary, done = job.state, job.summary, job.done
        matched = deepcopy(job.result.get('matched', []))
        try:
            segments = await asyncio.wait_for(render(chunk), 2.0) if chunk else []
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError): raise
            return {'ok': False, 'job_id': key, 'state': 'receipt_render_failed',
                    'summary': '命令回执转换失败；任务未重试，结果未确认。',
                    'segments': [], 'cursor': cursor, 'pending': not done,
                    'login_outcome': deepcopy(getattr(job,'login_outcome',None)),
                    'login_diagnostics': deepcopy(getattr(job,'login_diagnostics',None))}
        return {'ok': state not in ('failed', 'cancelled', 'unmatched', 'no_receipt'),
                'job_id': key, 'state': state, 'summary': summary,
                'segments': segments, 'cursor': end, 'pending': not done,
                'matched': matched, 'delivered_to_user': False,
                'login_outcome': deepcopy(getattr(job,'login_outcome',None)),
                    'login_diagnostics': deepcopy(getattr(job,'login_diagnostics',None))}
    async def close(self):
        self.closing = True
        owned = [j.task for j in self.jobs.values() if j.task is not None and not j.task.done()]
        for task in owned: task.cancel()
        if owned:
            _, pending = await asyncio.wait(owned, timeout=2.0)
            if pending:
                raise RuntimeError('command tasks did not drain; ownership retained')
        for job in self.jobs.values():
            if job.task is not None and job.task.done() and not job.task.cancelled():
                job.task.exception()

jobs = CommandJobs()
