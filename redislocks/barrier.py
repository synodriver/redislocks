# -*- coding: utf-8 -*-
import asyncio
from enum import IntEnum
from typing import Dict, Optional

from redis.asyncio import Redis

from redislocks.utils import ensure_str


class BrokenBarrierError(RuntimeError):
    """Barrier is broken by barrier.abort() call."""


class _BarrierState(IntEnum):
    FILLING = 0
    DRAINING = 1
    RESETTING = 2  # never, actually impossible
    BROKEN = 3


class Barrier:
    """Asyncio equivalent to threading.Barrier

    Implements a Barrier primitive.
    Useful for synchronizing a fixed number of tasks at known synchronization
    points. Tasks block on 'wait()' and are simultaneously awoken once they
    have all made their call.
    """

    def __init__(
        self,
        parties: int,
        client: Optional[Redis] = None,
        namespace: str = "BARRIER",
    ):
        """Create a barrier, initialised to 'parties' tasks."""
        if parties < 1:
            raise ValueError("parties must be > 0")
        self.client = client or Redis()
        self.namespace = namespace

        self._parties = parties  # 属于一个namespace的实例，此字段应该一致
        # self._state = _BarrierState.FILLING  # 放redis NAMESPACE:STATE
        self._count = 0  # count tasks in Barrier llen(self.waiter_key)
        self.waiter_key = self.get_namespaced_key("WAITER")
        self.pubsub_key = self.get_namespaced_key("PUBSUB")  # :OK :ERR
        self.state_key = self.get_namespaced_key("STATE")
        self._listen_task = asyncio.create_task(self._listen_events())
        self._waiters = {}  # type: Dict[str, asyncio.Future]
        self._wait_script = self.client.register_script(
            """
            local namespace = KEYS[1]
            local parties = tonumber(KEYS[2])
            local randkey = KEYS[3]
            local waiter_key = namespace .. ":WAITER"
            local pubsub_key = namespace .. ":PUBSUB"
            local state_key = namespace .. ":STATE"
            local state = redis.call("SET", state_key, 0, "NX", "GET")
            if state == nil then
                state = 0
            end
            local current_len = redis.call("LLEN", waiter_key)
            redis.call("RPUSH", waiter_key, randkey)
            if (current_len+1)==parties then
                redis.call("SET", state_key, 1)
                while true do
                    local token = redis.call("LPOP", waiter_key)
                    if not token then
                        break
                    end
                    redis.call("PUBLISH", pubsub_key..":OK", token)
                end
            end
            return current_len
            """
        )
        self._abort_script = self.client.register_script(
            """
            local namespace = KEYS[1]
            local err = tonumber(KEYS[2])
            local should_set = tonumber(KEYS[3])
            local waiter_key = namespace .. ":WAITER"
            local pubsub_key = namespace .. ":PUBSUB"
            local state_key = namespace .. ":STATE"
            local suffix = ""
            if err==1 then
                suffix = ":ERR"
                if should_set==1 then
                    redis.call("SET", state_key, 3)
                end
            else
                suffix = ":OK"
            end
            
            while true do
                local token = redis.call("LPOP", waiter_key)
                if not token then
                    break
                end
                redis.call("PUBLISH", pubsub_key..suffix, token)
            end
            """
        )

    # def __repr__(self):
    #     res = super().__repr__()
    #     extra = f"{self._state.value}"
    #     if not self.broken:
    #         extra += f", waiters:{self.n_waiting}/{self.parties}"
    #     return f"<{res[1:-1]} [{extra}]>"
    async def _listen_events(self):
        """
        监听redis中的key变动 从而知道什么时候可以获取锁 要抛出异常，必须使用这种
        :return:
        """
        async with self.client.pubsub() as pubsub:
            await pubsub.psubscribe(
                f"{self.pubsub_key}*",
            )  # pattern支持set_excption，如果channel不一样
            async for event in pubsub.listen():
                if (
                    ensure_str(event["type"]) == "pmessage"
                    and ensure_str(event["channel"]) == f"{self.pubsub_key}:OK"
                ):
                    token = ensure_str(event["data"])
                    if token in self._waiters:
                        waiter = self._waiters[token]
                        waiter.set_result(None)
                if (
                    ensure_str(event["type"]) == "pmessage"
                    and ensure_str(event["channel"]) == f"{self.pubsub_key}:ERR"
                ):
                    token = ensure_str(event["data"])
                    if token in self._waiters:
                        waiter = self._waiters[token]
                        waiter.set_exception(
                            BrokenBarrierError("Abort or reset of barrier")
                        )

    async def __aenter__(self):
        # wait for the barrier reaches the parties number
        # when start draining release and return index of waited task
        return await self.wait()

    async def __aexit__(self, *args):
        pass

    async def wait(self):
        """Wait for the barrier.

        When the specified number of tasks have started waiting, they are all
        simultaneously awoken.
        Returns an unique and individual index number from 0 to 'parties-1'.
        """
        fut = asyncio.get_running_loop().create_future()
        token = await self.current_time  # type: ignore
        try:
            self._waiters[token] = fut
            current_len = await self._wait_script(
                [self.namespace, self._parties, token]
            )
            await fut
            return current_len
        except asyncio.CancelledError:
            err = None
            while True:
                try:
                    await self.client.lrem(
                        self.waiter_key, 1, token
                    )  # notify就是pub个东西 后台有task pubsub
                    break
                except asyncio.CancelledError as e:
                    err = e
            if err is not None:
                try:
                    raise err
                finally:
                    err = None
            raise
        finally:
            del self._waiters[token]

    async def reset(self):
        """Reset the barrier to the initial state.

        Any tasks currently waiting will get the BrokenBarrier exception
        raised.
        """
        await self._abort_script([self.namespace, 1, 0])
        await self.client.delete(self.state_key)

    async def abort(self):
        """Place the barrier into a 'broken' state.

        Useful in case of error.  Any currently waiting tasks and tasks
        attempting to 'wait()' will have BrokenBarrierError raised.
        """
        await self._abort_script([self.namespace, 1, 1])

    @property
    def parties(self):
        """Return the number of tasks required to trip the barrier."""
        return self._parties

    @property
    async def n_waiting(self):
        """Return the number of tasks currently waiting at the barrier."""
        return await self.client.llen(self.waiter_key)

    @property
    async def broken(self) -> bool:
        """Return True if the barrier is in a broken state."""
        return int(await self.client.get(self.state_key) or 0) == _BarrierState.BROKEN

    @property
    async def current_time(self) -> str:
        return ".".join(map(str, await self.client.time()))

    def get_namespaced_key(self, suffix):
        return "{0}:{1}".format(self.namespace, suffix)

    async def aclose(self):
        self._listen_task.cancel()
        try:
            await self._listen_task
        except asyncio.CancelledError:
            pass
        self._listen_task = None
        await self.reset()
        await self.client.aclose()
