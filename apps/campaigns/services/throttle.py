"""Redis fixed-window rate limiter for per-campaign calls-per-minute budget.

A Lua script atomically reserves up to `want` tokens within the current
1-minute window, returning how many were granted. Atomicity matters because
the beat tick and event-driven kicks can run concurrently.
"""
from __future__ import annotations

import time

import redis
from django.conf import settings

# Reserve up to `want` tokens from a fixed window without exceeding `limit`.
_RESERVE_LUA = """
local current = tonumber(redis.call('get', KEYS[1]) or '0')
local remaining = tonumber(ARGV[1]) - current
if remaining <= 0 then return 0 end
local grant = math.min(remaining, tonumber(ARGV[2]))
redis.call('incrby', KEYS[1], grant)
redis.call('expire', KEYS[1], tonumber(ARGV[3]))
return grant
"""

_client = None
_script = None


def _get_client() -> redis.Redis:
    global _client, _script
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL)
        _script = _client.register_script(_RESERVE_LUA)
    return _client


def reserve_call_budget(campaign_id: int, calls_per_minute: int, want: int) -> int:
    """Reserve up to `want` call slots for this minute. Returns granted count."""
    if want <= 0:
        return 0
    if calls_per_minute <= 0:  # 0 = unthrottled
        return want
    _get_client()
    window = int(time.time()) // 60
    key = f"campaign:{campaign_id}:rate:{window}"
    return int(_script(keys=[key], args=[calls_per_minute, want, 120]))
