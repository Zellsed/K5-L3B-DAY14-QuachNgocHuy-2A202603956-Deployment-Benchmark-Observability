"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

  • Mỗi biến có giá trị mặc định an toàn để chạy local.
  • Không có secret nào nằm trong code: AGENT_API_KEY mặc định là chuỗi rỗng,
    khi đó /ask từ chối mọi request (fail closed) thay vì mở cửa.
  • Danh sách biến đầy đủ: xem .env.example.
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Chạy local: nạp file .env nếu có. load_dotenv KHÔNG ghi đè biến đã có trong môi trường,
# nên trên Docker / Railway / Render giá trị do platform đặt luôn được ưu tiên.
load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    port: int = field(default_factory=lambda: _int("PORT", 8000))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    # Secret — chỉ lấy từ môi trường, không có giá trị mặc định
    agent_api_key: str = field(default_factory=lambda: os.getenv("AGENT_API_KEY", ""))

    # Redis dùng chung cho rate limit + cost guard
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    # Rate limit: số request tối đa mỗi API key trong 60 giây
    rate_limit_per_minute: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_MINUTE", 10))

    # Cost guard: ngân sách LLM (USD) theo ngày và theo tháng
    daily_budget_usd: float = field(default_factory=lambda: _float("DAILY_BUDGET_USD", 1.0))
    monthly_budget_usd: float = field(default_factory=lambda: _float("MONTHLY_BUDGET_USD", 10.0))


settings = Settings()
