-- 看看是否可以立刻获取写锁
-- numkey: 1
-- namespace
local namespace = KEYS[1]

local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

if redis.call("EXISTS", write_key) == 1 or redis.call("SCARD", read_key) > 0 then
    -- 存在写锁或者存在读锁
    return 0 -- 不能立刻获取写锁
else
    return 1 -- 可以立刻获取写锁
end
