"""
Cost guard (tiêu chí 8) — chặn khi tổng chi phí LLM vượt ngân sách.

  • Chi phí mỗi request tính theo token, cộng dồn vào Redis
  • Tổng chi phí trong ngày ≥ DAILY_BUDGET_USD hoặc trong tháng ≥ MONTHLY_BUDGET_USD → HTTP 402

Key Redis (tổng chi phí của cả agent, dùng chung mọi worker / instance):
  cost:daily:YYYY-MM-DD   hết hạn sau 2 ngày   → sang ngày mới là key mới, tự reset
  cost:monthly:YYYY-MM    hết hạn sau 32 ngày  → sang tháng mới là key mới, tự reset
Ngày giờ theo UTC để mọi instance (dù đặt ở múi giờ nào) dùng chung một key.
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006


def _keys() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"cost:daily:{now:%Y-%m-%d}", f"cost:monthly:{now:%Y-%m}"


def estimate_tokens(text: str) -> int:
    """Ước lượng thô: ~4 ký tự / token (mock LLM không trả số token thật)."""
    return max(1, len(text) // 4)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    daily_key, monthly_key = _keys()
    daily_spent, monthly_spent = (float(v or 0) for v in r.mget(daily_key, monthly_key))

    if daily_spent >= settings.daily_budget_usd:
        period, spent, budget = "daily", daily_spent, settings.daily_budget_usd
    elif monthly_spent >= settings.monthly_budget_usd:
        period, spent, budget = "monthly", monthly_spent, settings.monthly_budget_usd
    else:
        return

    raise HTTPException(
        status_code=402,
        detail={
            "error": f"{period.capitalize()} budget exceeded",
            "spent_usd": round(spent, 6),
            "budget_usd": budget,
        },
    )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request (USD)."""
    cost = input_tokens / 1000 * PRICE_INPUT_PER_1K + output_tokens / 1000 * PRICE_OUTPUT_PER_1K
    daily_key, monthly_key = _keys()
    pipe = r.pipeline(transaction=True)
    pipe.incrbyfloat(daily_key, cost)       # cộng nguyên tử, an toàn khi nhiều worker
    pipe.expire(daily_key, 2 * 24 * 3600)
    pipe.incrbyfloat(monthly_key, cost)
    pipe.expire(monthly_key, 32 * 24 * 3600)
    pipe.execute()
    return cost
