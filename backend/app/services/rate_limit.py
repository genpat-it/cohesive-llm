"""Shared slowapi limiter.

Uses the first IP in X-Forwarded-For when present, so the limit applies to the
real client IP even when the request comes through Caddy + a corporate reverse
proxy. Falls back to the direct peer address otherwise.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address


def real_ip_key(request):
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # X-Forwarded-For: client, proxy1, proxy2 → take the first hop
        return xff.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=real_ip_key, default_limits=[])
