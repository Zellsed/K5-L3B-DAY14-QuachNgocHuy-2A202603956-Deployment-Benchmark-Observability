"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • REDIS_URL đọc từ config.
  • redis.from_url() KHÔNG mở kết nối ngay; kết nối chỉ được tạo ở lệnh đầu tiên.
    Nhờ vậy app vẫn khởi động được khi Redis chưa sẵn sàng (/health vẫn 200).
  • Timeout ngắn để /ready và /ask không bị treo lâu khi Redis chết.
"""
import redis

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    """Dùng cho GET /ready: ping được Redis → True, lỗi kết nối / timeout → False."""
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
