"""
Rate limiting (tiêu chí 7) — sliding window bằng Redis sorted set.

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây gần nhất
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State nằm trong Redis: mọi worker / instance dùng chung, restart agent vẫn còn

Cách hoạt động (key Redis: ratelimit:<hash của API key>):
  ZSET chứa mỗi request là một member, score = timestamp.
  1. ZREMRANGEBYSCORE  xoá các request cũ hơn 60 giây
  2. ZADD              ghi request hiện tại
  3. ZCARD             đếm số request trong cửa sổ (đã gồm request hiện tại)
  4. EXPIRE            key tự xoá sau 60 giây không dùng
  Bốn lệnh chạy trong một MULTI/EXEC (pipeline transaction) nên các worker không chen ngang nhau.
  Nếu đếm > giới hạn → xoá lại request vừa ghi (request bị từ chối không chiếm chỗ) và trả 429.
"""
import hashlib
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _redis_key(api_key: str) -> str:
    # Không lưu API key dạng gốc trong Redis, chỉ lưu hash
    return "ratelimit:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> None:
    """Raise HTTP 429 nếu API key `key` đã dùng hết lượt trong 60 giây gần nhất."""
    limit = settings.rate_limit_per_minute
    redis_key = _redis_key(key)
    now = time.time()
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"   # unique, kể cả 2 request cùng thời điểm

    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(redis_key, 0, now - WINDOW_SECONDS)
    pipe.zadd(redis_key, {member: now})
    pipe.zcard(redis_key)
    pipe.expire(redis_key, WINDOW_SECONDS)
    _, _, count, _ = pipe.execute()

    if count > limit:
        r.zrem(redis_key, member)
        # Request cũ nhất trong cửa sổ hết hạn lúc nào thì lại có chỗ
        oldest = r.zrange(redis_key, 0, 0, withscores=True)
        retry_after = int(oldest[0][1] + WINDOW_SECONDS - now) + 1 if oldest else WINDOW_SECONDS
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": limit,
                "window_seconds": WINDOW_SECONDS,
                "retry_after_seconds": retry_after,
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )
