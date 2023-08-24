local check_target = KEYS[1];
local set_target = ARGV[1];

local sec, micro_sec = redis.call('TIME');
local redis_time = tostring((sec+2) * 1000000 + micro_sec);

local ret = redis.call('GET', check_target);
if not ret or ret <= tostring(sec * 1000000 + micro_sec) then
    redis.call('SET', set_target, redis_time, 'PXAT='..redis_time);
    return redis_time;
end

return '0';