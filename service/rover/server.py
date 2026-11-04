"""启动/关闭钩子注册与执行。

三类钩子：
- on_core_start_before: 阻塞式，服务开始接受连接前必须跑完（数据库迁移等）
- on_core_start: 启动后执行
- on_core_shutdown: 关闭前执行

同一 priority 的钩子并发执行，全部完成后再进入下一 priority；单个钩子失败只记日志。
"""

import asyncio
import inspect
import sys
from rover.work_owner import WorkOwner

# Cleanup hooks have their own owner: normal ingress is already sealed at shutdown.
hook_owner = WorkOwner()
from dataclasses import dataclass, field
from itertools import groupby
from typing import Any, Callable, List, Optional, Set, TypeVar, overload

from rover.logger import logger

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class _DefHook:
    priority: int
    func: Callable = field(compare=False)

    def __hash__(self) -> int:
        return hash(self.func)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _DefHook):
            return self.func is other.func
        return NotImplemented

    def __lt__(self, other: "_DefHook") -> bool:
        return self.priority < other.priority


core_start_def: Set[_DefHook] = set()
core_start_before_def: Set[_DefHook] = set()
core_shutdown_def: Set[_DefHook] = set()


@overload
def on_core_start(func: F, /) -> F: ...


@overload
def on_core_start(func: None = None, /, priority: int = 0) -> Callable[[F], F]: ...


def on_core_start(func: Optional[Callable] = None, /, priority: int = 0):
    def decorator(f: Callable) -> Callable:
        core_start_def.add(_DefHook(priority=priority, func=f))
        return f

    if func is not None:
        return decorator(func)
    return decorator


@overload
def on_core_start_before(func: F, /) -> F: ...


@overload
def on_core_start_before(func: None = None, /, priority: int = 0) -> Callable[[F], F]: ...


def on_core_start_before(func: Optional[Callable] = None, /, priority: int = 0):
    def decorator(f: Callable) -> Callable:
        core_start_before_def.add(_DefHook(priority=priority, func=f))
        return f

    if func is not None:
        return decorator(func)
    return decorator


@overload
def on_core_shutdown(func: F, /) -> F: ...


@overload
def on_core_shutdown(func: None = None, /, priority: int = 0) -> Callable[[F], F]: ...


def on_core_shutdown(func: Optional[Callable] = None, /, priority: int = 0):
    def decorator(f: Callable) -> Callable:
        core_shutdown_def.add(_DefHook(priority=priority, func=f))
        return f

    if func is not None:
        return decorator(func)
    return decorator


async def _invoke_hook(function):
    result = (function() if inspect.iscoroutinefunction(function)
              else await asyncio.to_thread(function))
    if inspect.isawaitable(result):
        await result


async def _execute(hooks: Set[_DefHook], tag: str) -> None:
    for _, group in groupby(sorted(hooks), key=lambda h: h.priority):
        tasks = []
        for hook in group:
            # The owner retains the real task even if gather's observer is cancelled.
            tasks.append(hook_owner.spawn(lambda f=hook.func: _invoke_hook(f), name='waves-hook'))
        results = await asyncio.gather(*(asyncio.shield(task) for task in tasks),
                                       return_exceptions=True)
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            errors[0].hook_errors = tuple(errors)
            raise errors[0]


async def core_start_before_execute() -> None:
    await _execute(core_start_before_def, "启动前钩子")


async def core_start_execute() -> None:
    await _execute(core_start_def, "启动钩子")


async def core_shutdown_execute() -> None:
    try:
        await _execute(core_shutdown_def, "关闭钩子")
    finally:
        primary = sys.exception()
        try:
            await hook_owner.close()
        except BaseException as error:
            if primary is None:
                raise
            primary.hook_cleanup_error = error
