-- 加读锁
-- numkey: 1
-- namespace
local namespace = KEYS[1]
--local blocking = ARGV[1] -- string
local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

if redis.call("EXISTS", write_key) == 1 or redis.call("LLEN", write_waiter_key) > 0 then
    -- 存在写锁或者有人在等写锁
    return 0 -- 直接加锁失败，此时，如果是阻塞模式，开始监听keyspace
else
    local time = redis.call("TIME")
    local timestring = time[1] ..".".. time[2] -- string
    redis.call("SADD", read_key, timestring)
    return timestring -- 成功就返回时间戳
end
