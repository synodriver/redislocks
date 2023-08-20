"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import time
from datetime import timedelta
from typing import Literal, Optional, Union

from redis.asyncio import Redis


class _InternalLockCategory:
    write_lock_list = "write_lock_list"
    read_lock_list = "read_lock_list"


class _RedisEventCategory:
    expired = b"expired"
    incrby = b"incrby"


class RWLock:
    def __init__(
        self,
        client: Optional[Redis] = None,
        namespace: str = "RWLOCK",
    ):
        self.client = client or Redis()
        self.is_use_local_time = False
        self.namespace = namespace

    # 目前只实现了写锁
    async def acquire(self, mode: Literal["r", "w"] = "r") -> Union[str, bytes]:
        write_lock_list_name = self._get_key_string(
            _InternalLockCategory.write_lock_list
        )
        read_lock_list_name = self._get_key_string(_InternalLockCategory.read_lock_list)

        write_lock_keyspace_name = self._get_keyspace_name(
            _InternalLockCategory.write_lock_list
        )
        read_lock_keyspace_name = self._get_keyspace_name(
            _InternalLockCategory.read_lock_list
        )

        while True:
            print("start")
            # 先检查是否有读锁存在，如果有读锁，则预先抢占写锁并不设置超时
            # 否则直接开始获取写锁
            catch = 0
            async with self.client.pubsub() as pub_sub:
                # 先 sub 对应的读锁事件，检查是否存在读锁被获取的情况
                await pub_sub.subscribe(read_lock_keyspace_name)
                reading_count = await self.client.get(read_lock_list_name) or 0
                if reading_count > 0:
                    # 若不存在则开始抢占写锁，拒绝新读锁的进入（逻辑上）
                    catch = await self.client.set(write_lock_list_name, 1, nx=True)

                    # 等待读锁全部释放
                    async for event in pub_sub.listen():
                        print(event)
                        if (
                            event["data"] == _RedisEventCategory.incrby
                        ):  # todo ensure_bytes
                            reading_count = (
                                await self.client.get(
                                    _InternalLockCategory.write_lock_list  # todo 为什么这里没有_get_key_string
                                )
                                or 0
                            )
                            if not reading_count:
                                break
                        elif event["data"] == _RedisEventCategory.expired:
                            ...
                else:
                    ...

            # 获取写锁，如果上面 catch 到了则这里返回 0
            is_existing = await self.client.set(
                write_lock_list_name, 1, px=timedelta(milliseconds=500000), nx=True
            )
            if not catch and not is_existing:
                pub_sub = self.client.pubsub()
                await pub_sub.subscribe(
                    self._get_keyspace_name(_InternalLockCategory.write_lock_list)
                )

                # 等待读锁释放（过期释放）
                # TODO 补充主动删除事件
                async for event in pub_sub.listen():
                    print(event)
                    if event["data"] == _RedisEventCategory.expired:
                        print("enter event")
                        break
            elif catch:
                # 重置一下过期时间
                await self.client.set(
                    write_lock_list_name, 1, px=timedelta(milliseconds=500000)
                )
                return "1"
            else:
                return "1"

    async def release(self, token: Union[str, bytes]):
        ...

    def _get_key_string(self, category) -> str:
        return "{}:{}".format(self.namespace, category)

    def _get_keyspace_name(self, category) -> str:
        return "__keyspace@{}__:{}:{}".format(self._get_db(), self.namespace, category)

    async def _get_expire_time(self) -> int:
        return await self.client.time() + 3

    def _get_db(self) -> int:
        return self.client.get_connection_kwargs()["db"]

    @property
    async def current_time(self):
        if self.is_use_local_time:
            return time.time()
        return float(".".join(map(str, await self.client.time())))
