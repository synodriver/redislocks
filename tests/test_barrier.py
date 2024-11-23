# -*- coding: utf-8 -*-
"""
Copyright (c) 2008-2024 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import asyncio
import os
import unittest
from unittest import IsolatedAsyncioTestCase

from dotenv import load_dotenv
from redis.asyncio import Redis

from redislocks.barrier import Barrier, BrokenBarrierError

load_dotenv("./.env")


class TestBarrier(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client1 = Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))
        self.client2 = Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))

    async def test_wrong_parties(self):
        with self.assertRaises(ValueError):
            b1 = Barrier(-1, self.client1)
        with self.assertRaises(ValueError):
            b1 = Barrier(0, self.client1)

    async def test_wait(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client1)
        b2 = Barrier(2, self.client2)
        self.assertEqual(b1.parties, 2)
        await asyncio.sleep(0.5)
        state = 2

        async def task():
            nonlocal state
            await b1.wait()
            state -= 1

        t = asyncio.create_task(task())
        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 1)
        self.assertEqual(await b2.n_waiting, 1)
        self.assertEqual(state, 2)

        async def task2():
            nonlocal state
            await b2.wait()
            state -= 1

        t2 = asyncio.create_task(task2())
        await t2
        self.assertEqual(await b1.n_waiting, 0)
        self.assertEqual(await b2.n_waiting, 0)
        await asyncio.sleep(0.5)
        self.assertEqual(state, 0)

    async def test_cancel(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client1)
        state = 1

        async def task():
            nonlocal state
            await b1.wait()
            state -= 1

        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 0)
        try:
            await asyncio.wait_for(task(), 1)
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 0)
        self.assertEqual(state, 1)

    async def test_broken(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client1)
        state = 1

        async def task():
            nonlocal state
            with self.assertRaises(BrokenBarrierError):
                await b1.wait()
            state -= 1

        t = asyncio.create_task(task())
        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 1)
        await b1.abort()
        await t
        self.assertEqual(await b1.n_waiting, 0)
        self.assertEqual(state, 0)
        self.assertTrue(await b1.broken)

    async def test_reset(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client1)
        state = 1

        async def task():
            nonlocal state
            with self.assertRaises(BrokenBarrierError):
                await b1.wait()
            state -= 1

        t = asyncio.create_task(task())
        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 1)
        await b1.reset()
        await t
        self.assertEqual(await b1.n_waiting, 0)
        self.assertEqual(state, 0)
        self.assertFalse(await b1.broken)

    async def test_context(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client1)
        state = 1

        async def task():
            nonlocal state
            with self.assertRaises(BrokenBarrierError):
                async with b1:
                    state -= 1

        t = asyncio.create_task(task())
        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 1)
        await b1.reset()
        await t
        self.assertEqual(await b1.n_waiting, 0)
        self.assertEqual(state, 1)
        self.assertFalse(await b1.broken)

    async def test_aclose(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client2)
        state = 1

        async def task():
            nonlocal state
            with self.assertRaises(BrokenBarrierError):
                async with b1:
                    state -= 1

        t = asyncio.create_task(task())
        await asyncio.sleep(0.5)
        await b1.aclose()


class TestBarrierResp3(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client1 = Redis(
            host=os.getenv("REDIS"), password=os.getenv("PASSWORD"), protocol=3
        )
        self.client2 = Redis(
            host=os.getenv("REDIS"), password=os.getenv("PASSWORD"), protocol=3
        )

    async def test_wrong_parties(self):
        with self.assertRaises(ValueError):
            b1 = Barrier(-1, self.client1)
        with self.assertRaises(ValueError):
            b1 = Barrier(0, self.client1)

    async def test_wait(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client1)
        b2 = Barrier(2, self.client2)
        self.assertEqual(b1.parties, 2)
        await asyncio.sleep(0.5)
        state = 2

        async def task():
            nonlocal state
            await b1.wait()
            state -= 1

        t = asyncio.create_task(task())
        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 1)
        self.assertEqual(await b2.n_waiting, 1)
        self.assertEqual(state, 2)

        async def task2():
            nonlocal state
            await b2.wait()
            state -= 1

        t2 = asyncio.create_task(task2())
        await t2
        self.assertEqual(await b1.n_waiting, 0)
        self.assertEqual(await b2.n_waiting, 0)
        await asyncio.sleep(0.5)
        self.assertEqual(state, 0)

    async def test_cancel(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client1)
        state = 1

        async def task():
            nonlocal state
            await b1.wait()
            state -= 1

        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 0)
        try:
            await asyncio.wait_for(task(), 1)
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 0)
        self.assertEqual(state, 1)

    async def test_broken(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client1)
        state = 1

        async def task():
            nonlocal state
            with self.assertRaises(BrokenBarrierError):
                await b1.wait()
            state -= 1

        t = asyncio.create_task(task())
        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 1)
        await b1.abort()
        await t
        self.assertEqual(await b1.n_waiting, 0)
        self.assertEqual(state, 0)
        self.assertTrue(await b1.broken)

    async def test_reset(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client1)
        state = 1

        async def task():
            nonlocal state
            with self.assertRaises(BrokenBarrierError):
                await b1.wait()
            state -= 1

        t = asyncio.create_task(task())
        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 1)
        await b1.reset()
        await t
        self.assertEqual(await b1.n_waiting, 0)
        self.assertEqual(state, 0)
        self.assertFalse(await b1.broken)

    async def test_context(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client1)
        state = 1

        async def task():
            nonlocal state
            with self.assertRaises(BrokenBarrierError):
                async with b1:
                    state -= 1

        t = asyncio.create_task(task())
        await asyncio.sleep(0.5)
        self.assertEqual(await b1.n_waiting, 1)
        await b1.reset()
        await t
        self.assertEqual(await b1.n_waiting, 0)
        self.assertEqual(state, 1)
        self.assertFalse(await b1.broken)

    async def test_aclose(self):
        await self.client1.delete("BARRIER:STATE")
        b1 = Barrier(2, self.client2)
        state = 1

        async def task():
            nonlocal state
            with self.assertRaises(BrokenBarrierError):
                async with b1:
                    state -= 1

        t = asyncio.create_task(task())
        await asyncio.sleep(0.5)
        await b1.aclose()


if __name__ == "__main__":
    unittest.main()
