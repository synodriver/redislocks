# -*- coding: utf-8 -*-
"""
Copyright (c) 2008-2024 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import asyncio
import os
from unittest import IsolatedAsyncioTestCase

from dotenv import load_dotenv
from redis.asyncio import Redis

from redislocks.condition import Condition

load_dotenv("./.env")


class TestCondition(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client1 = Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))
        self.client2 = Redis(host=os.getenv("REDIS"), password=os.getenv("PASSWORD"))
        self.cond1 = Condition(self.client1)
        self.cond2 = Condition(self.client2)
        await self.client1.delete("CONDITION:*")

    async def test_timeout_in_block(self):
        async with self.cond1:
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(self.cond1.wait(), timeout=0.5)

    async def test_cancelled_error_wakeup(self):
        # Test that a cancelled error, received when awaiting wakeup,
        # will be re-raised un-modified.
        wake = False
        raised = None
        cond = self.cond1

        async def func():
            nonlocal raised
            async with cond:
                with self.assertRaises(asyncio.CancelledError) as err:
                    await cond.wait_for(lambda: wake)
                raised = err.exception
                raise raised

        task = asyncio.create_task(func())
        await asyncio.sleep(0.5)
        # Task is waiting on the condition, cancel it there.
        task.cancel(msg="foo")
        with self.assertRaises(asyncio.CancelledError) as err:
            await task

        await asyncio.sleep(0.5)
        # self.assertEqual(err.exception.args, ("foo",))
        # We should have got the _same_ exception instance as the one
        # originally raised.
        # self.assertIs(err.exception, raised)

    async def test_cancelled_error_re_aquire(self):
        # Test that a cancelled error, received when re-aquiring lock,
        # will be re-raised un-modified.
        wake = False
        raised = None
        cond = self.cond1

        async def func():
            nonlocal raised
            async with cond:
                with self.assertRaises(asyncio.CancelledError) as err:
                    await cond.wait_for(lambda: wake)
                raised = err.exception
                raise raised

        task = asyncio.create_task(func())
        await asyncio.sleep(0.5)
        # Task is waiting on the condition
        await cond.acquire()
        wake = True
        await cond.notify()
        await asyncio.sleep(0.5)
        # Task is now trying to re-acquire the lock, cancel it there.
        task.cancel(msg="foo")
        await cond.release()
        with self.assertRaises(asyncio.CancelledError) as err:
            await task
        # self.assertEqual(err.exception.args, ("foo",))
        # We should have got the _same_ exception instance as the one
        # originally raised.
        # self.assertIs(err.exception, raised)

    async def test_cancelled_wakeup(self):
        # Test that a task cancelled at the "same" time as it is woken
        # up as part of a Condition.notify() does not result in a lost wakeup.
        # This test simulates a cancel while the target task is awaiting initial
        # wakeup on the wakeup queue.
        condition = Condition(self.client1)
        await condition.reset()
        state = 0

        async def consumer():
            nonlocal state
            async with condition:
                while True:
                    await condition.wait_for(lambda: state != 0)
                    if state < 0:
                        return
                    state -= 1

        # create two consumers
        c = [asyncio.create_task(consumer()) for _ in range(2)]
        # wait for them to settle
        await asyncio.sleep(0.5)
        async with condition:
            # produce one item and wake up one
            state += 1
            await condition.notify(1)
            await asyncio.sleep(
                0.5
            )  # let condition._listen_task run and set future's result
            # Cancel it while it is awaiting to be run.
            # This cancellation could come from the outside
            c[0].cancel()
            await asyncio.sleep(0.5)

            # now wait for the item to be consumed
            # if it doesn't means that our "notify" didn"t take hold.
            # because it raced with a cancel()
            try:
                await asyncio.wait_for(condition.wait_for(lambda: state == 0), 0.5)
            except asyncio.TimeoutError:
                pass
            self.assertEqual(state, 0)

            # clean up
            state = -1
            await condition.notify_all()
        await c[1]

    async def test_cancelled_wakeup_relock(self):
        # Test that a task cancelled at the "same" time as it is woken
        # up as part of a Condition.notify() does not result in a lost wakeup.
        # This test simulates a cancel while the target task is acquiring the lock
        # again.
        condition = self.cond2
        state = 0

        async def consumer():
            nonlocal state
            async with condition:
                while True:
                    await condition.wait_for(lambda: state != 0)
                    if state < 0:
                        return
                    state -= 1

        # create two consumers
        c = [asyncio.create_task(consumer()) for _ in range(2)]
        # wait for them to settle
        await asyncio.sleep(0.5)
        async with condition:
            # produce one item and wake up one
            state += 1
            await condition.notify(1)

            # now we sleep for a bit.  This allows the target task to wake up and
            # settle on re-aquiring the lock
            await asyncio.sleep(0.5)

            # Cancel it while awaiting the lock
            # This cancel could come the outside.
            c[0].cancel()

            # now wait for the item to be consumed
            # if it doesn't means that our "notify" didn"t take hold.
            # because it raced with a cancel()
            try:
                await asyncio.wait_for(condition.wait_for(lambda: state == 0), 0.5)
            except asyncio.TimeoutError:
                pass
            self.assertEqual(state, 0)

            # clean up
            state = -1
            await condition.notify_all()
        await c[1]

    async def asyncTearDown(self):
        await self.cond1.reset()
