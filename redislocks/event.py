# -*- coding: utf-8 -*-
import asyncio
from typing import Optional

from redis.asyncio import Redis


class Event:
    """Asynchronous Distribute equivalent to asyncio.Event, based on redis.

    Class implementing event objects. An event manages a flag that can be set
    to true with the set() method and reset to false with the clear() method.
    The wait() method blocks until the flag is true. The flag is initially
    false.

    NAMESPACE:SET
    NAMESPACE:WAITER
    NAMESPACE:WAITERPOP
    """

    def __init__(
        self,
        client: Optional[Redis] = None,
        namespace: str = "EVENT",
    ):
        self.client = client or Redis()
        self.namespace = namespace

        self.set_key = self.get_namespaced_key("SET")
        self.waiter_key = self.get_namespaced_key("WAITER")
        self.waiter_pop_key = self.get_namespaced_key("WAITERPOP")

        self._wait_script = self.client.register_script(
            """
        local namespace = KEYS[1]
        local set_key = namespace .. ":SET"
        local waiter_key = namespace .. ":WAITER"
        
        if redis.call("EXISTS", set_key)==1 then
            return 1
        end
        local time = redis.call("TIME")
        local timestring = time[1] ..".".. time[2]
        redis.call("RPUSH", waiter_key, timestring)
        return 0
        """
        )
        self._set_script = self.client.register_script(
            """
            local namespace = KEYS[1]
            local set_key = namespace .. ":SET"
            local waiter_key = namespace .. ":WAITER"
            local waiter_pop_key = namespace .. ":WAITERPOP"
            
            if redis.call("SET", set_key, "1", "NX") then
                while true do
                    local token = redis.call("LPOP", waiter_key)
                    if not token then
                        break
                    end
                    redis.call("RPUSH", waiter_pop_key, token)
                end
            end
            """
        )
        self._cancelwait_script = self.client.register_script(
            """
            local namespace = KEYS[1]
            local waiter_key = namespace .. ":WAITER"
            local waiter_pop_key = namespace .. ":WAITERPOP"

            if not redis.call("LPOP", waiter_key) then
                redis.call("LPOP", waiter_pop_key)
            end
            """
        )

    async def is_set(self) -> bool:
        """Return True if and only if the internal redis flag exists."""
        return bool(await self.client.exists(self.set_key))

    async def set(self):
        """Set the internal flag to true. All tasks waiting for it to
        become true are awakened. Tasks that call wait() once the flag is
        true will not block at all.
        """
        await self._set_script([self.namespace])

    async def clear(self):
        """Reset the internal flag to false. Subsequently, tasks calling
        wait() will block until set() is called to set the internal flag
        to true again."""
        await self.client.delete(self.set_key)

    async def wait(self):
        """Block until the internal flag is true.

        If the internal flag is true on entry, return True
        immediately.  Otherwise, block until another task calls
        set() to set the flag to true, then return True.
        """
        if await self._wait_script([self.namespace]):
            return True
        # token: str = await self.current_time  # type: ignore
        try:
            await self.client.blpop(self.waiter_pop_key)
            return True
        except asyncio.CancelledError:
            err = None
            while True:
                try:
                    await self._cancelwait_script([self.namespace])  # 别在这cancel
                    break
                except asyncio.CancelledError as e:
                    err = e
            if err is not None:
                try:
                    raise err
                finally:
                    err = None
            raise

    def get_namespaced_key(self, suffix):
        return "{0}:{1}".format(self.namespace, suffix)

    @property
    async def current_time(self) -> str:
        return ".".join(map(str, await self.client.time()))

    async def reset(self):
        await self.client.delete(self.set_key, self.waiter_key, self.waiter_pop_key)

    async def aclose(self):
        await self.reset()
        await self.client.aclose()
