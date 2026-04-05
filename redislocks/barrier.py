# -*- coding: utf-8 -*-
import asyncio
import logging
import uuid
from enum import IntEnum
from typing import Dict, Optional

from redis.asyncio import Redis
from redis.exceptions import ConnectionError, RedisError, TimeoutError

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

    _logger = logging.getLogger("redislocks.barrier")

    def __init__(
        self,
        parties: int,
        client: Optional[Redis] = None,
        namespace: str = "BARRIER",
        reconnect_base_delay: float = 0.5,
        reconnect_max_delay: float = 30.0,
        reconnect_max_retries: Optional[int] = None,
    ):
        """Create a barrier, initialised to 'parties' tasks.

        :param parties: number of tasks required to trip the barrier
        :param client: redis client
        :param namespace: barrier的命名空间，相同的视为同一个barrier
        :param reconnect_base_delay: pubsub重连的基础延迟(秒)，每次翻倍直到max_delay
        :param reconnect_max_delay: pubsub重连的最大延迟(秒)
        :param reconnect_max_retries: pubsub最大重连次数，None为无限重试
        """
        if parties < 1:
            raise ValueError("parties must be > 0")
        self.client = client or Redis()
        self.namespace = namespace
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._reconnect_max_retries = reconnect_max_retries

        self._parties = parties  # 属于一个namespace的实例，此字段应该一致
        # self._state = _BarrierState.FILLING  # 放redis NAMESPACE:STATE
        self._count = 0  # count tasks in Barrier llen(self.waiter_key)
        self.waiter_key = self.get_namespaced_key("WAITER")
        self.pubsub_key = self.get_namespaced_key("PUBSUB")  # :OK :ERR
        self.state_key = self.get_namespaced_key("STATE")
        self._ready = asyncio.Event()
        self._listen_task = asyncio.create_task(self._listen_events())
        self._waiters = {}  # type: Dict[str, asyncio.Future]
        self._wait_script = self.client.register_script("""
            local namespace = KEYS[1]
            local parties = tonumber(KEYS[2])
            local randkey = KEYS[3]
            local waiter_key = namespace .. ":WAITER"
            local pubsub_key = namespace .. ":PUBSUB"
            local state_key = namespace .. ":STATE"
            local state = redis.call("SET", state_key, 0, "NX", "GET")
            if state == nil then
                state = 0
            else
                state = tonumber(state)
            end
            if state == 3 then
                return -1
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
                redis.call("DEL", state_key)
            end
            return current_len
            """)
        self._abort_script = self.client.register_script("""
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
            """)

    # def __repr__(self):
    #     res = super().__repr__()
    #     extra = f"{self._state.value}"
    #     if not self.broken:
    #         extra += f", waiters:{self.n_waiting}/{self.parties}"
    #     return f"<{res[1:-1]} [{extra}]>"
    async def _listen_events(self):
        """
        监听redis中的key变动 从而知道什么时候可以获取锁 要抛出异常，必须使用这种
        内置重连机制，断线后自动以指数退避策略重连
        :return:
        """
        retries = 0
        delay = self._reconnect_base_delay
        while True:
            pubsub = None
            try:
                pubsub = self.client.pubsub()
                await pubsub.psubscribe(
                    f"{self.pubsub_key}*",
                )  # pattern支持set_excption，如果channel不一样
                # 连接成功，重置重试计数和延迟
                retries = 0
                delay = self._reconnect_base_delay
                self._ready.set()
                self._logger.debug("pubsub psubscribed to %s*", self.pubsub_key)
                async for event in pubsub.listen():
                    if (
                        ensure_str(event["type"]) == "pmessage"
                        and ensure_str(event["channel"]) == f"{self.pubsub_key}:OK"
                    ):
                        token = ensure_str(event["data"])
                        if token in self._waiters:
                            waiter = self._waiters[token]
                            if not waiter.done():
                                waiter.set_result(None)
                    if (
                        ensure_str(event["type"]) == "pmessage"
                        and ensure_str(event["channel"]) == f"{self.pubsub_key}:ERR"
                    ):
                        token = ensure_str(event["data"])
                        if token in self._waiters:
                            waiter = self._waiters[token]
                            if not waiter.done():
                                waiter.set_exception(
                                    BrokenBarrierError("Abort or reset of barrier")
                                )
            except asyncio.CancelledError:
                # task被取消，正常退出，不重连
                raise
            except (ConnectionError, TimeoutError, RedisError, OSError) as e:
                retries += 1
                if (
                    self._reconnect_max_retries is not None
                    and retries > self._reconnect_max_retries
                ):
                    self._logger.error(
                        "pubsub reconnect failed after %d retries, giving up: %s",
                        retries - 1,
                        e,
                    )
                    return
                self._logger.warning(
                    "pubsub connection lost (attempt %d), reconnecting in %.1fs: %s",
                    retries,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._reconnect_max_delay)
            except Exception as e:
                self._logger.exception(
                    "unexpected error in _listen_events, reconnecting: %s", e
                )
                retries += 1
                if (
                    self._reconnect_max_retries is not None
                    and retries > self._reconnect_max_retries
                ):
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._reconnect_max_delay)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.aclose()
                    except Exception:
                        pass

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
        await self._ready.wait()
        fut = asyncio.get_running_loop().create_future()
        token = uuid.uuid4().hex
        try:
            self._waiters[token] = fut
            current_len = await self._wait_script(
                [self.namespace, self._parties, token]
            )
            if current_len == -1:
                raise BrokenBarrierError("Barrier is broken")
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
