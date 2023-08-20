-- 释放读锁
-- numkey: 1
-- namespace
local namespace = KEYS[1]
--local blocking = ARGV[1] -- string
local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

return redis.call("LPOP", read_key, 1)