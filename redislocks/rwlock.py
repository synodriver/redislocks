"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import asyncio
from typing import Dict, List, Literal, Optional, Union

from redis.asyncio import Redis

from redislocks.exceptions import NotAvailable
from redislocks.scripts import (
    lockread_script,
    lockwrite_nowait_script,
    unlockread_script,
    unlockwrite_script,
)
from redislocks.utils import ensure_bytes, ensure_str


class RWLock:
    """
    Redis内存视图
    "RWLOCK:EXISTS" : "ok" 判断是否存在
    "RWLOCK:READ": Set[str] 已经被获取的读锁，里面是他们的申请时间戳, redis把float当str
    "RWLOCK:WRITE": "1151.1919810" 已经被获取的写锁和他的申请时间戳
    "RWLOCK:WRITEWAITER": List[str] 等待获取写锁的，里面是他们的申请时间戳

    写锁优先，如果存在写锁或者存在等待获取写锁的，读锁只能先行等待进入等待队列
    """

    exists_val = "ok"

    def __init__(
        self,
        client: Optional[Redis] = None,
        namespace: str = "RWLOCK",
        blocking: bool = True,
    ):
        self.client = client or Redis()
        self.namespace = namespace
        self.blocking = blocking

        self.check_exists_key = self.get_namespaced_key("EXISTS")  # RWLOCK:EXISTS
        self.read_key = self.get_namespaced_key("READ")
        self.write_key = self.get_namespaced_key("WRITE")
        self.write_waiter_key = self.get_namespaced_key("WRITEWAITER")

        self._read_waiters = []  # type: List[asyncio.Future]
        self._write_waiters = {}  # type: Dict[float, asyncio.Future]

        self._lockread_script = self.client.register_script(
            lockread_script
        )  # todo lockread.lua
        self._unlockread_script = self.client.register_script(
            unlockread_script
        )  # todo unlockread.lua
        # self._checkcanread_script = self.client.register_script(
        #     """"""
        # )  #  checkcanread.lua
        # self._checkcanwrite_script = self.client.register_script(
        #     """"""
        # )  #  checkcanwrite.lua
        self._lockwrite_nowait_script = self.client.register_script(
            lockwrite_nowait_script
        )  # todo lockwrite_nowait.lua
        self._unlockwrite_script = self.client.register_script(
            unlockwrite_script
        )  # todo unlockwrite.lua
        self._local_readtokens = []  # type: List[str]
        self._local_writetoken = None  # type: Optional[str]
        self._listen_task = asyncio.create_task(self._listen_events())

    def __del__(self):
        self._listen_task.cancel()
        # try:
        #     await self._listen_task
        # except asyncio.CancelledError:
        #     pass
        self._listen_task = None

    async def _exists_or_init(self):
        # await self.client.config_set("notify-keyspace-events", "Ag$lshzxeKEtmdn") todo 需要修改配置吗
        old_key = await self.client.getset(self.check_exists_key, self.exists_val)
        if old_key:
            return False

    def _get_db(self) -> int:
        return self.client.get_connection_kwargs()["db"]

    async def acquire(self, mode: Literal["r", "w"] = "r") -> Union[str]:
        await self._exists_or_init()
        if mode == "r":
            if (token := await self._lockread_script([self.namespace])) == 0:  # 加锁失败
                if self.blocking:  # 阻塞模式，开始等self._read_waiters
                    waiter = asyncio.get_running_loop().create_future()
                    self._read_waiters.append(waiter)
                    try:
                        await waiter  # todo 添加asyncio.wait_for 就可以超时了
                    finally:
                        self._read_waiters.remove(waiter)
                    await self.acquire(mode)
                else:
                    raise NotAvailable
            else:
                token = ensure_str(token)
                self._local_readtokens.append(token)
                return token  # type: ignore
        elif mode == "w":
            if token := await self._lockwrite_nowait_script(
                [self.namespace]
            ):  # 可以立刻非阻塞获取写锁 str, bytes
                self._local_writetoken = ensure_str(token)
                return token  # type: ignore
            if not self.blocking:
                raise NotAvailable
            else:
                token: str = await self.current_time
                await self.client.rpush(self.write_waiter_key, token)  # 这下只能等了
                waiter = asyncio.get_running_loop().create_future()
                self._write_waiters[token] = waiter
                try:
                    await waiter
                finally:
                    del self._write_waiters[token]
                self._local_writetoken = token
                return token  # type: ignore
        else:
            raise ValueError("mode must be 'r' or 'w'")

    async def release(self, mode: Literal["r", "w"] = "r"):
        if mode == "r":
            try:
                token = self._local_readtokens.pop()
            except IndexError:  # 空list？
                raise ValueError("can not release more than acquire")
            if not await self._unlockread_script(
                [self.namespace, token]
            ):  # 什么都没srem出来，本地token有问题还是云端释放了？
                raise ValueError("No lock is released. Is redis changed?")
        elif mode == "w":
            if self._local_writetoken is None:
                raise ValueError("can not release write lock without acquire it")
            await self._unlockwrite_script([self.namespace])
            self._local_writetoken = None
        else:
            raise ValueError("mode must be 'r' or 'w'")

    async def have_lock(self, mode: Literal["r", "w"] = "r") -> bool:
        if mode == "r":
            for token in self._local_readtokens:
                if await self.client.sismember(self.read_key, token):
                    return True
            else:
                return False
        elif mode == "w":
            if self._local_writetoken and self._local_writetoken == ensure_str(
                await self.client.get(self.write_key)
            ):
                return True
            else:
                return False

    @property
    async def current_time(self) -> str:
        # if self.is_use_local_time:
        #     return time.time()
        return ".".join(map(str, await self.client.time()))

    def get_namespaced_key(self, suffix):
        return "{0}:{1}".format(self.namespace, suffix)

    async def _listen_events(self):
        """
        监听redis中的key变动 从而知道什么时候可以获取锁
        :return:
        """

        # todo 现在只有加读锁的逻辑，因此只能监听写锁有关的几个key，未来为了加写锁还得监听读锁的几个key
        # async def check_canread() -> bool:
        #     """
        #     判断是否可以加读锁 todo 这个函数抽空换成lua
        #     :return:
        #     """
        #     if await self._checkcanread_script([self.namespace]):
        #         return True
        #     return False
        # if (
        #     await self.client.llen(self.write_waiter_key)
        # ) == 0 and not await self.client.exists(self.write_key):
        #     return True
        # return False

        async with self.client.pubsub() as pubsub:
            await pubsub.subscribe(
                f"__keyspace@{self._get_db()}__:{self.write_key}",
            )
            async for event in pubsub.listen():
                print(event)
                if (
                    ensure_str(event["type"]) == "message"
                    and ensure_str(event["channel"])
                    == f"__keyspace@{self._get_db()}__:{self.write_key}"
                    and ensure_str(event["data"]) == "del"
                ):  # 写锁被删除了，现在可以读了
                    for waiter in self._read_waiters:
                        waiter.set_result(None)
                if (
                    ensure_str(event["type"]) == "message"
                    and ensure_str(event["channel"])
                    == f"__keyspace@{self._get_db()}__:{self.write_key}"
                    and ensure_str(event["data"]) == "set"
                ):  # 被释放的老 读锁/写锁 唤醒了新写锁，对应token的写锁不用等了，如果这个client有的话
                    token = ensure_str(
                        await self.client.get(self.write_key)
                    )  # 轮到哪个幸运儿上了
                    if token in self._write_waiters:
                        waiter = self._write_waiters[token]
                        waiter.set_result(None)
