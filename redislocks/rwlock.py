"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import time
from datetime import timedelta
from typing import Literal, Optional, Union

from redis.asyncio import Redis


class _InternalLockCategory:
    write_lock = "write_lock"
    read_lock = "read_lock"
    preservation = "preservation"


class _RedisEventCategory:
    expired = b"expired"
    delR = b"del"


class RWLock:
    def __init__(
        self,
        client: Optional[Redis] = None,
        namespace: Optional[str] = None,
    ):
        self.client = client or Redis()
        self.is_use_local_time = False
        self.namespace = namespace or "RWLOCK"

        self._read_lock_time = '0'
        self._write_lock_time = '0'

        self._write_lock_name = self._get_key_string(
            _InternalLockCategory.write_lock
        )
        self._read_lock_name = self._get_key_string(_InternalLockCategory.read_lock)
        self._preservation_name = self._get_key_string(_InternalLockCategory.preservation)

        self._write_lock_keyspace_name = self._get_keyspace_name(
            _InternalLockCategory.write_lock
        )
        self._read_lock_keyspace_name = self._get_keyspace_name(
            _InternalLockCategory.read_lock
        )
        self._preservation_keyspace_name = self._get_keyspace_name(_InternalLockCategory.preservation)

    async def acquire(self, mode: Literal["r", "w"] = "r"):
        if mode == 'r':
            await self._acquire_read_lock()
        elif mode == 'w':
            await self._acquire_write_lock()

    async def release(self):
        if self._read_lock_time != '0':
            await self._release_read_lock()
        elif self._write_lock_time != '0':
            await self._release_write_lock()

    async def _acquire_read_lock(self):
        script = self.client.register_script(self._get_check_and_set_reading_lua())
        with self.client.pubsub() as pub_sub:
            pub_sub.subscribe(self._preservation_keyspace_name)
            while True:
                ret = await script(keys=[self._preservation_name], args=[self._read_lock_name])
                if ret == '0':
                    async for event in pub_sub.listen():
                        if event['data'] in [_RedisEventCategory.expired, _RedisEventCategory.delR]:
                            break
                else:
                    self._read_lock_time = ret
                    return

    async def _release_read_lock(self):
        script = self.client.register_script(self._get_check_and_delete_reading_lua())
        await script(keys=[self._read_lock_name], args=[self._read_lock_time])
        self._read_lock_time = '0'

    async def _acquire_write_lock(self):
        script = self.client.register_script(self._get_check_and_set_writing_lua())
        with self.client.pubsub() as pub_sub:
            pub_sub.subscribe(self._read_lock_keyspace_name)
            pub_sub.subscribe(self._write_lock_keyspace_name)
            while True:
                ret = await script(keys=[self._read_lock_name, self._write_lock_name, self._preservation_name])
                if ret != '0' and ret != '1':
                    self._write_lock_time = ret
                    return
                elif ret == '0':
                    async for event in pub_sub.listen():
                        if event['channel'] == self._read_lock_keyspace_name and event['data'] in [_RedisEventCategory.expired, _RedisEventCategory.delR]:
                            break
                elif ret == '1':
                    async for event in pub_sub.listen():
                        if event['channel'] == self._write_lock_keyspace_name and event['data'] in [_RedisEventCategory.expired, _RedisEventCategory.delR]:
                            break

    async def _release_write_lock(self):
        script = self.client.register_script(self._get_check_and_delete_writing_lua())
        await script(keys=[self._write_lock_name, self._preservation_name], args=[self._write_lock_time])
        self._write_lock_time = '0'

    def _get_key_string(self, category) -> str:
        return "{}:{}".format(self.namespace, category)

    def _get_keyspace_name(self, category) -> str:
        return "__keyspace@{}__:{}:{}".format(self._get_db(), self.namespace, category)

    async def _get_expire_time(self) -> int:
        return await self.client.time() + 3

    def _get_db(self) -> int:
        return self.client.get_connection_kwargs()["db"]

    def _get_check_and_set_reading_lua(self) -> str:
        if not hasattr(self, '_check_and_set_for_reading'):
            with open('check_and_set_for_reading.lua') as f:
                setattr(self, '_check_and_set_for_reading', f.read())
        return self._check_and_set_for_reading

    def _get_check_and_delete_reading_lua(self) -> str:
        if not hasattr(self, '_check_and_delete_for_reading'):
            with open('check_and_delete_for_reading.lua') as f:
                setattr(self, '_check_and_delete_for_reading', f.read())
        return self._check_and_delete_for_reading

    def _get_check_and_set_writing_lua(self) -> str:
        if not hasattr(self, '_check_and_set_for_writing'):
            with open('check_and_set_for_writing.lua') as f:
                setattr(self, '_check_and_set_for_writing', f.read())
        return self._check_and_set_for_writing

    def _get_check_and_delete_writing_lua(self) -> str:
        if not hasattr(self, '_check_and_delete_for_writing'):
            with open('check_and_delete_for_writing.lua') as f:
                setattr(self, '_check_and_delete_for_writing', f.read())
        return self._check_and_delete_for_writing

    @property
    async def current_time(self):
        if self.is_use_local_time:
            return time.time()
        return float(".".join(map(str, await self.client.time())))
