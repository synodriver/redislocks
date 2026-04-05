# -*- coding: utf-8 -*-
"""
Copyright (c) 2008-2024 synodriver <diguohuangjiajinweijun@gmail.com>
"""

import asyncio
from asyncio import Queue as AIOQueue
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from redis.asyncio import Redis

from redislocks.utils import ensure_str


class Queue:
    """
    queue uses blpop
    """

    def __init__(self, client: Optional[Redis] = None, namespace: str = "QUEUE"):
        self.client = client or Redis()
        self.namespace = namespace

    async def put(self, value):
        await self.client.rpush(self.namespace, value)

    async def get(self):
        pair = await self.client.blpop(self.namespace)
        return pair[1]

    async def empty(self) -> bool:
        return await self.qsize() == 0  # type: ignore

    async def qsize(self):
        return await self.client.llen(self.namespace)


class BroadcastQueue(Queue):
    def __init__(
        self, client: Optional[Redis] = None, namespace: str = "BROADCASTQUEUE"
    ):
        super().__init__(client, namespace)
        self._listen_task = asyncio.create_task(self._listen_events())
        self._queue = AIOQueue()  # type: AIOQueue

    async def _listen_events(self):
        async with self.client.pubsub() as pubsub:
            await pubsub.subscribe(
                f"{self.namespace}",
            )
            async for event in pubsub.listen():
                if (
                    ensure_str(event["type"]) == "message"
                    and ensure_str(event["channel"]) == self.namespace
                ):
                    data = ensure_str(event["data"])
                    await self._queue.put(data)

    def __del__(self):
        if self._listen_task is not None:
            self._listen_task.cancel()
            self._listen_task = None

    async def aclose(self):
        self._listen_task.cancel()
        try:
            await self._listen_task
        except asyncio.CancelledError:
            pass
        self._listen_task = None
        await self.client.aclose()

    async def put(self, value):
        """
        自己发的自己也能收到，很正常
        :param value:
        :return:
        """
        await self.client.publish(self.namespace, value)

    async def get(self):
        return await self._queue.get()

    async def empty(self) -> bool:
        return self._queue.empty()

    async def qsize(self):
        return self._queue.qsize()


class Stream:
    """
    use redis's stream api
    """

    def __init__(
        self,
        client: Optional[Redis] = None,
        namespace: str = "STREAM",
        maxlen: int = 100,
        last_id: Optional[str] = None,
        on_cursor_change: Optional[
            Callable[[str], Union[None, Awaitable[None]]]
        ] = None,
    ):
        self.client = client or Redis()
        self.namespace = namespace
        self.maxlen = maxlen
        self.last_id = last_id or "0-0"
        self.on_cursor_change = on_cursor_change

    async def put(self, value: dict):
        if not isinstance(value, dict):
            raise TypeError("value must be dict")
        return await self.client.xadd(
            self.namespace, value, maxlen=self.maxlen, approximate=False
        )

    async def _run_callback(self):
        if self.on_cursor_change is not None:
            ret = self.on_cursor_change(self.last_id)
            if asyncio.iscoroutine(ret):
                await ret

    async def get(self, id_=None):
        data = await self.client.xread({self.namespace: id_ or self.last_id}, 1, 0)
        if isinstance(data, list):
            self.last_id = ensure_str(data[0][1][0][0])
            await self._run_callback()  # 保存last_id的机会，防止重复消费
            return data[0][1][0][1]
        else:  # resp 3 dict
            self.last_id = ensure_str(list(data.values())[0][0][0][0])
            await self._run_callback()
            return list(data.values())[0][0][0][1]

    async def qsize(self):
        return await self.client.xlen(self.namespace)

    async def empty(self) -> bool:
        return await self.qsize() == 0

    async def trim(
        self,
        maxlen: Optional[int] = None,
        minid: Optional[int] = None,
        limit: Optional[int] = None,
        approximate: Optional[bool] = False,
    ):
        kw = {"approximate": approximate}  # type: Dict[str, Any]
        if maxlen is not None:
            kw["maxlen"] = maxlen
        else:
            kw["minid"] = minid or self.last_id
        if limit is not None:
            kw["limit"] = limit
        return await self.client.xtrim(self.namespace, **kw)  # type: ignore

    async def delete(self, *ids):
        return await self.client.xdel(self.namespace, *ids)


class GroupStream(Stream):
    def __init__(
        self,
        client: Optional[Redis] = None,
        namespace: str = "STREAM",
        group: str = "GROUP",
        consumer: str = "CONSUMER",
        maxlen: int = 100,
        last_id: Optional[str] = None,
        on_cursor_change: Optional[
            Callable[[str], Union[None, Awaitable[None]]]
        ] = None,
    ):
        super().__init__(client, namespace, maxlen, last_id, on_cursor_change)
        self.group = group
        self.consumer = consumer
        self.consumer_exists_key = self.get_namespaced_key("CONSUMER_EXISTS")

    def get_namespaced_key(self, suffix):
        return "{0}:{1}".format(self.namespace, suffix)

    async def _check_group(self):
        # if not await self.client.exists(self.consumer_exists_key):
        async with self.client.pipeline() as pipe:
            await pipe.watch(self.consumer_exists_key)
            if await pipe.exists(self.consumer_exists_key):
                await pipe.reset()
                return None
            pipe.multi()
            pipe.xgroup_create(
                self.namespace, self.group, id=self.last_id, mkstream=True
            )
            pipe.set(self.consumer_exists_key, "1")
            await pipe.execute()

    async def put(self, value: dict):
        await self._check_group()
        return await super().put(value)

    async def check_pending(self, id_=None):
        await self._check_group()
        data = await self.client.xreadgroup(
            self.group, self.consumer, {self.namespace: id_ or "0-0"}, 1, 0
        )
        try:
            if isinstance(data, list):
                self.last_id = ensure_str(data[0][1][0][0])
                await self._run_callback()  # 保存last_id的机会，防止重复消费
                return data[0][1][0][1]
            else:  # resp 3 dict
                self.last_id = ensure_str(list(data.values())[0][0][0][0])
                await self._run_callback()
                return list(data.values())[0][0][0][1]
        except IndexError:
            return None

    async def get(self):
        await self._check_group()
        data = await self.client.xreadgroup(
            self.group, self.consumer, {self.namespace: ">"}, 1, 0
        )
        if isinstance(data, list):
            self.last_id = ensure_str(data[0][1][0][0])
            await self._run_callback()  # 保存last_id的机会，防止重复消费
            return data[0][1][0][1]
        else:  # resp 3 dict
            self.last_id = ensure_str(list(data.values())[0][0][0][0])
            await self._run_callback()
            return list(data.values())[0][0][0][1]

    async def ack(self, *ids):
        """
        调用get后处理完成后，需要调用ack
        :param ids:
        :return:
        """
        await self._check_group()
        return await self.client.xack(self.namespace, self.group, *ids)

    async def aclose(self):
        await self.client.xgroup_destroy(self.namespace, self.group)
        await self.client.delete(self.consumer_exists_key, self.namespace)
        await self.client.aclose()
