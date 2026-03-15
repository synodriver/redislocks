"""
Copyright (c) 2008-2023 synodriver <diguohuangjiajinweijun@gmail.com>
"""
import asyncio
import logging
import uuid
from enum import IntEnum
from typing import Dict, List, Literal, Optional

from redis.asyncio import Redis
from redis.exceptions import ConnectionError, RedisError, TimeoutError

from redislocks.exceptions import NotAvailable
from redislocks.utils import ensure_str


class LockState(IntEnum):
    empty = 0  # 空
    reading = 1  # 只有读锁
    writing = 2  # 有写锁
    waiting_write = 3  # 有读锁，没有写锁，但是有写锁在等待队列，因此此时不能继续获取读锁


# fixme 如果A获取锁，B获取的时候认为A超时给A释放了，此时AB同时持有锁。因此当前不允许超时机制
class RWLock:
    """
    Redis内存视图
    "RWLOCK:EXISTS" : "ok" 判断是否存在
    "RWLOCK:READ": Set[str] 已经被获取的读锁，里面是他们的申请时间戳, redis把float当str
    "RWLOCK:WRITE": "1151.1919810" 已经被获取的写锁和他的申请时间戳
    "RWLOCK:WRITEWAITER": List[str] 等待获取写锁的，里面是他们的申请时间戳

    写锁优先，如果存在写锁或者存在等待获取写锁的，读锁只能先行等待进入等待队列
    """

    exists_val = "ok"
    _logger = logging.getLogger("redislocks.rwlock")

    def __init__(
        self,
        client: Optional[Redis] = None,
        namespace: str = "RWLOCK",
        blocking: bool = True,
        reconnect_base_delay: float = 0.5,
        reconnect_max_delay: float = 30.0,
        reconnect_max_retries: Optional[int] = None,
    ):
        """

        :param client: redis client
        :param namespace: lock的命名空间，相同的视为同一把锁，使用相同的redis key
        :param blocking: 阻塞与否 非阻塞模式下，如果不能立刻获取锁则会抛出NotAvailable
        :param reconnect_base_delay: pubsub重连的基础延迟(秒)，每次翻倍直到max_delay
        :param reconnect_max_delay: pubsub重连的最大延迟(秒)
        :param reconnect_max_retries: pubsub最大重连次数，None为无限重试
        """
        self.client = client or Redis()
        self.namespace = namespace
        self.blocking = blocking
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._reconnect_max_retries = reconnect_max_retries

        self.check_exists_key = self.get_namespaced_key("EXISTS")  # RWLOCK:EXISTS
        self.read_key = self.get_namespaced_key("READ")
        self.write_key = self.get_namespaced_key("WRITE")
        self.write_waiter_key = self.get_namespaced_key("WRITEWAITER")
        self.notify_channel = self.get_namespaced_key("NOTIFY")  # 写锁帮轮通知channel

        self._read_waiters = []  # type: List[asyncio.Future]
        self._write_waiters = {}  # type: Dict[str, asyncio.Future]

        self._lockread_script = self.client.register_script(
            """
-- 加读锁
-- numkey: 1
-- namespace
-- ARGV[1]: 由调用方生成的唯一token
local namespace = KEYS[1]
local token = ARGV[1]
local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

local function get_state()
    local read_lock_exists = redis.call("SCARD", read_key) > 0
    local write_lock_exists = redis.call("EXISTS", write_key) == 1
    local write_waiter_exists = redis.call("LLEN", write_waiter_key) > 0
    if not read_lock_exists and not write_lock_exists then -- 没有读锁也没有写锁，是空的
        return 0
    elseif read_lock_exists and not write_lock_exists and not write_waiter_exists then -- 存在读锁，不存在写锁和写锁等待，读ing
        return 1
    elseif not read_lock_exists and write_lock_exists then -- 不存在读锁，存在写锁，写ing
        return 2
    elseif read_lock_exists and not write_lock_exists and  write_waiter_exists then -- 存在读锁，不存在写锁，不过有等待等待队列有东西
        return 3
    end
end

local current_state = get_state()

if current_state == nil then
    return redis.error_reply("unknown state")
end

if current_state == 2 or current_state == 3 then
    -- 写入状态 or 写锁正在等待等待
    return 0 -- 直接加锁失败，此时，如果是阻塞模式，开始监听keyspace
else
    redis.call("SADD", read_key, token)
    return token -- 成功就返回token
end
            """
        )  # lockread.lua
        self._unlockread_script = self.client.register_script(
            """
-- 释放读锁
-- numkey: 2
-- namespace token
-- ARGV[1]: notify_channel 用于PUBLISH帮轮上位的写锁token
local namespace = KEYS[1]
local token = KEYS[2] -- read token
local notify_channel = ARGV[1]
local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

local read_lock_exists = redis.call("SCARD", read_key) > 0
local write_lock_exists = redis.call("EXISTS", write_key) == 1
local write_waiter_exists = redis.call("LLEN", write_waiter_key) > 0

local function get_state()
    if not read_lock_exists and not write_lock_exists then -- 没有读锁也没有写锁，是空的
        return 0
    elseif read_lock_exists and not write_lock_exists and not write_waiter_exists then -- 存在读锁，不存在写锁和写锁等待，读ing
        return 1
    elseif not read_lock_exists and write_lock_exists then -- 不存在读锁，存在写锁，写ing
        return 2
    elseif read_lock_exists and not write_lock_exists and  write_waiter_exists then -- 存在读锁，不存在写锁，不过有等待等待队列有东西
        return 3
    end
end

local current_state = get_state()

if current_state == nil then
    return redis.error_reply("unknown state")
end

local ret = redis.call("SREM", read_key, token)
if redis.call("SCARD", read_key) == 0 and current_state == 3 then
    --读锁空了，有人在等写锁，且写锁现在还不存在， 那去掉读锁的过程就帮他们轮一下写锁
    local write_token = redis.call("LPOP", write_waiter_key)
    redis.call("SET", write_key, write_token)
    redis.call("PUBLISH", notify_channel, write_token)
end

return ret
            """
        )  # unlockread.lua
        self._lockwrite_script = self.client.register_script(
            """
-- 加写锁 直接设置不检查
-- numkey: 1
-- namespace
-- ARGV[1]: 由调用方生成的唯一token
local namespace = KEYS[1]
local token = ARGV[1]
local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

local function get_state()
    local read_lock_exists = redis.call("SCARD", read_key) > 0
    local write_lock_exists = redis.call("EXISTS", write_key) == 1
    local write_waiter_exists = redis.call("LLEN", write_waiter_key) > 0
    if not read_lock_exists and not write_lock_exists then -- 没有读锁也没有写锁，是空的
        return 0
    elseif read_lock_exists and not write_lock_exists and not write_waiter_exists then -- 存在读锁，不存在写锁和写锁等待，读ing
        return 1
    elseif not read_lock_exists and write_lock_exists then -- 不存在读锁，存在写锁，写ing
        return 2
    elseif read_lock_exists and not write_lock_exists and  write_waiter_exists then -- 存在读锁，不存在写锁，不过有等待等待队列有东西
        return 3
    end
end

local current_state = get_state()

if current_state == nil then
    return redis.error_reply("unknown state")
end

if current_state == 0 then
    -- 不存在写锁 也不存在 读锁 可以直接设置写锁
    redis.call("SET", write_key, token)
    return token -- 获取写锁成功，返回token
else
    return 0
end
            """
        )  # lockwrite.lua
        self._unlockwrite_script = self.client.register_script(
            """
-- 老写锁释放的时候带新写锁进来，或者读锁没有的时候带新写锁尽量
-- numkey: 2
-- namespace, old_token
-- ARGV[1]: notify_channel 用于PUBLISH帮轮上位的写锁token
local namespace = KEYS[1]
local old_token = KEYS[2]
local notify_channel = ARGV[1]
local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

local read_lock_exists = redis.call("SCARD", read_key) > 0
local write_lock_exists = redis.call("EXISTS", write_key) == 1
local write_waiter_exists = redis.call("LLEN", write_waiter_key) > 0

local function get_state()
    if not read_lock_exists and not write_lock_exists then -- 没有读锁也没有写锁，是空的
        return 0
    elseif read_lock_exists and not write_lock_exists and not write_waiter_exists then -- 存在读锁，不存在写锁和写锁等待，读ing
        return 1
    elseif not read_lock_exists and write_lock_exists then -- 不存在读锁，存在写锁，写ing
        return 2
    elseif read_lock_exists and not write_lock_exists and write_waiter_exists then -- 存在读锁，不存在写锁，不过有等待等待队列有东西
        return 3
    end
end

local current_state = get_state()

if current_state == nil then
    return redis.error_reply("unknown state")
end

if current_state == 2 then
    if redis.call("GET", write_key) ~= old_token then -- 这不是我的token, 谁动了我的写锁
        return 0
    end
    if write_waiter_exists then -- 还有人在等写锁，帮他轮
        local write_token = redis.call("LPOP", write_waiter_key)
        redis.call("SET", write_key, write_token)
        redis.call("PUBLISH", notify_channel, write_token)
    else --  后面没有人在等写锁了，那就删除写锁
        redis.call("DEL", write_key)
    end
    return 1
else
    return 0
end
            """
        )  # unlockwrite.lua
        self._cancellockwrite_script = self.client.register_script(
            """
-- 取消加写锁
-- numkey: 2
-- namespace， token
-- ARGV[1]: notify_channel 用于PUBLISH帮轮上位的写锁token
local namespace = KEYS[1]
local token = KEYS[2]
local notify_channel = ARGV[1]

local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

local write_waiter_exists = redis.call("LLEN", write_waiter_key) > 0

if redis.call("LREM", write_waiter_key, 1, token) == 0 then
    if redis.call("GET", write_key) == token then
        if write_waiter_exists then -- 还有人在等写锁，帮他轮
            local write_token = redis.call("LPOP", write_waiter_key)
            redis.call("SET", write_key, write_token)
            redis.call("PUBLISH", notify_channel, write_token)
        else --  后面没有人在等写锁了，那就删除写锁
            redis.call("DEL", write_key)
        end
    end
end
            """
        )  # cancellockwrite.lua
        self._get_state_script = self.client.register_script(
            """
-- 检查读锁或者写锁能否立刻获取
-- numkey: 1
-- key: namespace
local namespace = KEYS[1]
local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

local read_lock_exists = redis.call("SCARD", read_key) > 0
local write_lock_exists = redis.call("EXISTS", write_key) == 1
local write_waiter_exists = redis.call("LLEN", write_waiter_key) > 0
if not read_lock_exists and not write_lock_exists then -- 没有读锁也没有写锁，是空的
    return 0
elseif read_lock_exists and not write_lock_exists and not write_waiter_exists then -- 存在读锁，不存在写锁和写锁等待，读ing
    return 1
elseif not read_lock_exists and write_lock_exists then -- 不存在读锁，存在写锁，写ing
    return 2
elseif read_lock_exists and not write_lock_exists and  write_waiter_exists then -- 存在读锁，不存在写锁，不过有等待等待队列有东西
    return 3
end
            """
        )
        self._local_readtokens = []  # type: List[str]
        self._local_writetoken = None  # type: Optional[str]
        self._listen_task = asyncio.create_task(self._listen_events())

    def __del__(self):
        if self._listen_task is not None:
            self._listen_task.cancel()
            # try:
            #     await self._listen_task
            # except asyncio.CancelledError:
            #     pass
            self._listen_task = None

    async def _exists_or_init(self) -> None:
        if await self.client.set(self.check_exists_key, self.exists_val, nx=True):
            await self.client.config_set(
                "notify-keyspace-events", "Ag$lshzxeKEtmdn"
            )  # todo 需要修改配置吗

    async def reset(self):
        """
        删除redis中的key，重置状态，释放持有的token
        :return:
        """
        self._read_waiters.clear()
        self._write_waiters.clear()
        self._local_readtokens.clear()
        self._local_writetoken = None
        await self.client.delete(self.read_key, self.write_key, self.write_waiter_key)

    async def aclose(self):
        self._read_waiters.clear()
        self._write_waiters.clear()
        self._local_readtokens.clear()
        self._local_writetoken = None
        self._listen_task.cancel()
        try:
            await self._listen_task
        except asyncio.CancelledError:
            pass
        self._listen_task = None
        await self.client.delete(
            self.check_exists_key, self.read_key, self.write_key, self.write_waiter_key
        )
        await self.client.aclose()

    async def release_all(self):
        for _ in range(len(self._local_readtokens)):
            await self.release("r")
        if self._local_writetoken is not None:
            await self.release("w")

    def _get_db(self) -> int:
        return self.client.get_connection_kwargs()["db"]

    async def acquire(self, mode: Literal["r", "w"] = "r") -> str:
        await self._exists_or_init()
        if mode == "r":
            token_candidate = uuid.uuid4().hex
            if token := await self._lockread_script(
                [self.namespace], [token_candidate]
            ):
                token = ensure_str(token)
                self._local_readtokens.append(token)
                return token
            if self.blocking:  # 不能立刻获取到读锁，阻塞模式(默认)，开始等self._read_waiters，
                waiter = asyncio.get_running_loop().create_future()
                self._read_waiters.append(waiter)
                try:
                    await waiter
                finally:
                    self._read_waiters.remove(waiter)
                return await self.acquire(
                    mode
                )  # 终于写锁完成了，再次acquire抢读锁 fixme 不可能抢到递归超出限制都没抢到锁吧？太晦气了
            else:
                raise NotAvailable
        elif mode == "w":
            token_candidate = uuid.uuid4().hex
            if token := await self._lockwrite_script(
                [self.namespace], [token_candidate]
            ):  # 可以立刻非阻塞获取写锁 str, bytes
                self._local_writetoken = ensure_str(token)  # 时间戳
                return token  # type: ignore
            if self.blocking:  # 不能立刻获取到写锁，阻塞模式(默认)，开始等self._write_waiters，
                token: str = uuid.uuid4().hex  # type: ignore
                # 这下只能等了
                waiter = asyncio.get_running_loop().create_future()
                self._write_waiters[token] = waiter # todo try?
                await self.client.rpush(self.write_waiter_key, token)  # type: ignore
                try:
                    await waiter  # 一旦取消，则redis中writewaiter里面还是有token，但是本地的write_waiter已经del了 ，因此需要删除等待写锁队列里面的token
                except asyncio.CancelledError:
                    # 如果在这期间正好写锁轮了一下，write_waiter_key又上位了，lrem不到了，那就糟糕了
                    # 写一个cancellockwrite.lua，KEYS = [namespace, 要取消的token]，先lrem，没删除到就是这期间写锁轮了一下，上位了
                    # 可惜太晚了，还是必须要删掉，就如同unlockwrite.lua做的那样释放了先，致敬传奇耐取消王
                    # shield不可取，其使得真正的task在后台执行，而外部caller就返回了，这里就是要让caller卡在这，状态不变回来不能返回，因此不能shield起来当缩头乌龟
                    err = None
                    while True:
                        try:
                            await self._cancellockwrite_script([self.namespace, token], [self.notify_channel])
                            break
                        except asyncio.CancelledError as e:
                            err = e
                    if err is not None:
                        try:
                            raise err
                        finally:
                            err = None
                    # await self.client.lrem(
                    #     self.write_waiter_key, 1, token
                    # )  # type: ignore
                    raise
                finally:
                    del self._write_waiters[token]
                self._local_writetoken = token
                return token
            else:
                raise NotAvailable
        else:
            raise ValueError("mode must be 'r' or 'w'")

    async def release(self, mode: Literal["r", "w"] = "r"):
        if mode == "r":
            try:
                token = self._local_readtokens.pop()
            except IndexError:  # 空list？
                raise ValueError("can not release more than acquire")
            try:
                if not await self._unlockread_script(
                    [self.namespace, token], [self.notify_channel]
                ):  # 什么都没srem出来，本地token有问题还是云端释放了？
                    raise ValueError(
                        "No lock is released. Is it released by a timeout checker?"
                    )
            except asyncio.CancelledError:
                self._local_readtokens.append(token)  # 别在这取消啊
                raise
        elif mode == "w":
            if self._local_writetoken is None:
                raise ValueError("can not release write lock without acquire it")
            if not await self._unlockwrite_script(
                [self.namespace, self._local_writetoken], [self.notify_channel]
            ):  # 如果加入超时机制的话 这里需要检查远程的write_key与self._local_writetoken是否对的上
                # 如果对不上就是那个已经被超时检查器给释放了 读token扼要检查 没在set里面也是被超时检查器给释放了
                raise ValueError(
                    "No lock is released. Is it released by a timeout checker?"
                )
            self._local_writetoken = None
        else:
            raise ValueError("mode must be 'r' or 'w'")

    async def has_token(self, mode: Literal["r", "w"] = "r") -> bool:
        """如果当前lock存在对应的token返回True"""
        if mode == "r":
            for token in self._local_readtokens:
                if await self.client.sismember(self.read_key, token):  # type: ignore
                    return True
            else:
                return False
        elif mode == "w":
            if self._local_writetoken and self._local_writetoken == ensure_str(
                await self.client.get(self.write_key)
            ):
                return True
            else:
                return False

    async def get_state(self) -> int:
        return await self._get_state_script([self.namespace])

    async def locked(self, mode: Literal["r", "w"] = "r") -> bool:
        """如果锁不能立刻获取返回True"""
        current_state = await self._get_state_script([self.namespace])
        if mode == "r":
            if current_state in (2, 3):
                return True
            else:
                return False
        elif mode == "w":
            if current_state in (1, 2, 3):
                return True
            else:
                return False
        # if mode == "r":  # 读锁能不能立刻获取取决于写锁
        #     if await self.client.exists(self.write_key) or await self.client.llen(self.write_waiter_key) > 0:
        #         return False
        #     else:
        #         return True
        # elif mode == "w":
        #     if not await self.client.exists(self.write_key) and (await self.client.scard(self.read_key)) == 0:
        #         return True
        #     else:
        #         return False

    @property
    async def current_time(self) -> str:
        # if self.is_use_local_time:
        #     return time.time()
        return ".".join(map(str, await self.client.time()))

    def get_namespaced_key(self, suffix):
        return "{0}:{1}".format(self.namespace, suffix)

    async def _listen_events(self):
        """
        监听redis中的key变动 从而知道什么时候可以获取锁
        内置重连机制，断线后自动以指数退避策略重连

        订阅两个channel:
        1. keyspace channel: 监听write_key的del事件，用于唤醒读锁等待者
        2. notify_channel: 接收Lua脚本PUBLISH的帮轮上位token，用于唤醒写锁等待者
           (相比旧方案先收set事件再GET write_key，消除了竞态窗口)
        :return:
        """
        retries = 0
        delay = self._reconnect_base_delay
        keyspace_channel = f"__keyspace@{self._get_db()}__:{self.write_key}"
        while True:
            pubsub = None
            try:
                pubsub = self.client.pubsub()
                await pubsub.subscribe(keyspace_channel, self.notify_channel)
                # 连接成功，重置重试计数和延迟
                retries = 0
                delay = self._reconnect_base_delay
                self._logger.debug(
                    "pubsub subscribed to %s and %s",
                    keyspace_channel,
                    self.notify_channel,
                )
                async for event in pubsub.listen():
                    if ensure_str(event["type"]) != "message":
                        continue
                    ch = ensure_str(event["channel"])
                    data = ensure_str(event["data"])

                    if ch == keyspace_channel and data == "del":
                        # 写锁被删除了，现在可以读了，唤醒所有读锁等待者
                        for waiter in self._read_waiters:
                            if not waiter.done():
                                waiter.set_result(None)

                    elif ch == self.notify_channel:
                        # 收到帮轮上位的写锁token，直接从消息内容获取，无需GET
                        token = data
                        if token in self._write_waiters:
                            waiter = self._write_waiters[token]
                            if not waiter.done():
                                waiter.set_result(None)
            except asyncio.CancelledError:
                # task被取消，正常退出，不重连
                raise
            except (ConnectionError, TimeoutError, RedisError, OSError) as e:
                retries += 1
                if (
                    self._reconnect_max_retries is not None
                    and retries > self._reconnect_max_retries
                ):
                    self._logger.error(
                        "pubsub reconnect failed after %d retries, giving up: %s",
                        retries - 1,
                        e,
                    )
                    return
                self._logger.warning(
                    "pubsub connection lost (attempt %d), reconnecting in %.1fs: %s",
                    retries,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._reconnect_max_delay)
            except Exception as e:
                self._logger.exception(
                    "unexpected error in _listen_events, reconnecting: %s", e
                )
                retries += 1
                if (
                    self._reconnect_max_retries is not None
                    and retries > self._reconnect_max_retries
                ):
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._reconnect_max_delay)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.aclose()
                    except Exception:
                        pass
