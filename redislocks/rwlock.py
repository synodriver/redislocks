"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import time
from typing import Literal, Optional, Union

from redis.asyncio import Redis


class RWLock:
    def __init__(
        self,
        client: Optional[Redis] = None,
    ):
        self.client = client or Redis()
        self.is_use_local_time = False
        ...

    async def acquire(self, mode: Literal["r", "w"] = "r") -> Union[str, bytes]:
        ...

    async def release(self, token: Union[str, bytes]):
        ...

    @property
    async def current_time(self):
        if self.is_use_local_time:
            return time.time()
        return float(".".join(map(str, await self.client.time())))
