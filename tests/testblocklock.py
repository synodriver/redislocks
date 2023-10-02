"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import asyncio
import os
from unittest import IsolatedAsyncioTestCase

from dotenv import load_dotenv
from redis.asyncio import Redis

from redislocks import RWLock

load_dotenv("./.env")


class TestLock(IsolatedAsyncioTestCase):
    async def delkeys(self):
        await self.client.delete("RWLOCK:READ", "RWLOCK:WRITE", "RWLOCK:WRITEWAITER")

    async def asyncSetUp(self) -> None:
        self.client = Redis(host=os.getenv("REDIS"), max_connections=10)
        self.lock1 = RWLock(self.client)
        self.lock2 = RWLock(self.client)
        await self.client.config_set("notify-keyspace-events", "Ag$lshzxeKEtmdn")
        await self.client.delete("RWLOCK:READ", "RWLOCK:WRITE", "RWLOCK:WRITEWAITER")

    async def test_havelock(self):
        await self.lock1.acquire("r")
        self.assertTrue(await self.lock1.has_token("r"))
        self.assertTrue(await self.lock2.locked("w"))
        self.assertFalse(await self.lock2.locked("r"))
        self.assertNotEquals(await self.lock1.acquire("r"), None)
        self.assertTrue(await self.lock1.has_token("r"))
        await self.lock1.release("r")
        await self.lock1.release("r")
        self.assertFalse(await self.lock1.has_token("r"))
        self.assertFalse(await self.lock2.locked("r"))
        self.assertFalse(await self.lock2.locked("w"))
        print(await self.client.keys("*"))
        self.assertFalse(await self.lock2.has_token("r"))
        await self.lock1.acquire("w")
        self.assertTrue(await self.lock1.has_token("w"))
        await self.lock1.release("w")
        await self.delkeys()

    async def test_read_read(self):
        await self.lock1.acquire("r")
        await asyncio.wait_for(self.lock2.acquire("r"), 1)
        await self.lock1.release("r")
        await self.lock2.release("r")
        print(await self.client.keys("*"))
        await self.delkeys()

    async def test_write_write(self):
        await self.lock1.acquire("w")
        self.assertTrue(await self.lock2.locked("w"))
        self.assertTrue(await self.lock2.locked("r"))
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(
                self.lock2.acquire("w"), 1
            )  # fixme: WRITEWAITER列表中的token可能在task取消后 并没有消失，导致凭空多出来一个写锁等待请求，与本地localtoken对不上
        await self.lock1.release("w")
        self.assertFalse(await self.lock2.locked("w"))
        self.assertFalse(await self.lock2.locked("r"))
        print(await self.client.keys("*"))
        await self.delkeys()

    async def test_write_read(self):
        await self.lock1.acquire("w")
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.lock2.acquire("r"), 1)
        await self.lock1.release("w")
        print(await self.client.keys("*"))
        await self.delkeys()

    async def test_read_write(self):
        await self.lock1.acquire("r")
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.lock2.acquire("w"), 1)
        await self.lock1.release("r")
        self.assertFalse(await self.lock1.locked("w"))  # fixme 同上
        print(await self.client.keys("*"))
        await self.delkeys()

    async def test_async_wakeup_read(self):
        """
        唤醒等待的读者
        :return:
        """

        async def acquire_task():
            await self.lock1.acquire("w")
            await asyncio.sleep(2)
            await self.lock1.release("w")

        asyncio.create_task(acquire_task())
        await asyncio.sleep(1)
        t1 = asyncio.get_running_loop().time()
        await self.lock2.acquire("r")
        t2 = asyncio.get_running_loop().time()
        # self.assertTrue(abs(t2 - t1 - 2) < 0.01)
        print(abs(t2 - t1 - 1) < 0.1)
        await self.lock2.release("r")
        print(await self.client.keys("*"))
        await self.delkeys()

    async def test_async_wakeup_write(self):
        """
        唤醒等待的读者
        :return:
        """

        async def acquire_task():
            await self.lock1.acquire("r")
            await asyncio.sleep(2)
            await self.lock1.release("r")

        asyncio.create_task(acquire_task())
        await asyncio.sleep(1)
        t1 = asyncio.get_running_loop().time()
        await self.lock2.acquire("w")
        t2 = asyncio.get_running_loop().time()
        # self.assertTrue(abs(t2 - t1 - 2) < 0.01)
        print(abs(t2 - t1 - 1) < 0.1)
        await self.lock2.release("w")
        print(await self.client.keys("*"))
        await self.delkeys()

    async def test_write_first(self):
        async def acquire_task():
            await self.lock1.acquire("r")
            await self.lock1.acquire("w")

        asyncio.create_task(acquire_task())
        await asyncio.sleep(1)
        self.assertTrue(not await self.client.get("RWLOCK:WRITE"))  # 写锁在等待
        self.assertTrue(await self.lock1.locked("r"))
        self.assertTrue(await self.lock1.locked("w"))
        print(await self.client.keys("*"))
        print(await self.client.smembers("RWLOCK:READ"))
        print(await self.client.lrange("RWLOCK:WRITEWAITER", 0, 5))
        # await self.lock2.acquire("r")
        with self.assertRaises(asyncio.TimeoutError):  # 此时也不能加读锁了
            await asyncio.wait_for(self.lock2.acquire("r"), 1)
        await self.delkeys()

    async def test_state(self):
        self.assertEquals(await self.lock2.get_state(), 0)
        await self.lock1.acquire("w")
        self.assertEquals(await self.lock2.get_state(), 2)
        await self.lock1.release("w")
        self.assertEquals(await self.lock2.get_state(), 0)
        await self.lock1.acquire("r")
        self.assertEquals(await self.lock2.get_state(), 1)

        async def acquire_task():
            await self.lock1.acquire("w")

        asyncio.create_task(acquire_task())
        await asyncio.sleep(0.5)
        self.assertEquals(await self.lock2.get_state(), 3)

    async def asyncTearDown(self) -> None:
        await self.client.delete("RWLOCK:READ", "RWLOCK:WRITE", "RWLOCK:WRITEWAITER")


if __name__ == "__main__":
    import unittest

    unittest.main()
