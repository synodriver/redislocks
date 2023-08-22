-- 检查读锁或者写锁能否立刻获取
-- numkey: 1
-- key: namespace arg: mode
local namespace = KEYS[1]
local mode = ARGV[1] -- string, r or w
local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

if mode == "r" then
    if redis.call("EXISTS", write_key) == 1 or redis.call("LLEN", write_waiter_key) > 0 then
        -- 存在写锁或者有人在等写锁
        return 0
    else
        return 1
    end
elseif mode == "w" then
    if redis.call("EXISTS", write_key) == 0 and redis.call("SCARD", read_key) == 0 then
        -- 不存在写锁 也不存在 读锁 可以直接设置写锁
        return 1
    else
        return 0
    end
end
