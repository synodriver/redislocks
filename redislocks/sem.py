# -*- coding:utf-8 -*-
"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import asyncio
from typing import Awaitable, Callable, List, Optional, Union

from redis.asyncio import Redis

from redislocks.exceptions import NotAvailable


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
        namespace: str = "SEMAPHORE",  # 区分不同的锁
        stale_client_timeout: Optional[float] = None,
        blocking: bool = True,
    ):
        """

        :param value: 信号量容量
        :param client: redis client
        :param namespace: lock的命名空间，相同的视为同一把锁，使用相同的redis key
        :param stale_client_timeout:
        :param blocking:
        """
        self.client = client or Redis()
        if value < 1:
            raise ValueError("Semaphore initial value must be >= 1")
        self.value = value
        self.namespace = namespace
        self.stale_client_timeout = stale_client_timeout
        self.is_use_local_time = False
        self.blocking = blocking
        self._local_tokens = list()  # type: List[Union[str, bytes]]

    async def _exists_or_init(self):
        old_key = await self.client.set(
            self.check_exists_key, self.exists_val, get=True
        )
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

    async def release_all(self):
        for _ in range(len(self._local_tokens)):
            await self.release()

    @property
    async def available_count(self):
        return await self.client.llen(self.available_key)

    async def acquire(
        self,
        timeout: int = 0,
        target: Optional[Callable[[str], Union[None, Awaitable[None]]]] = None,
    ):
        """

        :param timeout: 获取信号量的超时时间
        :param target: 由sem保护的函数，执行完成后释放sem
        :return:
        """
        await self._exists_or_init()
        if self.stale_client_timeout is not None:
            await self.release_stale_locks()

        if self.blocking:
            pair = await self.client.blpop(self.available_key, timeout)  # type: ignore
            if pair is None:
                raise NotAvailable
            token = pair[1]
        else:
            token = await self.client.lpop(self.available_key)  # type: ignore
            if token is None:
                raise NotAvailable

        self._local_tokens.append(token)
        # 已经到了这一步了，可万万不能被取消，否则会破坏状态机，致敬传奇耐取消王
        err = None
        while True:
            try:
                await self.client.hset(self.grabbed_key, token, await self.current_time)  # type: ignore
                break
            except asyncio.CancelledError as e:
                err = e
        if err is not None:
            try:
                raise err
            finally:
                err = None  # 打破循环引用
        if target is not None:
            try:
                if asyncio.iscoroutinefunction(target):
                    await target(token)
                else:
                    target(token)
            finally:
                self._local_tokens.remove(token)
                # await asyncio.shield(self.signal(token))
                await self.signal(token)
        return token

    async def release_stale_locks(self, expires=10):
        token = await self.client.set(
            self.check_release_locks_key, self.exists_val, get=True
        )
        if token:
            return False
        await self.client.expire(self.check_release_locks_key, expires)
        try:
            for token, locked_at in (
                await self.client.hgetall(self.grabbed_key)
            ).items():
                timed_out_at = float(locked_at) + self.stale_client_timeout
                if token in self._local_tokens and timed_out_at < float(await self.current_time):
                    await self.signal(token)
                    self._local_tokens.remove(token)
        finally:
            await self.client.delete(self.check_release_locks_key)

    async def _is_locked(self, token):
        return await self.client.hexists(self.grabbed_key, token)

    @property
    def num_tokens(self):
        return len(self._local_tokens)

    async def has_token(self) -> bool:
        """当前信号量拥有至少一个token时返回True"""
        for t in self._local_tokens:
            if await self._is_locked(t):
                return True
        return False

    async def locked(self) -> bool:
        """如果信号量不能被立刻获取返回True"""
        grabbed: int = await self.client.hlen(self.grabbed_key)  # type: ignore
        return True if grabbed == self.value else False

    async def release(self):
        for i in range(len(self._local_tokens) - 1, -1, -1):
            token = self._local_tokens[i]
            if await self._is_locked(token):
                self._local_tokens.pop(i)
                return await self.signal(token)
        return False

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
        return self._get_and_set_key("_release_locks_key", "RELEASE_LOCKS")

    def _get_and_set_key(self, key_name, namespace_suffix):
        if not hasattr(self, key_name):
            setattr(self, key_name, self.get_namespaced_key(namespace_suffix))
        return getattr(self, key_name)

    async def aclose(self):
        self._local_tokens.clear()
        await self.client.delete(
            self.check_exists_key, self.available_key, self.grabbed_key
        )
        await self.client.aclose()

    @property
    async def current_time(self) -> str:
        # if self.is_use_local_time:
        #     return time.time()
        return ".".join(map(str, await self.client.time()))

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.release()
        return True if exc_type is None else False
