"""Explicit service-owned work. No payload logging, replay or foreign task census."""
import asyncio
import contextvars
import functools
import hashlib
import importlib.util
import inspect
import os
from pathlib import Path
import threading

class WorkOwner:
    def __init__(self,evidence=None):
        self.evidence=evidence;self.lock=threading.RLock();self.closing=False
        self.pending={};self.failures=[];self.limit=512

    def _admit(self):
        with self.lock:
            if self.closing:raise RuntimeError('service work admission closed')
            if len(self.pending)>=self.limit:raise RuntimeError('service work capacity')
            identity=object()
            token=None if self.evidence is None else self.evidence.begin('plugin_background')
            self.pending[identity]=[None,token]
            return identity

    def _done(self,identity,error=None):
        with self.lock:
            row=self.pending[identity]
            if self.evidence is not None:
                outcome='RETURNED' if error is None else 'CANCELLED' if isinstance(error,asyncio.CancelledError) else 'FAILED'
                try:self.evidence.finish(row[1],outcome)
                except BaseException as failure:
                    self.failures.append(failure)
                    if error is not None:error.add_note('service evidence failed; original error retained')
                    return  # Uncommitted terminal remains owned.
            if error is not None and not isinstance(error,asyncio.CancelledError):self.failures.append(error)
            del self.pending[identity]

    def spawn(self,factory,*,name=None):
        identity=self._admit()
        try:
            coroutine=factory()
            if not inspect.iscoroutine(coroutine):raise TypeError('coroutine factory required')
            try:task=asyncio.create_task(coroutine,name=name)
            except BaseException:coroutine.close();raise
        except BaseException as error:self._done(identity,error);raise
        with self.lock:self.pending[identity][0]=task
        def completed(done):
            try:done.result()
            except BaseException as error:self._done(identity,error)
            else:self._done(identity)
        task.add_done_callback(completed)
        return task

    async def in_executor(self,executor,function):
        identity=self._admit();context=contextvars.copy_context()
        try:future=executor.submit(context.run,function)
        except BaseException as error:self._done(identity,error);raise
        with self.lock:self.pending[identity][0]=future
        def completed(done):
            try:done.result()
            except BaseException as error:self._done(identity,error)
            else:self._done(identity)
        future.add_done_callback(completed)
        # Cancellation of a waiter does not cancel or retire the real worker.
        observer=asyncio.wrap_future(future)
        observer.add_done_callback(lambda f:f.exception() if not f.cancelled() else None)
        return await asyncio.shield(observer)

    def wrap_job(self,function):
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def asynchronous(*args,**kwargs):
                task=self.spawn(lambda:function(*args,**kwargs),name='waves-scheduled-work')
                return await asyncio.shield(task)
            return asynchronous
        @functools.wraps(function)
        def synchronous(*args,**kwargs):
            identity=self._admit()
            try:result=function(*args,**kwargs)
            except BaseException as error:self._done(identity,error);raise
            self._done(identity);return result
        return synchronous

    def seal(self):
        with self.lock:self.closing=True

    def state(self):
        with self.lock:return {'accepting':not self.closing,'pending':len(self.pending),'failed':len(self.failures),
            'drained':self.closing and not self.pending,
            'evidence':None if self.evidence is None else self.evidence.capacity_state()}

    async def close(self,timeout=5):
        self.seal();deadline=asyncio.get_running_loop().time()+timeout
        while self.state()['pending'] and asyncio.get_running_loop().time()<deadline:
            await asyncio.sleep(.01)
        state=self.state()
        if state['pending']:raise RuntimeError('service work still owned; no false retirement')
        if self.evidence is not None and not self.evidence.stop():raise RuntimeError('service evidence incomplete')
        if self.failures:raise RuntimeError('service work failed; failure responsibility retained') from self.failures[0]

owner=WorkOwner()

def configure():
    """Called under DataLease, before importing the service app/plugins."""
    source=os.environ.get('ROVER_TEV_SOURCE');expected=os.environ.get('ROVER_TEV_SHA256')
    if not source and not expected:return
    if owner.evidence is not None or owner.pending:raise RuntimeError('service evidence already bound or work already accepted')
    path=Path(source or '')
    if not path.is_absolute() or not path.is_file() or path.is_symlink():raise ValueError('explicit evidence source required')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('evidence source changed')
    spec=importlib.util.spec_from_file_location('waves_pinned_runtime_evidence',path)
    module=importlib.util.module_from_spec(spec)
    # Execute exactly the verified bytes, not a second mutable path read.
    exec(compile(raw,str(path),'exec'),module.__dict__)
    root=Path(os.environ['ROVER_DATA_DIR'])
    owner.evidence=module.create_for_bot(root,capacity_policy='continue_uncovered')
