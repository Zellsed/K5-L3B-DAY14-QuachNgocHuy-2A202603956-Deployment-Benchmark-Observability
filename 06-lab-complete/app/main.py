"""
Production AI Agent — Part 6 Final Project

Chạy:  uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
Chấm:  python check_production_ready.py

Thứ tự xử lý POST /ask:
  verify_api_key (401) → check_rate_limit (429) → check_budget (402) → LLM → record_cost
"""
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10): mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
        }
        if isinstance(record.msg, dict):        # logger.info({"event": ..., ...})
            entry.update(record.msg)
        else:
            entry["msg"] = record.getMessage()
        if record.exc_info:                     # traceback nằm trong 1 field, vẫn là 1 dòng JSON
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


# Logger riêng của app (không đụng vào logger của uvicorn), ghi ra stdout để platform thu log
logger = logging.getLogger("agent")
logger.setLevel(settings.log_level)
logger.propagate = False
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(JsonFormatter())
    logger.addHandler(_handler)


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, {"event": event, **fields})


# ─────────────────────────────────────────────────────────
# Lifecycle + graceful shutdown (tiêu chí 11)
# ─────────────────────────────────────────────────────────
START_TIME = time.time()
_state = {"shutting_down": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: KHÔNG ping Redis ở đây — app phải khởi động được khi Redis chưa có
    log_event("startup", environment=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              monthly_budget_usd=settings.monthly_budget_usd)
    if not settings.agent_api_key:
        log_event("config_warning", logging.WARNING,
                  detail="AGENT_API_KEY is not set — every /ask request will be rejected with 401")
    yield
    # Shutdown: uvicorn tự bắt SIGTERM, ngừng nhận kết nối mới, chờ request đang chạy xong
    # rồi mới chạy tới đây. App không tự đăng ký handler SIGTERM để không chặn mất handler của uvicorn.
    _state["shutting_down"] = True
    log_event("shutdown", detail="in-flight requests finished, closing Redis connection")
    try:
        r.close()
    except Exception:
        pass


app = FastAPI(
    title="Production AI Agent",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None,
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    """Ghi 1 dòng log JSON cho mỗi request: path, status, thời gian xử lý."""
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        log_event("request", method=request.method, path=request.url.path, status=status,
                  ms=round((time.perf_counter() - started) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Xử lý lỗi: trả mã lỗi rõ ràng, không lộ stack trace cho client
# ─────────────────────────────────────────────────────────
@app.exception_handler(redis.RedisError)
async def redis_error_handler(request: Request, exc: redis.RedisError):
    log_event("redis_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable, please retry later."},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.error({"event": "unhandled_error", "path": request.url.path}, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


@app.get("/")
def root():
    return {"app": "Production AI Agent", "environment": settings.environment,
            "endpoints": ["GET /health", "GET /ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness (tiêu chí 4): process còn chạy là 200, KHÔNG phụ thuộc Redis."""
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ready")
def ready():
    """Readiness (tiêu chí 5): 200 khi nhận được traffic; 503 khi mất Redis hoặc đang tắt."""
    if _state["shutting_down"]:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "shutting down"})
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"ready": False, "reason": "redis unavailable"})
    return {"ready": True}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)                 # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                            # 402 khi hết budget — kiểm tra TRƯỚC khi gọi LLM

    started = time.perf_counter()
    answer = llm_ask(body.question)
    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)

    log_event("llm_call", user_id=body.user_id, input_tokens=input_tokens,
              output_tokens=output_tokens, cost_usd=round(cost, 8),
              llm_ms=round((time.perf_counter() - started) * 1000, 1))
    return {"question": body.question, "answer": answer}
