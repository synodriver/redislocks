local check_write_target = KEYS[1];
local check_write_target_preservation = KEYS[2];
local lock_time = ARGV[1];

redis.call('DEL', check_write_target)

local preservation_time = redis.call('GET', check_write_target_preservation);
if preservation_time and preservation_time == lock_time then
    redis.call('DEL', check_write_target_preservation)
end