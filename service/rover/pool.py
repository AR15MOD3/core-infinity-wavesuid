"""线程池。to_thread 是装饰器：被装饰函数为协程函数时在线程内新建事件循环执行。"""

import asyncio
from rover.work_owner import owner as work_owner
import functools
import inspect
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Coroutine, ParamSpec, TypeVar, cast, overload

T = TypeVar("T")
P = ParamSpec("P")

_executor = ThreadPoolExecutor(max_workers=10)


@overload
def to_thread(func: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, Awaitable[T]]: ...


@overload
def to_thread(func: Callable[P, T]) -> Callable[P, Awaitable[T]]: ...


def to_thread(func: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()

        def sync_worker():
            if inspect.iscoroutinefunction(func):
                new_loop = asyncio.new_event_loop()
                try:
                    asyncio.set_event_loop(new_loop)
                    return new_loop.run_until_complete(func(*args, **kwargs))
                finally:
                    new_loop.close()
            return func(*args, **kwargs)

        return await work_owner.in_executor(_executor, sync_worker)

    return cast(Callable[..., Awaitable[Any]], wrapper)


run_in_thread_pool = to_thread


async def close_pool():
    _executor.shutdown(wait=False,cancel_futures=False)
    workers=tuple(_executor._threads)
    deadline=asyncio.get_running_loop().time()+5
    while any(t.is_alive() for t in workers) and asyncio.get_running_loop().time()<deadline:
        await asyncio.sleep(.01)
    if any(t.is_alive() for t in workers):raise RuntimeError('service executor workers still alive')
