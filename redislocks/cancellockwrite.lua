-- 取消加写锁
-- numkey: 2
-- namespace， token
local namespace = KEYS[1]
local token = KEYS[2]

local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

local write_waiter_exists = redis.call("LLEN", write_waiter_key) > 0

if redis.call("LREM", write_waiter_key, 1, token) == 0 then
    if redis.call("GET", write_key) == token then
        if write_waiter_exists then -- 还有人在等写锁，帮他轮
            local write_token = redis.call("LPOP", write_waiter_key)
            redis.call("SET", write_key, write_token)
        else --  后面没有人在等写锁了，那就删除写锁
            redis.call("DEL", write_key)
        end
    end
end