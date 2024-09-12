"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import asyncio
from enum import IntEnum
from typing import Dict, List, Literal, Optional

from redis.asyncio import Redis

from redislocks.exceptions import NotAvailable
from redislocks.scripts import (
    cancellockwrite_script,
    get_state_script,
    lockread_script,
    lockwrite_script,
    unlockread_script,
    unlockwrite_script,
)
from redislocks.utils import ensure_bytes, ensure_str


class LockState(IntEnum):
    empty = 0  # 空
    reading = 1  # 只有读锁
    writing = 2  # 有写锁
    waiting_write = 3  # 有读锁，没有写锁，但是有写锁在等待队列，因此此时不能继续获取读锁


# fixme 如果A获取锁，B获取的时候认为A超时给A释放了，此时AB同时持有锁。当前不允许超时机制
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
        """

        :param client: redis client
        :param namespace: lock的命名空间，相同的视为同一把锁，使用相同的redis key
        :param blocking: 阻塞与否 非阻塞模式下，如果不能立刻获取锁则会抛出NotAvailable
        """
        self.client = client or Redis()
        self.namespace = namespace
        self.blocking = blocking

        self.check_exists_key = self.get_namespaced_key("EXISTS")  # RWLOCK:EXISTS
        self.read_key = self.get_namespaced_key("READ")
        self.write_key = self.get_namespaced_key("WRITE")
        self.write_waiter_key = self.get_namespaced_key("WRITEWAITER")

        self._read_waiters = []  # type: List[asyncio.Future]
        self._write_waiters = {}  # type: Dict[str, asyncio.Future]

        self._lockread_script = self.client.register_script(
            lockread_script
        )  # lockread.lua
        self._unlockread_script = self.client.register_script(
            unlockread_script
        )  # unlockread.lua
        self._lockwrite_script = self.client.register_script(
            lockwrite_script
        )  # lockwrite.lua
        self._unlockwrite_script = self.client.register_script(
            unlockwrite_script
        )  # unlockwrite.lua
        self._cancellockwrite_script = self.client.register_script(
            cancellockwrite_script
        )  # cancellockwrite.lua
        self._get_state_script = self.client.register_script(get_state_script)
        self._local_readtokens = []  # type: List[str]
        self._local_writetoken = None  # type: Optional[str]
        self._listen_task = asyncio.create_task(self._listen_events())

    def __del__(self):
        if self._listen_task is not None:
            self._listen_task.cancel()
            # try:
            #     await self._listen_task
            # except asyncio.CancelledError:
            #     pass
            self._listen_task = None

    async def _exists_or_init(self) -> None:
        if await self.client.set(self.check_exists_key, self.exists_val, nx=True):
            await self.client.config_set(
                "notify-keyspace-events", "Ag$lshzxeKEtmdn"
            )  # todo 需要修改配置吗

    async def reset(self):
        """
        删除redis中的key，重置状态，释放持有的token
        :return:
        """
        self._read_waiters.clear()
        self._write_waiters.clear()
        self._local_readtokens.clear()
        self._local_writetoken = None
        await self.client.delete(self.read_key, self.write_key, self.write_waiter_key)

    async def aclose(self):
        self._read_waiters.clear()
        self._write_waiters.clear()
        self._local_readtokens.clear()
        self._local_writetoken = None
        self._listen_task.cancel()
        try:
            await self._listen_task
        except asyncio.CancelledError:
            pass
        self._listen_task = None
        await self.client.delete(
            self.check_exists_key, self.read_key, self.write_key, self.write_waiter_key
        )
        await self.client.aclose()

    async def release_all(self):
        for _ in range(len(self._local_readtokens)):
            await self.release("r")
        if self._local_writetoken is not None:
            await self.release("w")

    def _get_db(self) -> int:
        return self.client.get_connection_kwargs()["db"]

    async def acquire(self, mode: Literal["r", "w"] = "r") -> str:
        await self._exists_or_init()
        if mode == "r":
            if token := await self._lockread_script([self.namespace]):
                token = ensure_str(token)
                self._local_readtokens.append(token)
                return token
            if self.blocking:  # 不能立刻获取到读锁，阻塞模式(默认)，开始等self._read_waiters，
                waiter = asyncio.get_running_loop().create_future()
                self._read_waiters.append(waiter)
                try:
                    await waiter
                finally:
                    self._read_waiters.remove(waiter)
                return await self.acquire(
                    mode
                )  # 终于写锁完成了，再次acquire抢读锁 fixme 不可能抢到递归超出限制都没抢到锁吧？太晦气了
            else:
                raise NotAvailable
        elif mode == "w":
            if token := await self._lockwrite_script(
                [self.namespace]
            ):  # 可以立刻非阻塞获取写锁 str, bytes
                self._local_writetoken = ensure_str(token)  # 时间戳
                return token  # type: ignore
            if self.blocking:  # 不能立刻获取到写锁，阻塞模式(默认)，开始等self._write_waiters，
                token: str = await self.current_time  # type: ignore
                # 这下只能等了
                await self.client.rpush(self.write_waiter_key, token)  # type: ignore
                waiter = asyncio.get_running_loop().create_future()
                self._write_waiters[token] = waiter
                try:
                    await waiter  # 一旦取消，则redis中writewaiter里面还是有token，但是本地的write_waiter已经del了 ，因此需要删除等待写锁队列里面的token
                except asyncio.CancelledError:
                    # 如果在这期间正好写锁轮了一下，write_waiter_key又上位了，lrem不到了，那就糟糕了
                    # 写一个cancellockwrite.lua，KEYS = [namespace, 要取消的token]，先lrem，没删除到就是这期间写锁轮了一下，上位了
                    # 可惜太晚了，还是必须要删掉，就如同unlockwrite.lua做的那样释放了先，致敬传奇耐取消王
                    # shield不可取，其使得真正的task在后台执行，而外部caller就返回了，这里就是要让caller卡在这，状态不变回来不能返回，因此不能shield起来当缩头乌龟
                    err = None
                    while True:
                        try:
                            await self._cancellockwrite_script([self.namespace, token])
                            break
                        except asyncio.CancelledError as e:
                            err = e
                    if err is not None:
                        try:
                            raise err
                        finally:
                            err = None
                    # await self.client.lrem(
                    #     self.write_waiter_key, 1, token
                    # )  # type: ignore
                    raise
                finally:
                    del self._write_waiters[token]
                self._local_writetoken = token
                return token
            else:
                raise NotAvailable
        else:
            raise ValueError("mode must be 'r' or 'w'")

    async def release(self, mode: Literal["r", "w"] = "r"):
        if mode == "r":
            try:
                token = self._local_readtokens.pop()
            except IndexError:  # 空list？
                raise ValueError("can not release more than acquire")
            try:
                if not await self._unlockread_script(
                    [self.namespace, token]
                ):  # 什么都没srem出来，本地token有问题还是云端释放了？
                    raise ValueError(
                        "No lock is released. Is it released by a timeout checker?"
                    )
            except asyncio.CancelledError:
                self._local_readtokens.append(token)  # 别在这取消啊
                raise
        elif mode == "w":
            if self._local_writetoken is None:
                raise ValueError("can not release write lock without acquire it")
            if not await self._unlockwrite_script(
                [self.namespace]
            ):  # todo 如果加入超时机制的话 这里需要检查远程的write_key与self._local_writetoken是否对的上
                # 如果对不上就是那个已经被超时检查器给释放了 读token扼要检查 没在set里面也是被超时检查器给释放了
                raise ValueError(
                    "No lock is released. Is it released by a timeout checker?"
                )
            self._local_writetoken = None
        else:
            raise ValueError("mode must be 'r' or 'w'")

    async def has_token(self, mode: Literal["r", "w"] = "r") -> bool:
        """如果当前lock存在对应的token返回True"""
        if mode == "r":
            for token in self._local_readtokens:
                if await self.client.sismember(self.read_key, token):  # type: ignore
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

    async def get_state(self) -> int:
        return await self._get_state_script([self.namespace])

    async def locked(self, mode: Literal["r", "w"] = "r") -> bool:
        """如果锁不能立刻获取返回True"""
        current_state = await self._get_state_script([self.namespace])
        if mode == "r":
            if current_state in (2, 3):
                return True
            else:
                return False
        elif mode == "w":
            if current_state in (1, 2, 3):
                return True
            else:
                return False
        # if mode == "r":  # 读锁能不能立刻获取取决于写锁
        #     if await self.client.exists(self.write_key) or await self.client.llen(self.write_waiter_key) > 0:
        #         return False
        #     else:
        #         return True
        # elif mode == "w":
        #     if not await self.client.exists(self.write_key) and (await self.client.scard(self.read_key)) == 0:
        #         return True
        #     else:
        #         return False

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
        async with self.client.pubsub() as pubsub:
            await pubsub.subscribe(
                f"__keyspace@{self._get_db()}__:{self.write_key}",
            )
            async for event in pubsub.listen():
                # print(event)
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
                ):  # 被释放的老 读锁/写锁 唤醒了新写锁，对应token的写锁不用等了，如果那个token属于这个client有的话
                    token = ensure_str(
                        await self.client.get(self.write_key)
                    )  # 轮到哪个幸运儿上了
                    if token in self._write_waiters:
                        waiter = self._write_waiters[token]
                        waiter.set_result(None)
