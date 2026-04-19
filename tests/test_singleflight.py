# -*- coding: utf-8 -*-
"""
Copyright (c) 2008-2026 synodriver <diguohuangjiajinweijun@gmail.com>
"""

import asyncio
import os
import unittest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from dotenv import load_dotenv
from redis.asyncio import Redis

from redislocks.singleflight import SingleFlight

load_dotenv(str(Path(__file__).parent.resolve() / ".env"))


class TestSingleFlight(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client1 = Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))
        self.sf1 = SingleFlight(self.client1, namespace="demo1", ttl=5)
        self.sf2 = SingleFlight(self.client1, namespace="demo2", ttl=5)
        self.sf3 = SingleFlight(self.client1, namespace="demo3", ttl=5)

    async def test_singleflight_noclean(self):
        data = 0

        async def func():
            nonlocal data
            await asyncio.sleep(0.1)
            data += 1
            return 32

        t1 = asyncio.create_task(self.sf1.do("key", func))
        t2 = asyncio.create_task(self.sf1.do("key", func))
        t3 = asyncio.create_task(self.sf1.do("key", func))
        await asyncio.gather(t1, t2, t3)
        self.assertEqual(data, 1)
        self.assertEqual(t1.result(), 32)
        self.assertEqual(t2.result(), 32)
        self.assertEqual(t3.result(), 32)
        data = 0
        t1 = asyncio.create_task(self.sf1.do("key", func))
        t2 = asyncio.create_task(self.sf1.do("key", func))
        t3 = asyncio.create_task(self.sf1.do("key2", func))
        t4 = asyncio.create_task(self.sf1.do("key2", func))
        await asyncio.gather(t1, t2, t3, t4)
        self.assertEqual(data, 2)
        t5 = asyncio.create_task(self.sf1.do("key", func))
        await t5
        self.assertEqual(data, 3)

    async def test_singleflight(self):
        await self.client1.delete("demo*")
        data = 0

        async def func():
            nonlocal data
            await asyncio.sleep(0.1)
            data += 1
            return 32

        t1 = asyncio.create_task(self.sf1.do("key", func))
        t2 = asyncio.create_task(self.sf1.do("key", func))
        t3 = asyncio.create_task(self.sf1.do("key", func))
        await asyncio.gather(t1, t2, t3)
        self.assertEqual(data, 1)
        self.assertEqual(t1.result(), 32)
        self.assertEqual(t2.result(), 32)
        self.assertEqual(t3.result(), 32)
        data = 0
        t1 = asyncio.create_task(self.sf1.do("key", func))
        t2 = asyncio.create_task(self.sf1.do("key", func))
        t3 = asyncio.create_task(self.sf1.do("key2", func))
        t4 = asyncio.create_task(self.sf1.do("key2", func))
        await asyncio.gather(t1, t2, t3, t4)
        self.assertEqual(data, 2)
        t5 = asyncio.create_task(self.sf1.do("key", func))
        await t5
        self.assertEqual(data, 3)

    async def test_singleflight_err_noclean(self):
        await self.client1.delete("demo*")
        data = 0

        async def func():
            nonlocal data
            data += 1
            await asyncio.sleep(0.1)
            raise ValueError

        t1 = asyncio.create_task(self.sf2.do("key", func))
        t2 = asyncio.create_task(self.sf2.do("key", func))
        t3 = asyncio.create_task(self.sf2.do("key", func))
        try:
            await asyncio.gather(t1, t2, t3)
        except BaseException as e:
            print(f"[gather exception] type={type(e).__name__}, {e}")

        for t in [t1, t2, t3]:
            if t.done():
                try:
                    t.result()
                except BaseException as ex:
                    print(f"  task {t.get_name()} exception: {type(ex).__name__}")
            else:
                print(f"  task {t.get_name()} still pending!")

        self.assertEqual(data, 1, "data is not 1")

    async def test_singleflight_err(self):
        await self.client1.delete("demo*")
        data = 0

        async def func():
            nonlocal data
            data += 1
            await asyncio.sleep(0.1)
            raise ValueError

        t1 = asyncio.create_task(self.sf2.do("key", func))
        t2 = asyncio.create_task(self.sf2.do("key", func))
        t3 = asyncio.create_task(self.sf2.do("key", func))
        with self.assertRaises(ValueError):
            await asyncio.gather(t1, t2, t3)
        self.assertEqual(data, 1)

    async def test_cancel(self):
        await self.client1.delete("demo*")
        data = 0

        async def func():
            nonlocal data
            await asyncio.sleep(5)
            data += 1

        t1 = asyncio.create_task(self.sf3.do("key", func))
        t2 = asyncio.create_task(self.sf3.do("key", func))
        t3 = asyncio.create_task(self.sf3.do("key", func))
        await asyncio.sleep(
            1
        )  # 必须有，没有这个，三个task还没执行到await self._wait，就被取消了
        t1.cancel()
        try:
            await t1
        except asyncio.CancelledError:
            pass
        # with self.assertRaises(asyncio.CancelledError):
        #     await t1
        with self.assertRaises(asyncio.CancelledError):
            await t2
        with self.assertRaises(asyncio.CancelledError):
            await t3


