# Deployment Information

## Public URL

https://day14-agent-production-57725e43.up.railway.app

## Platform

**Railway** (gói dùng thử), deploy agent Part 6 trong `06-lab-complete/` bằng Dockerfile.

| Service | Loại | Ghi chú |
|---|---|---|
| `day14-agent` | Docker (build từ `06-lab-complete/Dockerfile`, cấu hình `railway.toml`) | Public domain ở trên, health check `/health` |
| `Redis` | Railway database `redis:8.2`, có volume | Chỉ truy cập qua private network, không mở ra Internet |

## Cách deploy

```powershell
cd 06-lab-complete
railway login
railway init                          # tạo project day14-agent
railway add --database redis          # thêm Redis
railway add --service day14-agent     # service riêng cho agent (Empty Service)
railway service day14-agent           # link CLI vào service agent, không phải Redis
railway variables --set "AGENT_API_KEY=<secret>" --set 'REDIS_URL=${{Redis.REDIS_URL}}' `
  --set "ENVIRONMENT=production" --set "RATE_LIMIT_PER_MINUTE=10" `
  --set "DAILY_BUDGET_USD=1.0" --set "MONTHLY_BUDGET_USD=10.0"
railway up                            # build Dockerfile + deploy
railway domain                        # tạo public URL
```

## Test Commands

Đặt `BASE` là URL ở trên và `AGENT_API_KEY` là key đã đặt trên Railway (không ghi key vào repo):

```bash
BASE=https://day14-agent-production-57725e43.up.railway.app

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả đã chạy (2026-11-26):

```text
/health            → 200   {"status":"ok","uptime_seconds":33.5,"environment":"production",...}
/ready             → 200   {"ready":true}        ← agent kết nối được Redis qua REDIS_URL
/ask (không key)   → 401
/ask (có key)      → 200
```

### Health check

```bash
curl https://day14-agent-production-57725e43.up.railway.app/health
# {"status":"ok","uptime_seconds":...,"environment":"production","timestamp":"..."}
```

### API test (with authentication)

```bash
curl -X POST https://day14-agent-production-57725e43.up.railway.app/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'
```

## Environment Variables Set

Đặt trên Railway (tab **Variables** của service `day14-agent`), **không** ghi trong code hay repo:

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `AGENT_API_KEY` | *(secret, sinh ngẫu nhiên bằng `secrets.token_urlsafe(32)`)* | Header `X-API-Key` phải trùng |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Reference variable: Railway tự thay bằng URL private của service Redis |
| `ENVIRONMENT` | `production` | Tắt `/docs` |
| `RATE_LIMIT_PER_MINUTE` | `10` | Mỗi API key |
| `DAILY_BUDGET_USD` | `1.0` | Cost guard theo ngày |
| `MONTHLY_BUDGET_USD` | `10.0` | Cost guard theo tháng |
| `PORT` | *(Railway tự cấp)* | Dockerfile `CMD` lắng nghe `${PORT:-8000}` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
