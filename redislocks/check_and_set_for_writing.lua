local check_read_target = KEYS[1];
local check_write_target = KEYS[2];
local check_write_target_preservation = KEYS[3];

local sec, micro_sec = redis.call('TIME');
local redis_time = tostring((sec+2) * 1000000 + micro_sec);
local preservation_time = redis.call('GET', check_write_target_preservation);

if not preservation_time or preservation_time < redis_time then
    redis.call('SET', check_write_target_preservation, redis_time, 'PXAT='..redis_time);
end

local read_lock_expiring_time_in_microsecond = redis.call('GET', check_read_target);
if not read_lock_expiring_time_in_microsecond then
    if redis.call('SET', check_write_target, redis_time, 'PXAT='..redis_time, 'NX') then;
        return redis_time;
    end
    return '1'
end

return '0';