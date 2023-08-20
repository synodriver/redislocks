"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import asyncio
import time
from typing import Literal, Optional, Union

from redis.asyncio import Redis

from redislocks.exceptions import NotAvailable


class RWLock:
    """
    Redis内存视图
    "RWLOCK:EXISTS" : "ok" 判断是否存在
    "RWLOCK:READ": [float, float] 已经被获取的读锁，里面是他们的申请时间戳
    "RWLOCK:WRITE": "1151.1919810" 已经被获取的写锁和他的申请时间戳
    "RWLOCK:WRITEWAITER": [float, float] 等待获取写锁的，里面是他们的申请时间戳

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
        self.is_use_local_time = False
        self.namespace = namespace
        self.blocking = blocking

        self.check_exists_key = self.get_namespaced_key("EXISTS")  # RWLOCK:EXISTS
        self.read_key = self.get_namespaced_key("READ")
        self.write_key = self.get_namespaced_key("WRITE")
        self.write_waiter_key = self.get_namespaced_key("WRITEWAITER")

        self._read_waiters = []  # type: List[asyncio.Future]

        self._lockread_script = self.client.register_script("""""")  # todo lockread.lua
        self._unlockread_script = self.client.register_script(
            """"""
        )  # todo unlockread.lua
        self._checkcanread_script = self.client.register_script(
            """"""
        )  # todo checkcanread.lua

        self._listen_task = asyncio.create_task(self._listen_events())

    async def _exists_or_init(self):
        # await self.client.config_set("notify-keyspace-events", "Ag$lshzxeKEtmdn") todo 需要修改配置吗
        old_key = await self.client.getset(self.check_exists_key, self.exists_val)
        if old_key:
            return False

    def _get_db(self) -> int:
        return self.client.get_connection_kwargs()["db"]

    async def acquire(self, mode: Literal["r", "w"] = "r") -> Union[str, bytes]:
        await self._exists_or_init()
        if mode == "r":
            if await self._lockread_script([self.namespace]) == 0:  # 加锁失败
                if self.blocking:  # 阻塞模式，开始等self._read_waiters
                    waiter = asyncio.get_running_loop().create_future()
                    self._read_waiters.append(waiter)
                    await waiter  # todo 添加asyncio.wait_for 就可以超时了
                    await self.acquire(mode)
                else:
                    raise NotAvailable
        elif mode == "w":
            pass  # todo 实现加写锁的逻辑
        else:
            raise ValueError("mode must be 'r' or 'w'")

    async def release(self, mode: Literal["r", "w"] = "r"):
        if mode == "r":
            if not await self._unlockread_script(
                [self.namespace]
            ):  # 什么都没lpop出来，一定是多释放了一次，直接报错
                raise ValueError("can not release more than acquire")

    @property
    async def current_time(self):
        if self.is_use_local_time:
            return time.time()
        return float(".".join(map(str, await self.client.time())))

    def get_namespaced_key(self, suffix):
        return "{0}:{1}".format(self.namespace, suffix)

    async def _listen_events(self):
        """
        监听redis中的key变动 从而知道什么时候可以获取锁
        :return:
        """

        # todo 现在只有加读锁的逻辑，因此只能监听写锁有关的几个key，未来为了加写锁还得监听读锁的几个key
        async def check_canread() -> bool:
            """
            判断是否可以加读锁 todo 这个函数抽空换成lua
            :return:
            """
            if (
                await self.client.llen(self.write_waiter_key)
            ) == 0 and not await self.client.exists(self.write_key):
                return True
            return False

        async with self.client.pubsub() as pubsub:
            await pubsub.subscribe(
                f"__keyspace@{self._get_db()}__:{self.write_key}",
                f"__keyspace@{self._get_db()}__:{self.write_waiter_key}",
            )
            async for event in pubsub.listen():
                if (
                    event["type"] == "message"
                    and event["channel"]
                    == f"__keyspace@{self._get_db()}__:{self.write_waiter_key}"
                    and event["data"] == "lpop"
                ):  # 写锁等待队列少了一个，是不是空了？
                    if await check_canread():
                        for waiter in self._read_waiters:
                            waiter.set_result(None)
