# -*- coding: utf-8 -*-
import asyncio
import os
from unittest import IsolatedAsyncioTestCase

from dotenv import load_dotenv
from redis.asyncio import Redis

from redislocks.event import Event

load_dotenv("./.env")


class TestEvent(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client1 = Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))
        self.client2 = Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))
        self.event1 = Event(self.client1)
        self.event2 = Event(self.client2)

    async def test_wait(self):
        await self.event1.reset()
        ret = 1

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = asyncio.create_task(task1())
        await asyncio.sleep(0.1)
        self.assertEqual(ret, 1)
        await self.event2.set()
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 0)
        self.assertTrue(await self.event1.is_set())
        await self.event2.reset()

        self.assertFalse(await self.client1.exists("EVENT:SET"))
        self.assertFalse(await self.client1.exists("EVENT:WAITER"))
        self.assertFalse(await self.client1.exists("EVENT:WAITERPOP"))

    async def test_wait2(self):
        await self.event1.reset()
        ret = 2

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = [asyncio.create_task(task1()) for _ in range(2)]
        await asyncio.sleep(0.1)
        self.assertEqual(ret, 2)
        await self.event2.set()
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 0)
        self.assertTrue(await self.event1.is_set())
        await self.event2.reset()

        self.assertFalse(await self.client1.exists("EVENT:SET"))
        self.assertFalse(await self.client1.exists("EVENT:WAITER"))
        self.assertFalse(await self.client1.exists("EVENT:WAITERPOP"))

    async def test_cancel(self):
        await self.event1.reset()
        ret = 1

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = asyncio.create_task(task1())
        await asyncio.sleep(0.1)
        self.assertEqual(ret, 1)
        t.cancel()
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 1)
        self.assertFalse(await self.event1.is_set())
        self.assertEqual(await self.client1.llen("EVENT:WAITER"), 0)
        self.assertEqual(await self.client1.llen("EVENT:WAITERPOP"), 0)

    async def test_cancel2(self):
        await self.event1.reset()
        ret = 1

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = asyncio.create_task(task1())
        await asyncio.sleep(0.1)
        self.assertEqual(ret, 1)
        t.cancel()
        await asyncio.sleep(0)
        t.cancel()
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 1)
        self.assertFalse(await self.event1.is_set())
        self.assertEqual(await self.client1.llen("EVENT:WAITER"), 0)
        self.assertEqual(await self.client1.llen("EVENT:WAITERPOP"), 0)

    async def test_direct_return(self):
        await self.event1.reset()
        await self.event2.set()
        ret = 1

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = asyncio.create_task(task1())
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 0)

    async def test_clear(self):
        await self.event1.reset()
        await self.event2.set()
        self.assertTrue(await self.event1.is_set())
        await self.event1.clear()

        ret = 1

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = asyncio.create_task(task1())
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 1)


class TestEventResp3(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client1 = Redis(
            host=os.getenv("REDIS"), password=os.getenv("PASSWORD"), protocol=3
        )
        self.client2 = Redis(
            host=os.getenv("REDIS"), password=os.getenv("PASSWORD"), protocol=3
        )
        self.event1 = Event(self.client1)
        self.event2 = Event(self.client2)

    async def test_wait(self):
        await self.event1.reset()
        ret = 1

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = asyncio.create_task(task1())
        await asyncio.sleep(0.1)
        self.assertEqual(ret, 1)
        await self.event2.set()
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 0)
        self.assertTrue(await self.event1.is_set())
        await self.event2.reset()

        self.assertFalse(await self.client1.exists("EVENT:SET"))
        self.assertFalse(await self.client1.exists("EVENT:WAITER"))
        self.assertFalse(await self.client1.exists("EVENT:WAITERPOP"))

    async def test_wait2(self):
        await self.event1.reset()
        ret = 2

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = [asyncio.create_task(task1()) for _ in range(2)]
        await asyncio.sleep(0.1)
        self.assertEqual(ret, 2)
        await self.event2.set()
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 0)
        self.assertTrue(await self.event1.is_set())
        await self.event2.reset()

        self.assertFalse(await self.client1.exists("EVENT:SET"))
        self.assertFalse(await self.client1.exists("EVENT:WAITER"))
        self.assertFalse(await self.client1.exists("EVENT:WAITERPOP"))

    async def test_cancel(self):
        await self.event1.reset()
        ret = 1

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = asyncio.create_task(task1())
        await asyncio.sleep(0.1)
        self.assertEqual(ret, 1)
        t.cancel()
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 1)
        self.assertFalse(await self.event1.is_set())
        self.assertEqual(await self.client1.llen("EVENT:WAITER"), 0)
        self.assertEqual(await self.client1.llen("EVENT:WAITERPOP"), 0)

    async def test_cancel2(self):
        await self.event1.reset()
        ret = 1

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = asyncio.create_task(task1())
        await asyncio.sleep(0.1)
        self.assertEqual(ret, 1)
        t.cancel()
        await asyncio.sleep(0)
        t.cancel()
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 1)
        self.assertFalse(await self.event1.is_set())
        self.assertEqual(await self.client1.llen("EVENT:WAITER"), 0)
        self.assertEqual(await self.client1.llen("EVENT:WAITERPOP"), 0)

    async def test_direct_return(self):
        await self.event1.reset()
        await self.event2.set()
        ret = 1

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = asyncio.create_task(task1())
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 0)

    async def test_clear(self):
        await self.event1.reset()
        await self.event2.set()
        self.assertTrue(await self.event1.is_set())
        await self.event1.clear()

        ret = 1

        async def task1():
            await self.event1.wait()
            nonlocal ret
            ret -= 1

        t = asyncio.create_task(task1())
        await asyncio.sleep(0.5)
        self.assertEqual(ret, 1)
