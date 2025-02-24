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

from redislocks.queue import BroadcastQueue, GroupStream, Queue, Stream
from redislocks.utils import ensure_str

load_dotenv("./.env")


class TestQueue(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client1 = Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))
        self.client2 = Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))

    async def test_queue_put_get(self):
        await self.client1.delete("QUEUE")
        q1 = Queue(self.client1)
        q2 = Queue(self.client1)
        self.assertTrue(await q1.empty())
        self.assertTrue(await q2.empty())
        self.assertEqual(await q1.qsize(), 0)
        self.assertEqual(await q2.qsize(), 0)
        await q1.put("value1")
        self.assertFalse(await q1.empty())
        self.assertFalse(await q2.empty())
        self.assertEqual(await q1.qsize(), 1)
        self.assertEqual(await q2.qsize(), 1)
        self.assertEqual(ensure_str(await q2.get()), "value1")
        self.assertTrue(await q1.empty())
        self.assertTrue(await q2.empty())
        self.assertEqual(await q1.qsize(), 0)
        self.assertEqual(await q2.qsize(), 0)
        await self.client1.delete("QUEUE")

    async def test_queue_timeout(self):
        await self.client1.delete("QUEUE")
        q1 = Queue(self.client1)
        q2 = Queue(self.client1)
        self.assertTrue(await q1.empty())
        self.assertTrue(await q2.empty())
        self.assertEqual(await q1.qsize(), 0)
        self.assertEqual(await q2.qsize(), 0)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(q2.get(), 1)

    async def test_bqueue_put_get(self):
        await self.client1.delete("BROADCASTQUEUE")
        b1 = BroadcastQueue(self.client1)
        b2 = BroadcastQueue(self.client2)
        await asyncio.sleep(0.5)
        self.assertTrue(await b1.empty())
        self.assertTrue(await b2.empty())
        self.assertEqual(await b1.qsize(), 0)
        self.assertEqual(await b2.qsize(), 0)
        await b1.put("value2")
        await asyncio.sleep(0.5)
        self.assertFalse(await b1.empty())
        self.assertFalse(await b2.empty())
        self.assertEqual(await b1.qsize(), 1)
        self.assertEqual(await b2.qsize(), 1)

        self.assertEqual(ensure_str(await b1.get()), "value2")
        self.assertEqual(ensure_str(await b2.get()), "value2")

        self.assertTrue(await b1.empty())
        self.assertTrue(await b2.empty())
        self.assertEqual(await b1.qsize(), 0)
        self.assertEqual(await b2.qsize(), 0)

    async def test_bqueue_timeout(self):
        await self.client1.delete("BROADCASTQUEUE")
        b1 = BroadcastQueue(self.client1)
        b2 = BroadcastQueue(self.client2)
        await asyncio.sleep(0.5)
        self.assertTrue(await b1.empty())
        self.assertTrue(await b2.empty())
        self.assertEqual(await b1.qsize(), 0)
        self.assertEqual(await b2.qsize(), 0)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(b2.get(), 1)

    async def test_stream_put_get(self):
        await self.client1.delete("STREAM")
        s1 = Stream(self.client1)
        s2 = Stream(self.client2)
        await asyncio.sleep(0.5)
        with self.assertRaises(TypeError):
            await s1.put("value1")
        self.assertEqual(await s1.qsize(), 0)
        self.assertEqual(await s2.qsize(), 0)
        self.assertTrue(await s1.empty())
        self.assertTrue(await s2.empty())
        await s1.put({"k": "value1"})
        await asyncio.sleep(0.5)
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        data = await s2.get()
        self.assertEqual(
            ensure_str(data.get(b"k", None) or data.get("k", None)), "value1"
        )
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        self.assertEqual(await s2.trim(), 0)
        await s1.put({"k": "value2"})
        await asyncio.sleep(0.5)
        self.assertEqual(await s1.qsize(), 2)
        self.assertEqual(await s2.qsize(), 2)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        data = await s2.get()
        self.assertEqual(
            ensure_str(data.get(b"k", None) or data.get("k", None)), "value2"
        )
        self.assertEqual(await s2.trim(), 1)
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())

    async def test_stream_callback(self):
        await self.client1.delete("STREAM")
        s1 = Stream(self.client1)
        lastid = None

        async def cb(id_):
            nonlocal lastid
            lastid = id_

        s2 = Stream(self.client2, on_cursor_change=cb)
        await asyncio.sleep(0.5)
        with self.assertRaises(TypeError):
            await s1.put("value1")
        self.assertEqual(await s1.qsize(), 0)
        self.assertEqual(await s2.qsize(), 0)
        self.assertTrue(await s1.empty())
        self.assertTrue(await s2.empty())
        await s1.put({"k": "value1"})
        await asyncio.sleep(0.5)
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        data = await s2.get()
        self.assertEqual(
            ensure_str(data.get(b"k", None) or data.get("k", None)), "value1"
        )
        self.assertTrue(lastid is not None)
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        self.assertEqual(await s2.trim(), 0)
        await s1.put({"k": "value2"})
        await asyncio.sleep(0.5)
        self.assertEqual(await s1.qsize(), 2)
        self.assertEqual(await s2.qsize(), 2)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        data = await s2.get()
        self.assertEqual(
            ensure_str(data.get(b"k", None) or data.get("k", None)), "value2"
        )
        self.assertEqual(await s2.trim(), 1)
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())

    async def test_stream_timeout(self):
        await self.client1.delete("STREAM")
        s1 = Stream(self.client1)
        s2 = Stream(self.client2)
        await asyncio.sleep(0.5)
        self.assertEqual(await s1.qsize(), 0)
        self.assertEqual(await s2.qsize(), 0)
        self.assertTrue(await s1.empty())
        self.assertTrue(await s2.empty())
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(s2.get(), 1)

    async def test_group_stream(self):
        await self.client1.delete("STREAM", "STREAM:CONSUMER_EXISTS")
        s1 = GroupStream(self.client1, consumer="consumer1")
        s2 = GroupStream(self.client2, consumer="consumer2")
        await s1.put({"k": "value1"})
        await s1.put({"k": "value2"})
        data = await s2.get()
        self.assertEqual(
            ensure_str(data.get("k", None) or data.get(b"k", None)), "value1"
        )
        pending = await s2.check_pending()
        self.assertEqual(
            ensure_str(pending.get("k", None) or pending.get(b"k", None)), "value1"
        )

        data = await s1.get()
        self.assertEqual(
            ensure_str(data.get("k", None) or data.get(b"k", None)), "value2"
        )
        pending = await s1.check_pending()
        self.assertEqual(
            ensure_str(pending.get("k", None) or pending.get(b"k", None)), "value2"
        )

        await s2.ack(s2.last_id)
        pending = await s2.check_pending()
        self.assertIsNone(pending)

        await s1.ack(s1.last_id)
        pending = await s1.check_pending()
        self.assertIsNone(pending)


