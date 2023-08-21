-- 加写锁 直接设置不检查
-- numkey: 1
-- namespace
local namespace = KEYS[1]
--local blocking = ARGV[1] -- string
local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

if redis.call("EXISTS", write_key) == 0 and redis.call("SCARD", read_key) == 0 then
    -- 不存在写锁 也不存在 读锁 可以直接设置写锁
    local time = redis.call("TIME")
    local timestring = time[1] ..".".. time[2] -- string
    redis.call("SET", write_key, timestring)
    return timestring -- 获取写锁成功，返回时间戳
else
    return 0
end
