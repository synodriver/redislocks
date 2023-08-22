"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import asyncio
import os
from unittest import IsolatedAsyncioTestCase

from dotenv import load_dotenv
from redis.asyncio import Redis

from redislocks import NotAvailable, Semaphore

load_dotenv("./.env")


class TestSem(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sem1 = Semaphore(2, Redis(host=os.getenv("REDIS"), max_connections=10))
        self.sem2 = Semaphore(2, Redis(host=os.getenv("REDIS"), max_connections=10))
        await self.sem1.reset()

    async def test_acquire(self):
        await self.sem1.acquire()
        self.assertTrue(await self.sem1.has_token())
        await self.sem1.release()
        self.assertFalse(await self.sem1.has_token())

        await self.sem1.acquire()
        await self.sem1.acquire()
        self.assertTrue(await self.sem2.locked())
        await self.sem1.reset()

    async def test_block_other(self):
        await self.sem1.acquire()
        await self.sem1.acquire()
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.sem2.acquire(), 2)

    async def test_wakeup_other(self):
        async def acquire_task():
            await self.sem1.acquire()
            await self.sem1.acquire()
            await asyncio.sleep(2)
            await self.sem1.release()

        asyncio.create_task(acquire_task())
        await asyncio.sleep(1)
        t1 = asyncio.get_running_loop().time()
        await self.sem2.acquire()
        t2 = asyncio.get_running_loop().time()
        self.assertTrue(abs(t2 - t1 - 1), 0.1)
        print(abs(t2 - t1 - 1))

        await self.sem1.reset()

    async def asyncTearDown(self) -> None:
        await self.sem1.reset()


if __name__ == "__main__":
    import unittest

    unittest.main()