class TestQueueResp3(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client1 = Redis(
            host=os.getenv("REDIS"), password=os.getenv("PASSWORD"), protocol=3
        )
        self.client2 = Redis(
            host=os.getenv("REDIS"), password=os.getenv("PASSWORD"), protocol=3
        )

    async def test_queue_put_get(self):
        await self.client1.delete("QUEUE")
        q1 = Queue(self.client1)
        q2 = Queue(self.client1)
        self.assertTrue(await q1.empty())
        self.assertTrue(await q2.empty())
        self.assertEqual(await q1.qsize(), 0)
        self.assertEqual(await q2.qsize(), 0)
        await q1.put("value1")
        self.assertFalse(await q1.empty())
        self.assertFalse(await q2.empty())
        self.assertEqual(await q1.qsize(), 1)
        self.assertEqual(await q2.qsize(), 1)
        self.assertEqual(ensure_str(await q2.get()), "value1")
        self.assertTrue(await q1.empty())
        self.assertTrue(await q2.empty())
        self.assertEqual(await q1.qsize(), 0)
        self.assertEqual(await q2.qsize(), 0)
        await self.client1.delete("QUEUE")

    async def test_queue_timeout(self):
        await self.client1.delete("QUEUE")
        q1 = Queue(self.client1)
        q2 = Queue(self.client1)
        self.assertTrue(await q1.empty())
        self.assertTrue(await q2.empty())
        self.assertEqual(await q1.qsize(), 0)
        self.assertEqual(await q2.qsize(), 0)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(q2.get(), 1)

    async def test_bqueue_put_get(self):
        await self.client1.delete("BROADCASTQUEUE")
        b1 = BroadcastQueue(self.client1)
        b2 = BroadcastQueue(self.client2)
        await asyncio.sleep(0.5)
        self.assertTrue(await b1.empty())
        self.assertTrue(await b2.empty())
        self.assertEqual(await b1.qsize(), 0)
        self.assertEqual(await b2.qsize(), 0)
        await b1.put("value2")
        await asyncio.sleep(0.5)
        self.assertFalse(await b1.empty())
        self.assertFalse(await b2.empty())
        self.assertEqual(await b1.qsize(), 1)
        self.assertEqual(await b2.qsize(), 1)

        self.assertEqual(ensure_str(await b1.get()), "value2")
        self.assertEqual(ensure_str(await b2.get()), "value2")

        self.assertTrue(await b1.empty())
        self.assertTrue(await b2.empty())
        self.assertEqual(await b1.qsize(), 0)
        self.assertEqual(await b2.qsize(), 0)

    async def test_bqueue_timeout(self):
        await self.client1.delete("BROADCASTQUEUE")
        b1 = BroadcastQueue(self.client1)
        b2 = BroadcastQueue(self.client2)
        await asyncio.sleep(0.5)
        self.assertTrue(await b1.empty())
        self.assertTrue(await b2.empty())
        self.assertEqual(await b1.qsize(), 0)
        self.assertEqual(await b2.qsize(), 0)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(b2.get(), 1)

    async def test_stream_put_get(self):
        await self.client1.delete("STREAM")
        s1 = Stream(self.client1)
        s2 = Stream(self.client2)
        await asyncio.sleep(0.5)
        with self.assertRaises(TypeError):
            await s1.put("value1")
        self.assertEqual(await s1.qsize(), 0)
        self.assertEqual(await s2.qsize(), 0)
        self.assertTrue(await s1.empty())
        self.assertTrue(await s2.empty())
        await s1.put({"k": "value1"})
        await asyncio.sleep(0.5)
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        data = await s2.get()
        self.assertEqual(
            ensure_str(data.get(b"k", None) or data.get("k", None)), "value1"
        )
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        self.assertEqual(await s2.trim(), 0)
        await s1.put({"k": "value2"})
        await asyncio.sleep(0.5)
        self.assertEqual(await s1.qsize(), 2)
        self.assertEqual(await s2.qsize(), 2)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        data = await s2.get()
        self.assertEqual(
            ensure_str(data.get(b"k", None) or data.get("k", None)), "value2"
        )
        self.assertEqual(await s2.trim(), 1)
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())

    async def test_stream_callback(self):
        await self.client1.delete("STREAM")
        s1 = Stream(self.client1)
        lastid = None

        async def cb(id_):
            nonlocal lastid
            lastid = id_

        s2 = Stream(self.client2, on_cursor_change=cb)
        await asyncio.sleep(0.5)
        with self.assertRaises(TypeError):
            await s1.put("value1")
        self.assertEqual(await s1.qsize(), 0)
        self.assertEqual(await s2.qsize(), 0)
        self.assertTrue(await s1.empty())
        self.assertTrue(await s2.empty())
        await s1.put({"k": "value1"})
        await asyncio.sleep(0.5)
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        data = await s2.get()
        self.assertEqual(
            ensure_str(data.get(b"k", None) or data.get("k", None)), "value1"
        )
        self.assertTrue(lastid is not None)
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        self.assertEqual(await s2.trim(), 0)
        await s1.put({"k": "value2"})
        await asyncio.sleep(0.5)
        self.assertEqual(await s1.qsize(), 2)
        self.assertEqual(await s2.qsize(), 2)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())
        data = await s2.get()
        self.assertEqual(
            ensure_str(data.get(b"k", None) or data.get("k", None)), "value2"
        )
        self.assertEqual(await s2.trim(), 1)
        self.assertEqual(await s1.qsize(), 1)
        self.assertEqual(await s2.qsize(), 1)
        self.assertFalse(await s1.empty())
        self.assertFalse(await s2.empty())

    async def test_stream_timeout(self):
        await self.client1.delete("STREAM")
        s1 = Stream(self.client1)
        s2 = Stream(self.client2)
        await asyncio.sleep(0.5)
        self.assertEqual(await s1.qsize(), 0)
        self.assertEqual(await s2.qsize(), 0)
        self.assertTrue(await s1.empty())
        self.assertTrue(await s2.empty())
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(s2.get(), 1)

    async def test_group_stream(self):
        await self.client1.delete("STREAM", "STREAM:CONSUMER_EXISTS")
        s1 = GroupStream(self.client1, consumer="consumer1")
        s2 = GroupStream(self.client2, consumer="consumer2")
        await s1.put({"k": "value1"})
        await s1.put({"k": "value2"})
        data = await s2.get()
        self.assertEqual(
            ensure_str(data.get("k", None) or data.get(b"k", None)), "value1"
        )
        pending = await s2.check_pending()
        self.assertEqual(
            ensure_str(pending.get("k", None) or pending.get(b"k", None)), "value1"
        )

        data = await s1.get()
        self.assertEqual(
            ensure_str(data.get("k", None) or data.get(b"k", None)), "value2"
        )
        pending = await s1.check_pending()
        self.assertEqual(
            ensure_str(pending.get("k", None) or pending.get(b"k", None)), "value2"
        )

        await s2.ack(s2.last_id)
        pending = await s2.check_pending()
        self.assertIsNone(pending)

        await s1.ack(s1.last_id)
        pending = await s1.check_pending()
        self.assertIsNone(pending)


if __name__ == "__main__":
    unittest.main()
