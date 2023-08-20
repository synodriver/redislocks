-- 看看是否可以唤醒blocking的读锁
-- numkey: 1
-- namespace
local namespace = KEYS[1]

local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

if redis.call("EXISTS", write_key) == 1 or redis.call("LLEN", write_waiter_key) > 0 then
    -- 存在写锁或者有人在等写锁
    return 0 -- 不能唤醒waiters
else
    return 1 -- 时辰已到，唤醒全部future
end
