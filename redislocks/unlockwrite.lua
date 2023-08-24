-- 老写锁释放的时候带新写锁进来，或者读锁没有的时候带新写锁尽量
-- numkey: 1
-- namespace
local namespace = KEYS[1]

local read_key = namespace .. ":READ"
local write_key = namespace .. ":WRITE"
local write_waiter_key = namespace .. ":WRITEWAITER"

local read_lock_exists = redis.call("SCARD", read_key) > 0
local write_lock_exists = redis.call("EXISTS", write_key) == 1
local write_waiter_exists = redis.call("LLEN", write_waiter_key) > 0

local function get_state()
    if not read_lock_exists and not write_lock_exists then -- 没有读锁也没有写锁，是空的
        return 0
    elseif read_lock_exists and not write_lock_exists and not write_waiter_exists then -- 存在读锁，不存在写锁和写锁等待，读ing
        return 1
    elseif not read_lock_exists and write_lock_exists then -- 不存在读锁，存在写锁，写ing
        return 2
    elseif read_lock_exists and not write_lock_exists and write_waiter_exists then -- 存在读锁，不存在写锁，不过有等待等待队列有东西
        return 3
    end
end

local current_state = get_state()

if current_state == 2 then
    if write_waiter_exists then -- 还有人在等写锁，帮他轮
        local write_token = redis.call("LPOP", write_waiter_key)
        redis.call("SET", write_key, write_token)
    else --  后面没有人在等写锁了，那就删除写锁
        redis.call("DEL", write_key)
    end
    return 1
else
    return 0
end