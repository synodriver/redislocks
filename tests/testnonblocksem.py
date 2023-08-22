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
            2, Redis(host=os.getenv("REDIS"), max_connections=10), blocking=False
        )
        self.sem2 = Semaphore(
            2, Redis(host=os.getenv("REDIS"), max_connections=10), blocking=False
        )
        await self.sem1.reset()

    async def test_acquire(self):
        await self.sem1.acquire()
        self.assertTrue(await self.sem1.has_token())
        await self.sem1.release()
        self.assertFalse(await self.sem1.has_token())
        await self.sem1.reset()

    async def test_raise(self):
        await self.sem1.acquire()
        await self.sem1.acquire()
        with self.assertRaises(NotAvailable):
            await self.sem2.acquire()

    async def asyncTearDown(self) -> None:
        await self.sem1.reset()


if __name__ == "__main__":
    import unittest

    unittest.main()
