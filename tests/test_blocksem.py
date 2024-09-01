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
        self.sem1 = Semaphore(
            2, Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))
        )
        self.sem2 = Semaphore(
            2, Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))
        )
        await self.sem1.reset()

    async def test_wrongvalue(self):
        with self.assertRaises(ValueError):
            sem = Semaphore(
                -1, Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))
            )

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

    async def test_context(self):
        async with self.sem1:
            self.assertFalse(await self.sem1.locked())
        await self.sem1.reset()

    async def test_release_all(self):
        await self.sem1.acquire()
        await self.sem1.acquire()
        await self.sem1.release_all()
        self.assertEquals(len(self.sem1._local_tokens), 0)
        self.assertEquals(await self.sem1.available_count, 2)
        await self.sem1.reset()

    async def test_target(self):
        result = False

        def target1(token):
            nonlocal result
            result = True

        await self.sem1.acquire(target=target1)
        self.assertTrue(result)
        self.assertEquals(len(self.sem1._local_tokens), 0)
        self.assertEquals(await self.sem1.available_count, 2)
        result = False

        async def target2(token):
            nonlocal result
            result = True

        await self.sem1.acquire(target=target2)
        self.assertTrue(result)
        self.assertEquals(len(self.sem1._local_tokens), 0)
        self.assertEquals(await self.sem1.available_count, 2)
        await self.sem1.reset()

    async def test_release_stale_locks(self):
        sem = Semaphore(
            2,
            Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD")),
            stale_client_timeout=1,
        )
        self.assertFalse(await sem.release())
        await sem.acquire()
        self.assertEquals(sem.num_tokens, 1)
        self.assertEquals(len(sem._local_tokens), 1)
        self.assertEquals(await sem.available_count, 1)
        await asyncio.sleep(2)
        await sem.release_stale_locks()
        self.assertEquals(len(sem._local_tokens), 0)
        self.assertEquals(await sem.available_count, 2)
        await sem.reset()

    async def asyncTearDown(self) -> None:
        await self.sem1.reset()
        await self.sem2.reset()
        await self.sem1.aclose()
        await self.sem2.aclose()


if __name__ == "__main__":
    import unittest

    unittest.main()
