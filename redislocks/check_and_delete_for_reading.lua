local delete_target = KEYS[1];
local check_token = ARGV[1];

local ret = redis.call('GET', delete_target);
if not ret then
    return '1';
elseif ret > check_token then
    return '0';
end

redis.call('DEL', delete_target);
return '1';