class TestSingleFlightResp3(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client1 = Redis(
            host=os.getenv("REDIS"), password=os.getenv("PASSWORD"), protocol=3
        )
        self.sf1 = SingleFlight(self.client1, namespace="demo1", ttl=5)
        self.sf2 = SingleFlight(self.client1, namespace="demo2", ttl=5)
        self.sf3 = SingleFlight(self.client1, namespace="demo3", ttl=5)

    async def test_singleflight_noclean(self):
        data = 0

        async def func():
            nonlocal data
            await asyncio.sleep(0.1)
            data += 1
            return 32

        t1 = asyncio.create_task(self.sf1.do("key", func))
        t2 = asyncio.create_task(self.sf1.do("key", func))
        t3 = asyncio.create_task(self.sf1.do("key", func))
        await asyncio.gather(t1, t2, t3)
        self.assertEqual(data, 1)
        self.assertEqual(t1.result(), 32)
        self.assertEqual(t2.result(), 32)
        self.assertEqual(t3.result(), 32)
        data = 0
        t1 = asyncio.create_task(self.sf1.do("key", func))
        t2 = asyncio.create_task(self.sf1.do("key", func))
        t3 = asyncio.create_task(self.sf1.do("key2", func))
        t4 = asyncio.create_task(self.sf1.do("key2", func))
        await asyncio.gather(t1, t2, t3, t4)
        self.assertEqual(data, 2)
        t5 = asyncio.create_task(self.sf1.do("key", func))
        await t5
        self.assertEqual(data, 3)

    async def test_singleflight(self):
        await self.client1.delete("demo*")
        data = 0

        async def func():
            nonlocal data
            await asyncio.sleep(0.1)
            data += 1
            return 32

        t1 = asyncio.create_task(self.sf1.do("key", func))
        t2 = asyncio.create_task(self.sf1.do("key", func))
        t3 = asyncio.create_task(self.sf1.do("key", func))
        await asyncio.gather(t1, t2, t3)
        self.assertEqual(data, 1)
        self.assertEqual(t1.result(), 32)
        self.assertEqual(t2.result(), 32)
        self.assertEqual(t3.result(), 32)
        data = 0
        t1 = asyncio.create_task(self.sf1.do("key", func))
        t2 = asyncio.create_task(self.sf1.do("key", func))
        t3 = asyncio.create_task(self.sf1.do("key2", func))
        t4 = asyncio.create_task(self.sf1.do("key2", func))
        await asyncio.gather(t1, t2, t3, t4)
        self.assertEqual(data, 2)
        t5 = asyncio.create_task(self.sf1.do("key", func))
        await t5
        self.assertEqual(data, 3)

    async def test_singleflight_err_noclean(self):
        await self.client1.delete("demo*")
        data = 0

        async def func():
            nonlocal data
            data += 1
            await asyncio.sleep(0.1)
            raise ValueError

        t1 = asyncio.create_task(self.sf2.do("key", func))
        t2 = asyncio.create_task(self.sf2.do("key", func))
        t3 = asyncio.create_task(self.sf2.do("key", func))
        try:
            await asyncio.gather(t1, t2, t3)
        except BaseException as e:
            print(f"[gather exception] type={type(e).__name__}, {e}")

        for t in [t1, t2, t3]:
            if t.done():
                try:
                    t.result()
                except BaseException as ex:
                    print(f"  task {t.get_name()} exception: {type(ex).__name__}")
            else:
                print(f"  task {t.get_name()} still pending!")

        self.assertEqual(data, 1, "data is not 1")

    async def test_singleflight_err(self):
        await self.client1.delete("demo*")
        data = 0

        async def func():
            nonlocal data
            data += 1
            await asyncio.sleep(0.1)
            raise ValueError

        t1 = asyncio.create_task(self.sf2.do("key", func))
        t2 = asyncio.create_task(self.sf2.do("key", func))
        t3 = asyncio.create_task(self.sf2.do("key", func))
        with self.assertRaises(ValueError):
            await asyncio.gather(t1, t2, t3)
        self.assertEqual(data, 1)

    async def test_cancel(self):
        await self.client1.delete("demo*")
        data = 0

        async def func():
            nonlocal data
            await asyncio.sleep(5)
            data += 1

        t1 = asyncio.create_task(self.sf3.do("key", func))
        t2 = asyncio.create_task(self.sf3.do("key", func))
        t3 = asyncio.create_task(self.sf3.do("key", func))
        await asyncio.sleep(
            1
        )  # 必须有，没有这个，三个task还没执行到await self._wait，就被取消了
        t1.cancel()
        try:
            await t1
        except asyncio.CancelledError:
            pass
        # with self.assertRaises(asyncio.CancelledError):
        #     await t1
        with self.assertRaises(asyncio.CancelledError):
            await t2
        with self.assertRaises(asyncio.CancelledError):
            await t3


if __name__ == "__main__":
    unittest.main()

    # async def main():
    #     import time
    #
    #     client = Redis(host="127.0.0.1", port=6379, decode_responses=False)
    #     sf = SingleFlight(client, namespace="demo", ttl=30)
    #
    #     async def expensive_query(user_id: int) -> dict:
    #         print(f"  [executor] querying user_id={user_id} ...")
    #         await asyncio.sleep(2)  # 模拟耗时操作
    #         return {"user_id": user_id, "name": "Alice"}
    #
    #     # 10 个并发请求，只有 1 个会真正执行 expensive_query
    #     async def worker(i: int):
    #         t0 = time.monotonic()
    #         result = await sf.do("user:42", expensive_query, 42, wait_timeout=10)
    #         elapsed = time.monotonic() - t0
    #         print(f"  [worker {i}] got result={result}  ({elapsed:.2f}s)")
    #
    #     await asyncio.gather(*(worker(i) for i in range(10)))
    #     await client.aclose()
    #
    # asyncio.run(main())

