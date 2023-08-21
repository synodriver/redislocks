-- 老写锁释放的时候带新写锁进来，或者读锁没有的时候带新写锁尽量
-- numkey: 1
-- namespace
local namespace = KEYS[1]

local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

if redis.call("LLEN", write_waiter_key) > 0 then -- 还有人在等写锁，帮他轮
    local write_token = redis.call("LPOP", write_waiter_key)
    redis.call("SET", write_key, write_token)
else --  后面没有人在等写锁了，那就删除写锁
    redis.call("DEL", write_key)
end
