# -*- coding: utf-8 -*-
import asyncio
import uuid
from typing import Dict, Optional

from redis.asyncio import Redis

# from redis.asyncio.lock import Lock
from redislocks.lock import Lock
from redislocks.utils import ensure_str


class Condition:
    """Asynchronous Distribute redis equivalent to asyncio.Condition.

    Distribute condition powered by Redis.

    NAMESPACE:WAITER somebody rpush a token to this
    NAMESPACE:WAITERPOP somebody is waiting here using blpop, when notify is called, caller pop a token from this and rpush it to NAMESPACE:WAITERPOP
    """

    def __init__(
        self,
        client: Optional[Redis] = None,
        lock: Optional[Lock] = None,
        namespace: str = "CONDITION",
    ):
        self.client = client or Redis()
        self._lock = lock or Lock(self.client, f"{namespace}:LOCK")

        self.locked = self._lock.locked
        self.acquire = self._lock.acquire
        self.release = self._lock.release

        self.namespace = namespace
        self.waiter_key = self.get_namespaced_key("WAITER")
        self.pubsub_key = self.get_namespaced_key("PUBSUB")

        self._waiters = {}  # type: Dict[str, asyncio.Future]
        # Bug fix #5: use ARGV[1] for parameter n instead of KEYS[2]
        self._notify_script = self.client.register_script(
            """
        local namespace = KEYS[1]
        local n = ARGV[1] -- push times
        local waiter_key = namespace .. ":WAITER"
        local pubsub_key = namespace .. ":PUBSUB"
        
        for i=1,n do
            local token = redis.call("LPOP", waiter_key)
            if token then
                redis.call("PUBLISH", pubsub_key, token)
            else
                break
            end
        end
        """
        )
        self._listen_task = asyncio.create_task(self._listen_events())

    async def __aenter__(self):
        await self.acquire()
        return None

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()

    async def _listen_events(self):
        """
        监听redis中的key变动 从而知道什么时候可以获取锁
        :return:
        """
        async with self.client.pubsub() as pubsub:
            await pubsub.subscribe(
                f"{self.pubsub_key}",
            )
            async for event in pubsub.listen():
                if (
                    ensure_str(event["type"]) == "message"
                    and ensure_str(event["channel"]) == self.pubsub_key
                ):
                    token = ensure_str(event["data"])
                    if token in self._waiters:
                        waiter = self._waiters[token]
                        # Bug fix #4: check if future is already done before
                        # setting result, otherwise InvalidStateError crashes
                        # this listener task and all future notifications break
                        if not waiter.done():
                            waiter.set_result(None)

    async def wait(self):
        if not await self._lock.has_token():
            raise RuntimeError("cannot wait on un-acquired lock")
        # 这里不用担心被其他进程干扰，锁已经由本进程锁定
        fut = asyncio.get_running_loop().create_future()
        # Bug fix #3: add uuid to token to guarantee uniqueness even when
        # multiple waiters call wait() at the same Redis TIME microsecond
        token: str = f"{await self.current_time}:{uuid.uuid4().hex}"  # type: ignore
        # Bug fix #1: register waiter in local dict AND push to Redis BEFORE
        # releasing the lock. This prevents a race where another task acquires
        # the lock and calls notify() before we've registered, causing a lost
        # wakeup.
        self._waiters[token] = fut
        await self.client.rpush(self.waiter_key, token)
        await self.release()
        try:
            try:
                try:
                    await fut
                    return True
                finally:
                    self._waiters.pop(token, None)
            except asyncio.CancelledError:
                err = None  # fixme 这里也可能浪费notify  fut完成而被cancel,或者未完成而被cancel，正好错过一次pub
                while True:
                    try:
                        removed = await self.client.lrem(
                            self.waiter_key, 1, token
                        )  # notify就是pub个东西 后台有task pubsub
                        break
                    except asyncio.CancelledError as e:
                        err = e
                if removed == 0:
                    # 如果 LREM 返回 0，说明 token 已经被 notify 弹走了。
                    # 但是我们被取消了，没人会处理这个信号。
                    # 必须把这次机会“转让”给下一个人。
                    while True:
                        try:
                            await self._notify(1)
                            break
                        except asyncio.CancelledError as e:
                            err = e
                if err is not None:
                    try:
                        raise err
                    finally:
                        err = None
                raise
                # fixme: 这是个坑 走到这里有4种可能，其中3的可能性最大，赌了
                # 1 rpush没执行完 这种情况下什么都不用做
                # 2 rpush执行完成
                # 3 blpop没执行完 和2一样，此时应该执行self._cancelwait_script([self.namespace, token])
                # 不一定可以删到东西，因为在这期间可能被别人blpop了
                # 4 blpop执行完成 此时，凭空丢失了一次notify，得想办法调用self._notify(1)
                # if not await self._cancelwait_script([self.namespace, token]):
                #     # 删除token失败，返回0
                #     # 如果返回0，说明blpop已经成功，这里取消后notify就消失了一次，需要再次notify一个
                #     should_renotify = True
                # raise

            finally:
                # Must re-acquire lock even if wait is cancelled.
                # We only catch CancelledError here, since we don't want any
                # other (fatal) errors with the future to cause us to spin.
                err = None
                while True:
                    try:
                        await self.acquire()
                        break
                    except asyncio.CancelledError as e:
                        err = e

                if err is not None:
                    try:
                        raise err  # Re-raise most recent exception instance.
                    finally:
                        err = None  # Break reference cycles.
        except BaseException:
            # Any error raised out of here _may_ have occurred after this Task
            # believed to have been successfully notified.
            # Make sure to notify another Task instead.  This may result
            # in a "spurious wakeup", which is allowed as part of the
            # Condition Variable protocol.
            if fut.done() and not fut.cancelled():
                err = None
                while True:
                    try:
                        await self._notify(1)
                        break
                    except asyncio.CancelledError as e:
                        err = e
                if err is not None:
                    try:
                        raise err
                    finally:
                        err = None
            raise

    async def wait_for(self, predicate):
        """Wait until a predicate becomes true.

        The predicate should be a callable whose result will be
        interpreted as a boolean value.  The method will repeatedly
        wait() until it evaluates to true.  The final predicate value is
        the return value.
        """
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        while not result:
            await self.wait()
            result = predicate()
            if asyncio.iscoroutine(result):
                result = await result
        return result

    async def notify(self, n=1):
        """By default, wake up one task waiting on this condition, if any.
        If the calling task has not acquired the lock when this method
        is called, a RuntimeError is raised.

        This method wakes up n of the tasks waiting for the condition
         variable; if fewer than n are waiting, they are all awoken.

        Note: an awakened task does not actually return from its
        wait() call until it can reacquire the lock. Since notify() does
        not release the lock, its caller should.
        """
        if not await self._lock.has_token():
            raise RuntimeError("cannot notify on un-acquired lock")
        await self._notify(n)

    async def _notify(self, n):
        await self._notify_script(keys=[self.namespace], args=[n])

    async def notify_all(self):
        """Wake up all tasks waiting on this condition. This method acts
        like notify(), but wakes up all waiting tasks instead of one. If the
        calling task has not acquired the lock when this method is called,
        a RuntimeError is raised.
        """
        n = await self.client.llen(self.waiter_key)
        await self.notify(n)

    def get_namespaced_key(self, suffix):
        return "{0}:{1}".format(self.namespace, suffix)

    @property
    async def current_time(self) -> str:
        return ".".join(map(str, await self.client.time()))

    async def reset(self):
        await self._lock.reset()
        await self.client.delete(self.waiter_key)

    async def aclose(self):
        self._listen_task.cancel()
        try:
            await self._listen_task
        except asyncio.CancelledError:
            pass
        self._listen_task = None
        await self.reset()
        await self._lock.aclose()

    def __del__(self):
        if self._listen_task is not None:
            self._listen_task.cancel()
            self._listen_task = None
