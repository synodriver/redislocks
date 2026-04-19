# -*- coding: utf-8 -*-
"""
Copyright (c) 2008-2026 synodriver <diguohuangjiajinweijun@gmail.com>
"""

import asyncio
import pickle
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Callable, NoReturn, ParamSpec, TypeVar

if TYPE_CHECKING:
    from redis.asyncio import Redis
else:
    Redis = TypeVar("Redis")

P = ParamSpec("P")
R = TypeVar("R")

# Lua: delete lock only if value matches (ownership check)
_UNLOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
end
return 0
"""

# Lua: set result + publish + conditional unlock, atomically
_FINISH_SCRIPT = """
redis.call("SET", KEYS[2], ARGV[2], "EX", ARGV[3])
redis.call("PUBLISH", KEYS[3], "1")
if redis.call("GET", KEYS[1]) == ARGV[1] then
    redis.call("DEL", KEYS[1])
end
return 1
"""


class SingleFlight:
    """
    跨进程/跨机器的 singleflight，语义与本地版完全一致：
    同一 key 只有一个 executor 执行 fn，其余 waiter 共享结果。

    机制
    ----
    1. SET NX        —— 分布式锁，保证只有一个 executor
    2. Result Key    —— executor 把结果（或异常）序列化后写入
    3. Pub/Sub       —— executor 完成后 push 一条消息，waiter 在 channel 上
                       阻塞等待（TCP 长连接，不是轮询）

    与本地版对应关系
    ----------------
    ┌─ 本地版 ────────────┐  ┌─ 分布式版 ──────────────┐
    │ self._cached dict   │  │ Redis SET NX (lock key) │
    │ Caller._done Event  │  │ Redis Pub/Sub channel   │
    │ Caller._val/_err    │  │ Redis result key (TTL)  │
    │ del cached[key]     │  │ DEL lock key / TTL 过期 │
    └─────────────────────┘  └─────────────────────────┘
    """

    _OK = 0
    _ERR = 1

    def __init__(
        self,
        redis_client: Redis,
        namespace: str = "sfg",
        ttl: int = 300,
        wait_timeout: float | None = None,
    ) -> None:
        """

        :param redis_client: 异步 Redis 客户端
        :param namespace: key 前缀，用于隔离不同 Group
        :param ttl: lock / result 的存活秒数，防止泄漏
        :param wait_timeout: waiter等待结果的时间
        """
        self.client = redis_client
        self._ns = namespace
        self._ttl = ttl
        self.wait_timeout = wait_timeout

        # 注册 Lua 脚本，Redis 会缓存 SHA1，后续用 EVALSHA 调用
        self._finish_script = self.client.register_script(_FINISH_SCRIPT)
        self._unlock_script = self.client.register_script(_UNLOCK_SCRIPT)

    # ── key 生成 ─────────────────────────────────────────────────

    def _lock_key(self, key: str) -> str:
        """生成分布式锁的 Redis key。

        :param key: 业务去重键
        :return: 完整的 Redis key
        """
        return f"{self._ns}:l:{key}"

    def _result_key(self, key: str) -> str:
        """生成结果存储的 Redis key。

        :param key: 业务去重键
        :return: 完整的 Redis key
        """
        return f"{self._ns}:r:{key}"

    def _channel(self, key: str) -> str:
        """生成 Pub/Sub 通知频道名。

        :param key: 业务去重键
        :return: 完整的频道名
        """
        return f"{self._ns}:c:{key}"

    # ── 公开接口 ─────────────────────────────────────────────────

    async def do(
        self,
        key: str,
        fn: Callable[P, Awaitable[R | NoReturn]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R | NoReturn:
        """对同一 key 去重的并发调用。

        如果当前没有相同 key 的执行中请求，则获取锁成为 executor 执行 ``fn``；
        否则作为 waiter 等待 executor 的结果。

        :param key: 去重键
        :param fn: 异步函数（真正执行的操作）
        :param args: 传给 ``fn`` 的位置参数
        :param kwargs: 传给 ``fn`` 的关键字参数
        :return: ``fn`` 的返回值
        :raises asyncio.TimeoutError: waiter 等待超时
        :raises BaseException: ``fn`` 抛出的异常会传播给 executor 和所有 waiter
        """
        # ① 尝试拿锁 —— 成功就是 executor
        gen = uuid.uuid4().hex
        acquired = await self.client.set(
            self._lock_key(key),
            gen,
            nx=True,
            ex=self._ttl,
        )

        if acquired:
            return await self._exec(key, gen, fn, args, kwargs)

        # ② 没拿到锁 —— 当 waiter
        return await self._wait(key, self.wait_timeout)

    # ── executor 路径 ───────────────────────────────────────────

    async def _exec(self, key: str, gen: str, fn, args, kwargs):  # type: ignore[no-untyped-def]
        """拿到锁后执行 ``fn``，将结果存入 Redis 并通过 Pub/Sub 通知 waiter。

        :param key: 业务去重键
        :param gen: 本次执行的唯一标识（UUID），用于 ownership 校验
        :param fn: 要执行的异步函数
        :param args: 传给 ``fn`` 的位置参数
        :param kwargs: 传给 ``fn`` 的关键字参数
        :return: ``fn`` 的返回值
        :raises BaseException: ``fn`` 抛出的异常会原样抛出
        """
        envelope = self._pack_error(RuntimeError("executor: unexpected error"), gen)
        try:
            val = await fn(*args, **kwargs)
            envelope = self._pack_ok(val, gen)
            return val
        except BaseException as exc:
            envelope = self._pack_error(exc, gen)
            raise
        finally:
            try:
                # 存结果 + 通知 + 安全释放锁（Lua 保证原子性 + ownership 校验）
                await self._finish_script(  # type: ignore[misc]
                    keys=[
                        self._lock_key(key),
                        self._result_key(key),
                        self._channel(key),
                    ],
                    args=[gen, envelope, self._ttl],
                )
            except:
                # 最差情况也要释放锁
                try:
                    await self._unlock_script(  # type: ignore[misc]
                        keys=[self._lock_key(key)],
                        args=[gen],
                    )
                except:
                    pass

    # ── waiter 路径 ─────────────────────────────────────────────

    async def _wait(self, key: str, timeout: float | None):
        """作为 waiter 等待 executor 完成并读取结果。

        先订阅 Pub/Sub 频道，然后循环检查结果是否就绪；
        收到通知后从 Redis 读取结果并返回（或抛出 executor 记录的异常）。

        :param key: 业务去重键
        :param timeout: 最长等待秒数，``None`` 表示无限等待
        :return: executor 执行 ``fn`` 的返回值
        :raises asyncio.TimeoutError: 等待超时
        :raises BaseException: executor 执行 ``fn`` 时抛出的异常
        """
        ps = self.client.pubsub()
        ch = self._channel(key)
        rk = self._result_key(key)
        lk = self._lock_key(key)

        # 计算绝对截止时间（避免每次 recv 只算单次超时）
        deadline: float | None = None
        if timeout is not None:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
        try:
            # ③ 先订阅（必须在检查 result 之前，否则会漏消息）
            await ps.subscribe(ch)

            while True:
                # ── pipeline 原子读取 lock + result ──
                # Redis 单线程保证这两条 GET 之间不会被其他命令插队
                pipe = self.client.pipeline(transaction=False)
                pipe.get(lk)  # lock key
                pipe.get(rk)  # result key
                lock_val, result_raw = await pipe.execute()

                if result_raw is not None:
                    wrapper = pickle.loads(result_raw)
                    result_gen: str = wrapper.get("g", "")

                    # lock_val is bytes from Redis, decode for comparison
                    lock_gen = (
                        lock_val.decode() if isinstance(lock_val, bytes) else lock_val
                    )

                    # 判断 result 是否属于"当前（或刚结束的）执行轮次"
                    # ┌──────────────────────────────────────────────────────┐
                    # │ lock_val=b"gen_B"  result_gen="gen_B"  → 同一轮 ✓    │
                    # │ lock_val=None      result_gen="gen_B"  → 执行完 ✓    │
                    # │ lock_val=b"gen_C"  result_gen="gen_B"  → 残留 ✗     │
                    # └──────────────────────────────────────────────────────┘
                    if lock_gen is not None and result_gen != lock_gen:
                        pass  # 上一轮残留 → 不返回，继续等
                    else:
                        # result 有效
                        if wrapper["s"] == self._OK:
                            return wrapper["d"]
                        raise wrapper["d"]

                # ── 无有效 result → 等 Pub/Sub 通知 ──
                remaining: float | None = None
                if deadline is not None:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError(
                            f"DistributedSingleFlight: wait timed out for key '{key}'"
                        )

                try:
                    await asyncio.wait_for(self._recv_one(ps), timeout=remaining)
                except asyncio.TimeoutError:
                    raise asyncio.TimeoutError(
                        f"DistributedSingleFlight: wait timed out for key='{key}'"
                    )

        finally:
            try:
                await ps.unsubscribe(ch)
                await ps.aclose()
            except:
                pass

    @staticmethod
    async def _recv_one(ps):
        """从 Pub/Sub 流中读取下一条 ``message`` 类型的消息。

        :param ps: Redis Pub/Sub 对象
        """
        async for msg in ps.listen():
            if msg["type"] == "message":
                return

    # ── 序列化 / 反序列化 ───────────────────────────────────────

    @classmethod
    def _pack_ok(cls, val: Any, gen: str) -> bytes:
        """将成功结果序列化为 bytes，用于存入 Redis。

        :param val: ``fn`` 的返回值
        :param gen: 本次执行的唯一标识
        :return: pickle 序列化后的字节串
        """
        return pickle.dumps(
            {"s": cls._OK, "d": val, "g": gen}, protocol=pickle.HIGHEST_PROTOCOL
        )

    @classmethod
    def _pack_error(cls, exc: BaseException, gen: str) -> bytes:
        """将异常序列化为 bytes，用于存入 Redis。

        :param exc: ``fn`` 抛出的异常
        :param gen: 本次执行的唯一标识
        :return: pickle 序列化后的字节串
        """
        return pickle.dumps(
            {"s": cls._ERR, "d": exc, "g": gen}, protocol=pickle.HIGHEST_PROTOCOL
        )

    @staticmethod
    def _unpack(raw: bytes):
        """反序列化 Redis 中存储的结果。

        :param raw: pickle 序列化后的字节串
        :return: 成功时返回原始值
        :raises BaseException: 如果存储的是异常则直接抛出
        """
        wrapper = pickle.loads(raw)  # type: ignore[arg-type]
        if wrapper["s"] == SingleFlight._OK:
            return wrapper["d"]
        # waiter 也要看到 executor 抛出的原始异常
        raise wrapper["d"]
