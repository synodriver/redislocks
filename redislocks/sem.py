# -*- coding:utf-8 -*-
"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import asyncio
import time
# __version_info__ = ("0", "2", "2")
from typing import Awaitable, Callable, Optional, Union

from redis.asyncio import Redis


class NotAvailable(Exception):
    """Raised when unable to acquire the Semaphore in non-blocking mode"""


class Semaphore:
    """
    Redis中可能存在的key: Namespace:AVAILABLE list 存放全部可用的token，有self.value个
    Namespace:GRABBED hash[str, float] token and acquire_time pair, 表示这些token正在被使用
    Namespace:EXISTS str, 表示这个锁已经存在
    """

    exists_val = "ok"

    def __init__(
        self,
        value: int,
        client: Optional[Redis] = None,
        namespace: Optional[str] = None,  # 区分不同的锁
        stale_client_timeout: Optional[float] = None,
        blocking: bool = True,
    ):
        self.client = client or Redis()
        if value < 1:
            raise ValueError("Semaphore initial value must be >= 0")
        self.value = value
        self.namespace = namespace if namespace else "SEMAPHORE"
        self.stale_client_timeout = stale_client_timeout
        self.is_use_local_time = False
        self.blocking = blocking
        self._local_tokens = list()

    async def _exists_or_init(self):
        old_key = await self.client.getset(self.check_exists_key, self.exists_val)
        if old_key:
            return False
        return await self._init()

    async def _init(self):
        await self.client.expire(self.check_exists_key, 10)
        async with self.client.pipeline() as pipe:
            pipe.multi()
            pipe.delete(self.grabbed_key, self.available_key)
            pipe.rpush(self.available_key, *range(self.value))
            await pipe.execute()
        await self.client.persist(self.check_exists_key)

    @property
    async def available_count(self):
        return await self.client.llen(self.available_key)

    async def acquire(
        self,
        timeout: int = 0,
        target: Optional[Callable[[str], Union[None, Awaitable[None]]]] = None,
    ):
        await self._exists_or_init()
        if self.stale_client_timeout is not None:
            await self.release_stale_locks()

        if self.blocking:
            pair = await self.client.blpop(self.available_key, timeout)
            if pair is None:
                raise NotAvailable
            token = pair[1]
        else:
            token = await self.client.lpop(self.available_key)
            if token is None:
                raise NotAvailable

        self._local_tokens.append(token)
        await self.client.hset(self.grabbed_key, token, await self.current_time)
        if target is not None:
            try:
                if asyncio.iscoroutinefunction(target):
                    await target(token)
                else:
                    target(token)
            finally:
                await self.signal(token)
        return token

    async def release_stale_locks(self, expires=10):
        token = self.client.getset(self.check_release_locks_key, self.exists_val)
        if token:
            return False
        await self.client.expire(self.check_release_locks_key, expires)
        try:
            for token, looked_at in (
                await self.client.hgetall(self.grabbed_key)
            ).items():
                timed_out_at = float(looked_at) + self.stale_client_timeout
                if timed_out_at < await self.current_time:
                    await self.signal(token)
        finally:
            await self.client.delete(self.check_release_locks_key)

    async def _is_locked(self, token):
        return await self.client.hexists(self.grabbed_key, token)

    async def has_lock(self):
        for t in self._local_tokens:
            if await self._is_locked(t):
                return True
        return False

    async def release(self):
        if not await self.has_lock():
            return False
        return await self.signal(self._local_tokens.pop())

    async def reset(self):
        await self._init()

    async def signal(self, token):
        if token is None:
            return None
        async with self.client.pipeline() as pipe:
            pipe.multi()
            pipe.hdel(self.grabbed_key, token)
            pipe.lpush(self.available_key, token)
            await pipe.execute()
            return token

    def get_namespaced_key(self, suffix):
        return "{0}:{1}".format(self.namespace, suffix)

    @property
    def check_exists_key(self):
        return self._get_and_set_key("_exists_key", "EXISTS")

    @property
    def available_key(self):
        return self._get_and_set_key("_available_key", "AVAILABLE")

    @property
    def grabbed_key(self):
        return self._get_and_set_key(
            "_grabbed_key", "GRABBED"
        )  # 在redis中表示已经被各个client获得的key

    @property
    def check_release_locks_key(self):
        return self._get_and_set_key("_release_locks_ley", "RELEASE_LOCKS")

    def _get_and_set_key(self, key_name, namespace_suffix):
        if not hasattr(self, key_name):
            setattr(self, key_name, self.get_namespaced_key(namespace_suffix))
        return getattr(self, key_name)

    @property
    async def current_time(self):
        if self.is_use_local_time:
            return time.time()
        return float(".".join(map(str, await self.client.time())))

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.release()
        return True if exc_type is None else False
