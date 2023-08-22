"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import asyncio
import os
from unittest import IsolatedAsyncioTestCase

from dotenv import load_dotenv
from redis.asyncio import Redis

from redislocks import NotAvailable, RWLock

load_dotenv("./.env")


class TestLock(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = Redis(host=os.getenv("REDIS"), max_connections=10)
        self.lock1 = RWLock(self.client, blocking=False)
        self.lock2 = RWLock(self.client, blocking=False)
        await self.client.config_set("notify-keyspace-events", "Ag$lshzxeKEtmdn")
        await self.client.delete("RWLOCK:READ", "RWLOCK:WRITE", "RWLOCK:WRITEWAITER")

    async def test_havelock(self):
        await self.lock1.acquire("r")
        self.assertTrue(await self.lock1.has_token("r"))
        await self.lock1.acquire("r")
        self.assertTrue(await self.lock1.has_token("r"))
        self.assertTrue(await self.lock2.locked("w"))
        await self.lock1.release("r")
        await self.lock1.release("r")
        self.assertFalse(await self.lock1.has_token("r"))
        print(await self.client.keys("*"))
        self.assertFalse(await self.lock2.has_token("r"))

    async def test_read_read(self):
        await self.lock1.acquire("r")
        await asyncio.wait_for(self.lock2.acquire("r"), 1)
        await self.lock1.release("r")
        await self.lock2.release("r")
        print(await self.client.keys("*"))

    async def test_write_write(self):
        await self.lock1.acquire("w")
        with self.assertRaises(NotAvailable):
            await self.lock2.acquire("w")
        await self.lock1.release("w")
        print(await self.client.keys("*"))

    async def test_write_read(self):
        await self.lock1.acquire("w")
        with self.assertRaises(NotAvailable):
            await self.lock2.acquire("r")
        await self.lock1.release("w")
        print(await self.client.keys("*"))

    async def test_read_write(self):
        await self.lock1.acquire("r")
        with self.assertRaises(NotAvailable):
            await self.lock2.acquire("w")
        await self.lock1.release("r")
        print(await self.client.keys("*"))

    async def asyncTearDown(self) -> None:
        await self.client.delete("RWLOCK:READ", "RWLOCK:WRITE", "RWLOCK:WRITEWAITER")


if __name__ == "__main__":
    import unittest

    unittest.main()
