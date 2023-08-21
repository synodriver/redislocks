-- 释放读锁
-- numkey: 2
-- namespace token
local namespace = KEYS[1]
local token = KEYS[2] -- read token
--local blocking = ARGV[1] -- string
local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

local ret = redis.call("SREM", read_key, token)
if redis.call("SCARD", read_key) == 0 and redis.call("LLEN", write_waiter_key) > 0 and redis.call("EXISTS", write_key) == 0 then
    --读锁空了，有人在等写锁，且写锁现在还不存在， 那去掉读锁的过程就帮他们轮一下写锁
    local write_token = redis.call("LPOP", write_waiter_key)
    redis.call("SET", write_key, write_token)
end

return ret