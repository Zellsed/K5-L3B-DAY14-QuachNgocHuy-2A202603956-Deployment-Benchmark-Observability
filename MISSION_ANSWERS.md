# Day 14 Lab — Mission Answers

> Bài cá nhân. Mọi output, số MB, mã HTTP dán vào đây phải lấy từ lệnh tự chạy (xem [RULES.md](RULES.md)).
> Che token / API key trước khi dán.

---

## Part 1: Localhost vs Production (CP1)

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng code (develop/app.py) | Vì sao nguy hiểm | Bản production sửa thế nào |
|---|---|---|---|---|
| 1 | Secret hardcode trong code | L17 `OPENAI_API_KEY = "sk-hardcoded-..."`, L18 `DATABASE_URL = "postgresql://admin:password123@..."` | Push lên GitHub là lộ key và mật khẩu DB, kể cả khi xoá sau đó vì vẫn nằm trong lịch sử git. Muốn đổi key phải sửa code và deploy lại | `config.py` L27, L32 đọc `OPENAI_API_KEY`, `AGENT_API_KEY` từ biến môi trường; `.env` không commit, chỉ commit `.env.example` |
| 2 | Log ra secret bằng `print()` | L33–L38, đặc biệt L34 `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | Ai đọc được log (log platform, đồng nghiệp, dịch vụ log bên thứ ba) là có key. `print()` không có level, không có timestamp, không parse được | Dùng `logging` format JSON (L28–L31); log `question_length`, `client_ip` chứ không log nội dung nhạy cảm và không log key (L111–L115) |
| 3 | Config cứng, không quản lý cấu hình | L21 `DEBUG = True`, L22 `MAX_TOKENS = 500` | Không đổi được hành vi giữa dev/staging/production nếu không sửa code; dễ quên tắt debug khi lên production | `config.py`: mọi giá trị lấy từ env có default (`DEBUG` mặc định `false`), có `validate()` fail fast khi `ENVIRONMENT=production` mà thiếu `AGENT_API_KEY` |
| 4 | Không có health check | Không có `/health`, `/ready` (comment L42–L43) | Platform/load balancer không biết app còn sống hay đã treo để restart, cũng không biết khi nào sẵn sàng nhận traffic | `/health` (liveness, L135–L149), `/ready` (readiness, trả 503 khi chưa sẵn sàng, L152–L161), thêm `/metrics` |
| 5 | Bind `localhost` và port cố định | L51 `host="localhost"`, L52 `port=8000` | Trong container, `localhost` chỉ nhận kết nối từ bên trong container nên bên ngoài không gọi được; Railway/Render cấp port qua biến `PORT`, cứng 8000 thì health check fail | `host` mặc định `0.0.0.0`, `port` đọc từ env `PORT` (`config.py` L17–L18, `app.py` L196–L197) |
| 6 | `reload=True` luôn bật | L53 | Auto-reload theo dõi file, tốn tài nguyên, có thể restart giữa chừng khi file thay đổi; chỉ dành cho dev | `reload=settings.debug`, chỉ bật khi `DEBUG=true` (L198) |
| 7 | Không xử lý shutdown | Không có lifespan hay signal handler | Khi platform gửi SIGTERM (deploy mới, scale down), request đang chạy bị cắt ngang, kết nối không được đóng | `lifespan` (L39–L67) đặt `is_ready=False` rồi chờ request dở dang; handler SIGTERM (L180–L189) |
| 8 | Không validate input, không có CORS | L31 `def ask_agent(question: str)` nhận query, không kiểm tra rỗng | Câu hỏi rỗng vẫn gọi LLM (tốn tiền); không kiểm soát origin nào được gọi API | Kiểm tra `question` rỗng thì trả 422 (L107–L108); `CORSMiddleware` chỉ cho `ALLOWED_ORIGINS` (L77–L82) |

### Exercise 1.2: Chạy bản develop

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 07:52:09 GMT
server: uvicorn
content-length: 126
content-type: application/json

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

**Nhận xét:** app chạy được và trả 200 trên máy mình, nhưng chưa production-ready. Key nằm trong code và bị in ra log mỗi request. Bind `localhost:8000` nên chạy trong container hay trên cloud sẽ không nhận được kết nối. Không có `/health` nên platform không tự restart được khi app treo, và khi tắt thì request đang chạy bị cắt ngang.

### Exercise 1.3: So sánh develop vs production

<!-- Chạy bản production: cd ../production && cp .env.example .env && pip install -r requirements.txt && python app.py
     rồi: curl -i http://localhost:8000/health -->

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 07:55:20 GMT
server: uvicorn
content-length: 130
content-type: application/json

{"status":"ok","uptime_seconds":52.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T07:55:21.128715+00:00"}
```

Bản production có `/health` trả 200 kèm uptime, version, environment. Bản develop không có endpoint này (gọi sẽ ra 404).

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong `app.py` (`DEBUG = True`, `MAX_TOKENS = 500`) | Đọc từ biến môi trường qua `config.py`, có default an toàn và `validate()` | Cùng một image/code chạy được ở dev, staging, production chỉ bằng cách đổi env (12-Factor: config in env) |
| Secrets | Viết thẳng trong code, còn bị `print` ra log | Lấy từ env (`OPENAI_API_KEY`, `AGENT_API_KEY`), không log; `.env` không commit | Tránh lộ key qua GitHub và log; đổi key không cần sửa code |
| Health check | Không có | `/health` (liveness), `/ready` (readiness, 503 khi chưa sẵn sàng), `/metrics` | Platform biết khi nào restart container và load balancer biết khi nào được gửi traffic vào |
| Logging | `print()` dạng text, in cả secret | `logging` dạng JSON có level và timestamp, chỉ log metadata | Log aggregator (Loki, Datadog…) parse và lọc được; không lộ dữ liệu nhạy cảm |
| Shutdown | Đột ngột, không xử lý | Graceful: `lifespan` đặt `is_ready=False`, chờ request dở dang, có handler SIGTERM | Deploy mới hay scale down không làm hỏng request người dùng đang chờ |
| Host / port | `localhost:8000` cố định | `0.0.0.0` và `PORT` từ env | Chạy được trong container và trên Railway/Render (platform tự cấp `PORT`) |
| Debug / reload | `reload=True` luôn bật | Chỉ bật khi `DEBUG=true` | Production không tốn tài nguyên theo dõi file, không bị restart bất ngờ |
| Input `/ask` | Query string `?question=`, không kiểm tra | JSON body `{"question": ...}`, rỗng thì 422 | Lỗi input trả mã rõ ràng, không gọi LLM vô ích |

**Lưu ý:** `production/config.py` chỉ dùng `os.getenv`, không tự đọc file `.env`. Muốn dùng giá trị trong `.env` khi chạy local phải export biến trước khi chạy, ví dụ `set -a; source .env; set +a`. Trên cloud, platform tự đặt các biến môi trường nên không cần file `.env`.

---

## Part 2: Docker (CP2)

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` (L8), bản Python đầy đủ dựa trên Debian, có sẵn compiler và nhiều thư viện hệ thống nên nặng.
2. **Working directory:** `/app` (L11). Các lệnh `COPY`, `RUN`, `CMD` sau đó chạy tương đối với thư mục này.
3. **Tại sao `COPY requirements.txt` trước khi copy code:** Docker cache theo từng layer. Nếu một layer thay đổi thì mọi layer phía sau phải build lại. `requirements.txt` ít thay đổi còn code thì sửa liên tục. Copy `requirements.txt` rồi `pip install` trước (L14–L17), sau đó mới copy `app.py` (L20). Nhờ vậy khi chỉ sửa code, layer cài dependencies vẫn dùng cache và build lại chỉ mất vài giây. Nếu copy cả code trước thì mỗi lần sửa một dòng code đều phải `pip install` lại từ đầu.
4. **CMD vs ENTRYPOINT:** `CMD ["python", "app.py"]` (L30) là lệnh mặc định, bị thay thế hoàn toàn nếu truyền lệnh khi chạy (`docker run my-agent:develop bash` sẽ chạy `bash`). `ENTRYPOINT` là chương trình cố định của container: tham số truyền vào `docker run` được nối thêm vào sau nó, muốn đổi phải dùng `--entrypoint`. Hay dùng kết hợp: `ENTRYPOINT` là chương trình, `CMD` là tham số mặc định.

### Exercise 2.2: Build và chạy container develop

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run --rm -p 8000:8000 my-agent:develop
$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker"
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 08:01:08 GMT
server: uvicorn
content-length: 103
content-type: application/json

{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
```

### Exercise 2.3: Multi-stage build và kích thước image

<!-- docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .
     docker images | grep my-agent -->

- **Stage 1 (`builder`, L16–L29):** từ `python:3.11-slim`, cài `gcc` và `libpq-dev` để biên dịch các gói cần build, rồi `pip install --user` toàn bộ dependencies vào `/root/.local`. Stage này chỉ dùng để build, không được deploy.
- **Stage 2 (`runtime`, L36–L72):** bắt đầu lại từ một `python:3.11-slim` sạch, chỉ `COPY --from=builder /root/.local` (các package đã cài sẵn) cùng `main.py` và `utils/mock_llm.py`. Chạy bằng user `appuser` (không phải root), có `HEALTHCHECK`, chạy `uvicorn` 2 worker.
- **Tại sao image nhỏ hơn:**
  - Base `slim` nhỏ hơn nhiều so với `python:3.11` bản đầy đủ.
  - Image cuối chỉ chứa stage cuối cùng. `gcc`, header `libpq-dev`, cache apt và mọi thứ dùng để build đều nằm lại ở stage `builder` và bị bỏ đi.
  - `--no-cache-dir` không lưu cache pip, `rm -rf /var/lib/apt/lists/*` xoá cache apt.
  - Kèm theo đó là lợi ích bảo mật: ít package hơn thì ít lỗ hổng hơn, và không chạy bằng root.

```text
$ docker images | grep my-agent
my-agent:advanced         13efc6cbffc5        240MB         57.4MB
my-agent:develop          0dad1f8881c1       1.67GB          426MB
```

Docker 29 in hai cột: **DISK USAGE** (dung lượng image sau khi giải nén trên ổ) và **CONTENT SIZE** (dung lượng nén khi tải về / push lên registry).

| Image | Disk usage | Content size (nén) |
|---|---:|---:|
| my-agent:develop (`python:3.11`, single-stage) | 1.67 GB ≈ 1670 MB | 426 MB |
| my-agent:advanced (`python:3.11-slim`, multi-stage) | 240 MB | 57.4 MB |
| **Giảm** | **≈ 85.6%** | **≈ 86.5%** |

Cách tính: (develop − advanced) / develop × 100 → (1670 − 240) / 1670 ≈ 85.6%; (426 − 57.4) / 426 ≈ 86.5%. Image production nhỏ hơn khoảng 7 lần và dưới mức 500 MB yêu cầu của Part 6.

### Exercise 2.4: Docker Compose stack

<!-- Cần `touch .env.local` trong 02-docker/production/ trước khi up.
     Nên dùng -p riêng, vd: docker compose -p k5-day14-docker up -d --build -->

**Services được start (4):**

| Service | Image | Vai trò | Cổng ra ngoài |
|---|---|---|---|
| `nginx` | `nginx:alpine` | Reverse proxy, load balancer, rate limit 10 req/s mỗi IP, thêm security header | `80`, `443` |
| `agent` | build từ `02-docker/production/Dockerfile` (target `runtime`) | FastAPI agent | Không mở, chỉ đi qua Nginx |
| `redis` | `redis:7-alpine` | Cache session và rate limit (maxmemory 256 MB, LRU) | Không |
| `qdrant` | `qdrant/qdrant:v1.9.0` | Vector database cho RAG | Không |

**Sơ đồ kiến trúc:**

```text
                 host :80
Client ──HTTP──►  ┌────────┐
                  │ nginx  │  rate limit 10r/s, security headers
                  └───┬────┘
          ────────────┼──────────── network "internal" (bridge) ─────────
                      │ proxy_pass http://agent:8000
                  ┌───▼────┐
                  │ agent  │  FastAPI :8000, healthcheck /health
                  └─┬────┬─┘
     REDIS_URL      │    │      QDRANT_URL
  redis://redis:6379│    │http://qdrant:6333
              ┌─────▼┐  ┌▼───────┐
              │redis │  │ qdrant │
              └──┬───┘  └───┬────┘
          volume redis_data  volume qdrant_data
```

**Các service giao tiếp với nhau thế nào:**
- Cả 4 service cùng nằm trong network `internal`, gọi nhau bằng **tên service** nhờ DNS nội bộ của Docker (`agent`, `redis`, `qdrant`).
- Chỉ `nginx` mở cổng ra host (`80:80`). Client gọi `http://localhost/...`, Nginx chuyển tiếp vào upstream `agent_backend` = `agent:8000`. Agent không mở cổng ra ngoài.
- Agent nhận địa chỉ Redis và Qdrant qua biến môi trường `REDIS_URL`, `QDRANT_URL`; secret lấy từ `env_file: .env.local` (không commit).
- Thứ tự khởi động: `depends_on` với `condition: service_healthy` bắt agent chờ Redis (`redis-cli ping`) và Qdrant (mở được TCP 6333) healthy rồi mới chạy; Nginx chờ agent.
- Dữ liệu Redis, Qdrant lưu trong volume nên còn nguyên khi container bị tạo lại.
- Ghi chú: comment đầu file ghi "agent (2 replicas)" nhưng file không khai báo `deploy.replicas`, nên mặc định chỉ có 1 container agent. Bên trong container đó `uvicorn` chạy 2 worker.

**Kết quả test qua Nginx:**

```text
$ docker compose -p k5-day14-docker up -d --build
 ✔ Container k5-day14-docker-qdrant-1 Healthy
 ✔ Container k5-day14-docker-redis-1  Healthy
 ✔ Container k5-day14-docker-agent-1  Started
 ✔ Container k5-day14-docker-nginx-1  Started

$ docker compose -p k5-day14-docker ps
NAME                       IMAGE                   COMMAND                  SERVICE   CREATED          STATUS                    PORTS
k5-day14-docker-agent-1    k5-day14-docker-agent   "uvicorn main:app --…"   agent     27 seconds ago   Up 13 seconds (healthy)   8000/tcp
k5-day14-docker-nginx-1    nginx:alpine            "/docker-entrypoint.…"   nginx     26 seconds ago   Up 13 seconds             0.0.0.0:80->80/tcp, [::]:80->80/tcp, 0.0.0.0:443->443/tcp, [::]:443->443/tcp
k5-day14-docker-qdrant-1   qdrant/qdrant:v1.9.0    "./entrypoint.sh"        qdrant    28 seconds ago   Up 25 seconds (healthy)   6333-6334/tcp
k5-day14-docker-redis-1    redis:7-alpine          "docker-entrypoint.s…"   redis     27 seconds ago   Up 24 seconds (healthy)   6379/tcp

$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
Date: Thu, 26 Nov 2026 08:07:45 GMT
Content-Type: application/json
Content-Length: 96
Connection: keep-alive
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":17.8,"version":"2.0.0","timestamp":"2026-11-26T08:07:45.567282"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
Date: Thu, 26 Nov 2026 08:07:54 GMT
Content-Type: application/json
Content-Length: 126
Connection: keep-alive
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

**Nhận xét:**
- Header `Server: nginx` và 3 header bảo mật (`X-Frame-Options`, `X-Content-Type-Options`, `X-XSS-Protection`) cho thấy request đi qua Nginx rồi mới tới agent.
- Trong `ps`, chỉ Nginx map cổng ra host (`0.0.0.0:80->80`); agent (`8000/tcp`), Redis, Qdrant chỉ mở trong network nội bộ.
- Redis và Qdrant `Healthy` trước, sau đó agent mới start, đúng với `depends_on: condition: service_healthy`.
- Compose cảnh báo thuộc tính `version` đã lỗi thời và bị bỏ qua; không ảnh hưởng kết quả.

---

## Part 3: Cloud Deployment (CP6 — deploy agent Part 6)

### Exercise 3.1 / 3.2: Deploy nền tảng chính

- **Nền tảng:** Railway (Exercise 3.1), deploy agent Part 6 trong `06-lab-complete/` bằng `railway up` (build theo `Dockerfile` + `railway.toml`).
- **URL public:** https://day14-agent-production-57725e43.up.railway.app
- **Redis trên cloud:** `railway add --database redis` tạo service Redis (`redis:8.2`, có volume) trong cùng project. Service agent đặt biến `REDIS_URL=${{Redis.REDIS_URL}}` (reference variable) nên Railway tự trỏ tới địa chỉ private của Redis; `/ready` trả 200 xác nhận agent kết nối được.
- **Biến môi trường trên Railway:** `AGENT_API_KEY` (sinh ngẫu nhiên, không ghi vào repo), `REDIS_URL`, `ENVIRONMENT=production`, `RATE_LIMIT_PER_MINUTE=10`, `DAILY_BUDGET_USD=1.0`, `MONTHLY_BUDGET_USD=10.0`; `PORT` do Railway cấp. Chi tiết: [DEPLOYMENT.md](DEPLOYMENT.md).
- **Ảnh:** [dashboard](screenshots/dashboard.png) · [running](screenshots/running.png) · [test](screenshots/test.png)

**4 lệnh tự kiểm (CP6):**

```text
PS> $base = "https://day14-agent-production-57725e43.up.railway.app"
PS> curl.exe -sS -o NUL -w "%{http_code}`n" "$base/health"
200
PS> curl.exe -sS -o NUL -w "%{http_code}`n" "$base/ready"
200
PS> curl.exe -sS -o NUL -w "%{http_code}`n" -X POST "$base/ask" -H "Content-Type: application/json" -d '{\"question\":\"Hello\"}'
401
PS> curl.exe -sS -o NUL -w "%{http_code}`n" -X POST "$base/ask" -H "Content-Type: application/json" -H "X-API-Key: $key" -d '{\"question\":\"Hello\"}'
200
```

**Sự cố khi deploy:** sau `railway add --database redis`, CLI tự link vào service Redis nên lần `railway up` đầu tiên đẩy code agent vào **service Redis**. Đã gỡ deployment đó (Redis vẫn chạy image `redis:8.2`), tạo service riêng `railway add --service day14-agent`, `railway service day14-agent`, kiểm tra `railway status` thấy đúng service rồi mới `railway up` lại. Bài học: luôn kiểm tra service đang link trước khi deploy.

### Nền tảng còn lại: so sánh `railway.toml` với `render.yaml`

So sánh bản mình viết cho agent Part 6 (`06-lab-complete/railway.toml`, `06-lab-complete/render.yaml`), có đối chiếu bản mẫu trong `03-cloud-deployment/`:

| Khía cạnh | `railway.toml` | `render.yaml` | Ghi chú |
|---|---|---|---|
| Định dạng / phạm vi | TOML, cấu hình **một service** (service đang link) | YAML Blueprint, mô tả **cả hạ tầng**: nhiều service trong một file | Railway tạo service/database bằng CLI hoặc dashboard; Render tạo tất cả từ file |
| Cách build | `builder = "DOCKERFILE"`, `dockerfilePath = "Dockerfile"` | `runtime: docker`, `rootDir: 06-lab-complete`, `dockerfilePath`, `dockerContext` | Bản mẫu 03 dùng `NIXPACKS` / `runtime: python` + `buildCommand: pip install ...`, không cần Dockerfile |
| Lệnh start | Không khai báo: dùng `CMD` trong Dockerfile (`${PORT:-8000}`) | Không khai báo: dùng `CMD` trong Dockerfile | Bản mẫu 03 khai `startCommand = "uvicorn app:app --port $PORT"` vì không có Dockerfile |
| Port | Railway inject `PORT` | Render inject `PORT` | Cả hai đều cần app lắng nghe theo `PORT` |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout = 100` | `healthCheckPath: /health` | Railway: health check lúc deploy để quyết định có chuyển traffic sang bản mới không |
| Restart | `restartPolicyType = "ON_FAILURE"`, tối đa 3 lần | Render tự restart, không cấu hình trong file | |
| Biến môi trường / secret | **Không có trong file**: đặt bằng `railway variables --set` hoặc dashboard | Khai trong `envVars`; secret dùng `sync: false` (nhập tay trên dashboard) hoặc `generateValue: true` (Render tự sinh) | Cả hai đều không ghi giá trị secret vào repo |
| Redis / service phụ | Không khai trong file; `railway add --database redis`, nối bằng `REDIS_URL=${{Redis.REDIS_URL}}` | Khai luôn service `type: keyvalue` và nối bằng `fromService: {type: keyvalue, property: connectionString}` | Bản mẫu 03 dùng `type: redis` (tên cũ) |
| Cách deploy | `railway up` đẩy thư mục local lên, không cần GitHub | Push lên GitHub → New → Blueprint; `autoDeploy` khi push | |
| Gói miễn phí | Trial có credit ($5 / 30 ngày lúc mình đăng ký) | Free plan, service ngủ sau ~15 phút không có request (cold start) | |

**Vì sao khác nhau:** Railway coi file config là phần **build/deploy của từng service**, còn hạ tầng (service, database, biến) quản lý bằng CLI/dashboard. Vì vậy `railway.toml` ngắn và không nói gì về Redis hay secret. Render theo hướng **Infrastructure as Code**: một Blueprint mô tả đủ web service, Redis, biến môi trường và cách nối chúng với nhau, nên dựng lại toàn bộ môi trường chỉ từ repo, đổi lại file dài hơn và phải có GitHub. (Railway cũng đang chuyển sang Infrastructure as Code: CLI cảnh báo Config as Code `railway.toml` sẽ được thay bằng `.railway/railway.ts`.)

### Exercise 3.3: Pipeline CI/CD Cloud Run

Đọc `03-cloud-deployment/production-cloud-run/cloudbuild.yaml` và `service.yaml`:

1. **Trigger:** push lên nhánh `main` kích hoạt Cloud Build chạy `cloudbuild.yaml` (hoặc chạy tay `gcloud builds submit`), timeout 20 phút.
2. **Test:** bước `test` chạy trong `python:3.11-slim`: `pip install` + `pytest tests/`; test fail thì pipeline dừng, không build. (Thư mục `tests/` chưa có trong repo nên bước này sẽ fail nếu chạy thật.)
3. **Build & push:** `docker build` (dùng `--cache-from ...:latest` để tận dụng layer cache) gắn 2 tag `:$COMMIT_SHA` và `:latest`, rồi `docker push --all-tags` lên Container Registry `gcr.io/$PROJECT_ID/ai-agent`.
4. **Deploy:** `gcloud run deploy ai-agent --image ...:$COMMIT_SHA` (tag theo commit để biết chính xác bản nào đang chạy và rollback được) ở `asia-southeast1`, 512Mi / 1 CPU, `min-instances=1` để tránh cold start, `max-instances=10`; `ENVIRONMENT` qua `--set-env-vars`, `OPENAI_API_KEY` lấy từ **Secret Manager** qua `--set-secrets`.
5. **`service.yaml`** là cách khai báo cùng service đó theo kiểu Infrastructure as Code (Knative, `gcloud run services replace`): autoscale 1–10 instance, mỗi instance tối đa 80 request đồng thời, timeout 60 giây, `livenessProbe` gọi `/health` mỗi 30 giây, `startupProbe` gọi `/ready` trước khi nhận traffic, secret (`OPENAI_API_KEY`, `AGENT_API_KEY`) lấy từ Secret Manager qua `secretKeyRef`.

---

## Part 4: API Security (CP3)

### Exercise 4.1: API Key authentication (`04-api-gateway/develop`)

Chạy: `AGENT_API_KEY=secret-key-123 python app.py`

- **API key được check ở đâu:** hàm `verify_api_key` trong `develop/app.py`. `APIKeyHeader(name="X-API-Key")` đọc header, key đúng lấy từ env `AGENT_API_KEY`. Hàm được gắn vào endpoint bằng `Depends(verify_api_key)` ở `POST /ask`, nên FastAPI chạy nó trước khi vào thân hàm. Endpoint `/` không gắn nên vẫn public.
- **Sai / thiếu key thì sao:** thiếu header thì **401** `Missing API key`; có header nhưng sai thì **403** `Invalid API key.`. Request bị chặn trước khi gọi LLM nên không tốn chi phí.
- **Rotate key thế nào:** key nằm trong biến môi trường chứ không nằm trong code, nên chỉ cần đổi `AGENT_API_KEY` trên platform (hoặc secret manager) rồi restart/redeploy, không phải sửa code. Muốn đổi mà không làm gián đoạn client: cho app chấp nhận tạm thời cả key cũ lẫn key mới (ví dụ `AGENT_API_KEYS=old,new`), báo client chuyển sang key mới, rồi gỡ key cũ.

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 401 Unauthorized
date: Thu, 26 Nov 2026 08:10:22 GMT
server: uvicorn
content-length: 67
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 403 Forbidden
date: Thu, 26 Nov 2026 08:10:38 GMT
server: uvicorn
content-length: 29
content-type: application/json

{"detail":"Invalid API key."}
```

```text
$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: ***" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 08:10:48 GMT
server: uvicorn
content-length: 122
content-type: application/json

{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT (`04-api-gateway/production`)

- **JWT flow:**
  1. Client gửi `POST /auth/token` với `{"username","password"}`. `authenticate_user` so với `DEMO_USERS`; sai thì 401 `Invalid credentials`.
  2. Đúng thì `create_token` ký một JWT bằng HS256 với `JWT_SECRET` (lấy từ env). Payload gồm `sub` (username), `role`, `iat` (thời điểm cấp), `exp` (hết hạn sau 60 phút).
  3. Client gửi token ở header `Authorization: Bearer <token>` cho mọi request sau.
  4. `verify_token` (dependency của `/ask`) giải mã và kiểm tra chữ ký và hạn: thiếu token thì 401, hết hạn thì 401 `Token expired`, chữ ký sai/token bị sửa thì 403 `Invalid token`. Hợp lệ thì trả `username` và `role` cho endpoint dùng tiếp.
  - Ưu điểm so với API key: server không phải lưu session, token tự mang theo danh tính và role (dùng để chọn mức rate limit), và tự hết hạn.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 08:11:38 GMT
server: uvicorn
content-length: 307
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin

{"access_token":"eyJhbGciOiJIUzI1NiIs...<đã che>","token_type":"bearer","expires_in_minutes":60,"hint":"..."}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 08:12:00 GMT
server: uvicorn
content-length: 192
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin

{"question":"Explain JWT","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","usage":{...}}
```

### Exercise 4.3: Rate limiting

- **Thuật toán:** sliding window log (`rate_limiter.py`). Mỗi user có một `deque` lưu timestamp các request. Mỗi lần gọi, bỏ các timestamp cũ hơn 60 giây, nếu số còn lại ≥ giới hạn thì trả 429, ngược lại thêm timestamp hiện tại. Khác fixed window ở chỗ không bị dồn gấp đôi request ở ranh giới hai phút.
- **Giới hạn:** role `user` (tài khoản `student`) 10 request / 60 giây; role `admin` 100 request / 60 giây. Khi vượt: 429 kèm header `X-RateLimit-Limit`, `X-RateLimit-Remaining: 0`, `X-RateLimit-Reset`, `Retry-After`.
- **Bypass cho admin:** không bỏ hẳn giới hạn mà dùng limiter riêng theo role lấy từ JWT: `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user` trong `app.py`. Tài khoản admin (`teacher`) được 100 req/phút. Muốn bỏ hẳn thì bỏ qua `limiter.check()` khi `role == "admin"`, nhưng nên giữ một mức trần để chống token admin bị lộ.

Request `Explain JWT` ở 4.2 là request thứ 1 trong cửa sổ 60 giây, nên vòng lặp chỉ còn 9 request được 200, request thứ 10 của vòng lặp (thứ 11 tổng cộng) bắt đầu bị 429:

```text
$ for i in {1..20}; do echo -n "Request $i: "; curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\":\"Test $i\"}"; done
Request 1: 200
Request 2: 200
Request 3: 200
Request 4: 200
Request 5: 200
Request 6: 200
Request 7: 200
Request 8: 200
Request 9: 200
Request 10: 429
Request 11: 429
Request 12: 429
Request 13: 429
Request 14: 429
Request 15: 429
Request 16: 429
Request 17: 429
Request 18: 429
Request 19: 429
Request 20: 429

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Over limit"}'
HTTP/1.1 429 Too Many Requests
date: Thu, 26 Nov 2026 08:12:21 GMT
server: uvicorn
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795680802
retry-after: 39
content-length: 98
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":39}}
```

### Exercise 4.4: Cost guard

**Cách làm trong `04-api-gateway/production/cost_guard.py`:**
- Mỗi user có một `UsageRecord` theo ngày (`day = YYYY-MM-DD`) đếm `input_tokens`, `output_tokens`, `request_count`. Chi phí tính theo giá GPT-4o-mini: $0.15 / 1M token input, $0.60 / 1M token output.
- Trong `/ask`, thứ tự là: rate limit → `cost_guard.check_budget(username)` **trước** khi gọi LLM → gọi LLM → `record_usage()` cộng token thật vào record.
- `check_budget`: tổng chi phí toàn hệ thống ≥ $10/ngày thì 503; chi phí của user ≥ $1/ngày thì **402 Payment Required**; ≥ 80% budget thì ghi log cảnh báo.
- Reset: sang ngày mới thì `_get_record` thấy `record.day != today` và tạo record mới.
- Hạn chế: state nằm trong RAM của process nên mất khi restart và không dùng chung giữa nhiều instance. Ngoài ra field `usage.budget_remaining_usd` trong response đang trả về `total_cost_usd` (số đã dùng), không phải số còn lại.

**Bản dùng Redis (yêu cầu của đề, budget $10/tháng):**

```python
from datetime import datetime, timezone

def check_budget(r, user_id: str, estimated_cost: float, monthly_budget: float = 10.0) -> bool:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    key = f"budget:{user_id}:{month}"           # sang tháng mới là key mới → tự reset
    current = float(r.get(key) or 0)
    if current + estimated_cost > monthly_budget:
        return False                             # endpoint trả 402
    pipe = r.pipeline()
    pipe.incrbyfloat(key, estimated_cost)        # cộng nguyên tử, an toàn khi nhiều instance
    pipe.expire(key, 32 * 24 * 3600)             # tự xoá sau ~1 tháng
    pipe.execute()
    return True
```

Lưu trong Redis thì mọi instance đọc chung một số, restart không mất, và key theo tháng giúp reset tự động.

**Vì sao cần cả rate limit lẫn cost guard:** rate limit chặn **tốc độ** (số request mỗi phút), bảo vệ server khỏi spam, brute force, một client chiếm hết tài nguyên. Cost guard chặn **tổng tiền** theo ngày/tháng. Một user gọi đều 10 req/phút không bao giờ dính 429 nhưng chạy suốt cả tháng, hoặc gửi prompt rất dài, vẫn đốt hết ngân sách LLM, và chỉ cost guard bắt được trường hợp đó. Ngược lại cost guard không chặn được một đợt bùng nổ request rẻ làm sập server. Hai cơ chế bổ sung cho nhau.

---

## Part 5: Scaling & Reliability (CP4)

### Exercise 5.1: Health checks

- **`/health` (liveness) khác `/ready` (readiness) ở đâu:**

| | `/health` — liveness | `/ready` — readiness |
|---|---|---|
| Câu hỏi | Process còn sống không? | Có nên gửi traffic vào instance này không? |
| Ai gọi | Platform / orchestrator (Docker `HEALTHCHECK`, K8s liveness, Railway) | Load balancer / K8s readiness |
| Fail thì sao | Platform **restart** container | **Ngừng gửi traffic** vào instance đó, không restart |
| Trong `develop/app.py` | Luôn 200 khi process chạy; kèm uptime, version và check memory (`degraded` nếu RAM ≥ 90%) | 503 khi `_is_ready = False` (đang khởi động hoặc đang shutdown); 200 kèm `in_flight_requests` |

Tách hai endpoint vì một instance có thể còn sống nhưng chưa sẵn sàng (đang load model, mất kết nối Redis, đang tắt). Nếu dùng chung một endpoint cho cả hai, mất Redis sẽ làm platform restart liên tục các container vẫn khoẻ, trong khi đúng ra chỉ cần tạm ngừng gửi traffic.

```text
$ python app.py &
2026-11-26 15:15:05,289 INFO Starting agent on port 8000
INFO:     Waiting for application startup.
2026-11-26 15:15:05,322 INFO Agent starting up...
2026-11-26 15:15:05,322 INFO Loading model and checking dependencies...
2026-11-26 15:15:05,522 INFO ✅ Agent is ready!
INFO:     Application startup complete.

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 08:15:07 GMT
server: uvicorn
content-length: 185
content-type: application/json

{"status":"ok","uptime_seconds":2.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T08:15:07.664535+00:00","checks":{"memory":{"status":"ok","used_percent":11.7}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 08:15:07 GMT
server: uvicorn
content-length: 37
content-type: application/json

{"ready":true,"in_flight_requests":1}
```

`in_flight_requests` bằng 1 vì middleware `track_requests` đếm luôn chính request `/ready` đang được xử lý.

### Exercise 5.2: Graceful shutdown

```text
$ curl -s -X POST "http://localhost:8000/ask?question=Long%20task" &
$ sleep 0.2; kill -TERM $PID
INFO:     Shutting down
INFO:     127.0.0.1:55824 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
INFO:     Waiting for application shutdown.
2026-11-26 15:15:17,864 INFO 🔄 Graceful shutdown initiated...
2026-11-26 15:15:17,864 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [1504]
2026-11-26 15:15:17,865 INFO Received signal 15 — uvicorn will handle graceful shutdown
[1]-  Done                    python app.py
[2]+  Done                    curl -s -X POST "http://localhost:8000/ask?question=Long%20task"
```

**Quan sát:** request **vẫn hoàn thành** với `200 OK` dù SIGTERM được gửi ngay sau đó.
1. Nhận SIGTERM, uvicorn in `Shutting down`: **ngừng nhận kết nối mới** nhưng không cắt request đang chạy.
2. Request `/ask` đang dở được xử lý xong và trả 200; curl nhận đủ body.
3. Sau đó uvicorn chạy phần shutdown của `lifespan`: đặt `_is_ready = False` (nên `/ready` sẽ trả 503 và load balancer ngừng gửi traffic), chờ `_in_flight_requests` về 0 (tối đa 30 giây), rồi thoát với `Shutdown complete`.
4. `uvicorn.run(..., timeout_graceful_shutdown=30)` là mức chờ tối đa; quá 30 giây thì tắt hẳn.

Dòng `Received signal 15` của `handle_sigterm` in ra cuối cùng vì uvicorn đã cài handler riêng để xử lý SIGTERM; hàm này chỉ để log thêm. Nếu dùng `kill -9` (SIGKILL) thì process chết ngay, không bắt được, request đang chạy bị mất. Vì vậy platform luôn gửi SIGTERM trước, chờ một khoảng (grace period), rồi mới SIGKILL.

### Exercise 5.3: Stateless design

**Vì sao state trong RAM hỏng khi chạy nhiều instance:**
- Mỗi instance là một process riêng với RAM riêng. Nếu lưu `conversation_history = {}` trong RAM, request 1 của user A rơi vào instance 1 thì history nằm ở instance 1. Load balancer chia request theo round-robin nên request 2 rơi vào instance 2: instance 2 không có history, agent "quên" cuộc trò chuyện.
- Restart, deploy bản mới, scale down hay container bị crash đều làm mất toàn bộ state trong RAM.
- Không scale ngang được: thêm instance thì dữ liệu càng bị chia nhỏ.

**Cách `05-scaling-reliability/production/app.py` giải quyết:**
- Session lưu trong Redis, key `session:{session_id}`, giá trị là JSON `{"history": [...]}`, `SETEX` với TTL 3600 giây (`save_session`, `load_session`).
- `append_to_history` đọc session từ Redis, thêm message, giữ tối đa 20 message rồi ghi lại.
- Client gửi `session_id` trong mỗi request `/chat`, nên **bất kỳ instance nào** cũng đọc được đúng history. Response có `served_by` để thấy instance nào xử lý.
- `/ready` ping Redis, mất Redis thì 503. App tự chuyển sang dict trong RAM nếu không kết nối được Redis lúc khởi động, nhưng đó chỉ là chế độ dự phòng, không scale được.

### Exercise 5.4: Load balancing

Chạy `docker compose -p k5-day14-scaling up -d --build --scale agent=3`: Compose tạo 3 container `agent-1..3`, 1 `redis`, 1 `nginx` (chỉ Nginx mở cổng `8080`).

```text
$ docker compose -p k5-day14-scaling ps
NAME                       IMAGE                    COMMAND                  SERVICE   CREATED          STATUS                            PORTS
k5-day14-scaling-agent-1   k5-day14-scaling-agent   "python -m uvicorn a…"   agent     23 seconds ago   Up 6 seconds (health: starting)   8000/tcp
k5-day14-scaling-agent-2   k5-day14-scaling-agent   "python -m uvicorn a…"   agent     23 seconds ago   Up 5 seconds (health: starting)   8000/tcp
k5-day14-scaling-agent-3   k5-day14-scaling-agent   "python -m uvicorn a…"   agent     23 seconds ago   Up 3 seconds (health: starting)   8000/tcp
k5-day14-scaling-nginx-1   nginx:alpine             "/docker-entrypoint.…"   nginx     22 seconds ago   Up 1 second                       0.0.0.0:8080->80/tcp, [::]:8080->80/tcp
k5-day14-scaling-redis-1   redis:7-alpine           "docker-entrypoint.s…"   redis     24 seconds ago   Up 19 seconds (healthy)           6379/tcp
```

**Lần chạy đầu: 10/10 request bị `502 Bad Gateway`.** Vòng lặp curl chạy ngay sau `up`, khi 3 agent mới lên được 3–6 giây (`health: starting`) và uvicorn trong container chưa mở cổng 8000. Nginx không kết nối được backend nào nên trả 502. Vài giây sau `test_stateless.py` (5.5) chạy thì mọi request thành công. Bài học: phải chờ instance sẵn sàng (readiness) rồi mới gửi traffic. Nginx open-source ở đây không gọi `/ready`, chỉ thử lại instance khác khi gặp lỗi kết nối hoặc 503 (`proxy_next_upstream error timeout http_503`, tối đa 3 lần).

**Lần chạy lại: chờ cả 3 agent `healthy` rồi mới gửi request.**

```text
$ docker compose -p k5-day14-scaling up -d --scale agent=3
$ until [ "$(docker compose -p k5-day14-scaling ps agent | grep -c '(healthy)')" -eq 3 ]; do echo "waiting..."; sleep 3; done
waiting...
waiting...
$ docker compose -p k5-day14-scaling ps
NAME                       IMAGE                    COMMAND                  SERVICE   CREATED          STATUS                    PORTS
k5-day14-scaling-agent-1   k5-day14-scaling-agent   "python -m uvicorn a…"   agent     34 seconds ago   Up 19 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   k5-day14-scaling-agent   "python -m uvicorn a…"   agent     34 seconds ago   Up 19 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   k5-day14-scaling-agent   "python -m uvicorn a…"   agent     34 seconds ago   Up 18 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   nginx:alpine             "/docker-entrypoint.…"   nginx     33 seconds ago   Up 16 seconds             0.0.0.0:8080->80/tcp, [::]:8080->80/tcp
k5-day14-scaling-redis-1   redis:7-alpine           "docker-entrypoint.s…"   redis     34 seconds ago   Up 32 seconds (healthy)   6379/tcp

$ for i in {1..10}; do curl -s -X POST http://localhost:8080/chat -H "Content-Type: application/json" -d "{\"question\":\"Request $i\"}" | python -c "...print('Request $i ->', d['served_by'])"; done
Request 1 -> instance-10dc90
Request 2 -> instance-10905c
Request 3 -> instance-ee51c0
Request 4 -> instance-10dc90
Request 5 -> instance-10905c
Request 6 -> instance-ee51c0
Request 7 -> instance-10dc90
Request 8 -> instance-10905c
Request 9 -> instance-ee51c0
Request 10 -> instance-10dc90

$ docker compose -p k5-day14-scaling logs agent --tail 20   (rút gọn)
agent-3  | INFO:app:Starting instance instance-ee51c0
agent-3  | INFO:app:Storage: Redis ✅
agent-3  | INFO:     172.19.0.6:58090 - "POST /chat HTTP/1.1" 200 OK   (x3)
agent-1  | INFO:app:Starting instance instance-10905c
agent-1  | INFO:app:Storage: Redis ✅
agent-1  | INFO:     172.19.0.6:48848 - "POST /chat HTTP/1.1" 200 OK   (x3)
agent-2  | INFO:app:Starting instance instance-10dc90
agent-2  | INFO:app:Storage: Redis ✅
agent-2  | INFO:     172.19.0.6:46050 - "POST /chat HTTP/1.1" 200 OK   (x4)
```

**Kết quả:** 10/10 request trả 200, chia đều theo round-robin đúng chu kỳ `10dc90 → 10905c → ee51c0`: agent-2 (`10dc90`) 4 request, agent-1 (`10905c`) 3, agent-3 (`ee51c0`) 3. Log mỗi agent đều có dòng `POST /chat` từ cùng một IP `172.19.0.6` (container Nginx), xác nhận request đi qua Nginx rồi mới tới agent. Cả 3 instance đều báo `Storage: Redis ✅`. Các dòng `GET /health` từ `127.0.0.1` là healthcheck của Docker chạy bên trong container mỗi 15 giây.

**Cách Nginx chia tải:** `upstream agent_cluster { server agent:8000; }`. Lúc khởi động, Nginx hỏi DNS nội bộ của Docker (`127.0.0.11`) tên `agent` và nhận về IP của cả 3 container, rồi chia request theo **round-robin**. Header `X-Served-By: $upstream_addr` cho biết container nào xử lý.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
============================================================
Stateless Scaling Demo
============================================================

Session ID: 35553e0a-c80d-4692-8606-9c976f1530da

Request 1: [instance-0795ad]
  Q: What is Docker?
Request 2: [instance-0795ad]
  Q: Why do we need containers?
Request 3: [instance-165816]
  Q: What is Kubernetes?
Request 4: [instance-0795ad]
  Q: How does load balancing work?
Request 5: [instance-165816]
  Q: What is Redis used for?

------------------------------------------------------------
Total requests: 5
Instances used: {'instance-0795ad', 'instance-165816'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ...
  [user]: What is Redis used for?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...

✅ Session history preserved across all instances via Redis!
```

**Giải thích kết quả:**
- 5 câu hỏi cùng một `session_id` được 2 instance khác nhau xử lý (`instance-0795ad` và `instance-165816`), xen kẽ nhau.
- Đọc history cuối cùng vẫn đủ **10 message** (5 user + 5 assistant), đúng thứ tự. Nghĩa là instance `165816` ở request 3 đã đọc được history mà instance `0795ad` ghi ở request 1–2, và ngược lại, vì cả hai cùng đọc/ghi Redis chứ không giữ trong RAM.
- Nếu app lưu history trong RAM, instance `165816` sẽ chỉ thấy các câu của chính nó và history sẽ thiếu message.
- Lần này chỉ thấy 2 trên 3 instance, khác với lần chạy lại 5.4 (chia đều cả 3). Lý do có thể là `test_stateless.py` chạy ngay sau khi stack vừa lên, khi một agent chưa mở cổng: Nginx thử kết nối thất bại thì chuyển sang instance khác (`proxy_next_upstream error`), nên request vẫn thành công nhưng chỉ rơi vào 2 instance. Khi cả 3 đã healthy thì round-robin chia đều.
- Script không tự tắt instance nào (khác mô tả trong CODE_LAB). Muốn thấy rõ hơn: `docker compose -p k5-day14-scaling stop agent` cho một container giữa chừng, rồi gọi tiếp với cùng `session_id`, history vẫn còn.

Xong thì `docker compose -p k5-day14-scaling down` để trả cổng 8080.

---

## Part 6: Final Project (CP5)

<!-- Trong 06-lab-complete/: python check_production_ready.py (bản đầy đủ, có Docker) → PASS 12/12.
     Dán phần "📋 Checklist Deliverable" của output. -->

Chạy trong WSL2 (`os.name == "posix"`), Docker 29.9.3, bản đầy đủ (không `--static-only` / `--skip-docker`):

```text
$ cd 06-lab-complete && python check_production_ready.py
...
✅ 1. Dockerfile (multi-stage, < 500 MB)
     ✅ Docker: build image thành công — 12s
     ✅ Docker: image < 500 MB — 273 MB
     ✅ Docker: container chạy được, GET /health → 200
✅ 5. Readiness endpoint (GET /ready)
     ✅ Khi chạy: Redis mất kết nối → /ready 503 (còn /health vẫn 200)
✅ 7. Rate limiting
     ✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
     ✅ Khi chạy: state nằm trong Redis — restart agent vẫn bị 429
✅ 8. Cost guard
     ✅ Khi chạy: hết budget → 402 — 402 ở request thứ 1
✅ 10. Structured logging
     ✅ Khi chạy: mọi dòng log của app là JSON hợp lệ — 17 dòng JSON
✅ 11. Graceful shutdown
     ✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
     ✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 1.1s
...
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

### Thiết kế chính

| Thành phần | Cách làm | File |
|---|---|---|
| Config | Mọi giá trị đọc từ env (`PORT`, `ENVIRONMENT`, `AGENT_API_KEY`, `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD`, `MONTHLY_BUDGET_USD`); không có API key mặc định → chưa đặt key thì `/ask` từ chối tất cả (fail closed) | `app/config.py` |
| Auth | `APIKeyHeader("X-API-Key", auto_error=False)`, so sánh bằng `secrets.compare_digest`; thiếu/sai → 401 | `app/auth.py` |
| Redis | `redis.from_url` không kết nối lúc import → app khởi động được khi chưa có Redis; timeout 2 giây | `app/redis_client.py` |
| Rate limit | Sliding window 60 giây bằng ZSET `ratelimit:<sha256(key)>`: `ZREMRANGEBYSCORE` → `ZADD` → `ZCARD` → `EXPIRE` trong một MULTI/EXEC; vượt thì xoá request vừa ghi, trả 429 + `Retry-After` | `app/rate_limiter.py` |
| Cost guard | `INCRBYFLOAT` vào `cost:daily:YYYY-MM-DD` và `cost:monthly:YYYY-MM` (UTC, tự hết hạn); kiểm tra **trước** khi gọi LLM, vượt → 402 | `app/cost_guard.py` |
| `/health` vs `/ready` | `/health` luôn 200 khi process sống (không đụng Redis, tránh bị platform restart oan); `/ready` ping Redis, mất Redis hoặc đang tắt → 503 | `app/main.py` |
| Log | Logger `agent` riêng với `JsonFormatter`: mỗi dòng 1 JSON (`ts`, `lvl`, `event`, ...); middleware ghi `method`, `path`, `status`, `ms` cho mỗi request; traceback nằm trong field `exc` | `app/main.py` |
| Xử lý lỗi | Input sai → 422 của Pydantic; lỗi Redis → 503 `Retry-After`; lỗi khác → 500 `Internal server error`; không trả stack trace cho client | `app/main.py` |
| Graceful shutdown | Không tự đăng ký handler SIGTERM; uvicorn ngừng nhận kết nối mới, chờ request đang chạy, rồi chạy phần shutdown của `lifespan` (đặt `shutting_down`, đóng Redis) | `app/main.py` |
| Docker | Multi-stage (builder tạo `/opt/venv`, runtime `python:3.11-slim` chỉ copy venv + `app/` + `utils/`), user `app`, `HEALTHCHECK`, `CMD sh -c "exec uvicorn ... --port ${PORT:-8000} --workers 2"` để uvicorn là PID 1 | `Dockerfile` |

---

## AI log

<!-- Mỗi lần dùng AI cho code Part 6 hoặc câu trả lời: hỏi gì, nhận gì, tự sửa và tự kiểm tra thế nào. -->

| # | Công cụ | Hỏi gì | Nhận được gì | Tự sửa / tự kiểm tra thế nào |
|---|---|---|---|---|
| 1 | Claude Code | Đọc các file .md của repo, liệt kê những gì cần setup trước khi làm lab | Danh sách việc cần làm (WSL2, pip/venv, Docker WSL integration, tên repo) và khung file `MISSION_ANSWERS.md` | Tự chạy lệnh kiểm tra: `os.name` in `posix`, `docker run --rm hello-world` chạy được |
| 2 | Claude Code | Điền Part 1 (anti-pattern, bảng so sánh develop/production) từ `develop/app.py`, `production/app.py`, `production/config.py` | Bảng 8 anti-pattern kèm số dòng, nhận xét 1.2, bảng so sánh 1.3 | Tự đối chiếu từng số dòng với code; output curl 1.2 là của mình chạy; output `/health` 1.3 tự chạy rồi dán |
| 3 | Claude Code | Trả lời câu hỏi Exercise 2.1, 2.3, 2.4 từ các Dockerfile, `docker-compose.yml`, `nginx.conf` | Câu trả lời 2.1, giải thích multi-stage, bảng service và sơ đồ stack | Tự đối chiếu số dòng; số MB và output curl/compose là của mình chạy rồi dán |
| 4 | Claude Code | Giải thích Part 4 (API key, JWT flow, rate limiter, cost guard) từ `04-api-gateway/` | Câu trả lời 4.1–4.4 và hàm `check_budget` dùng Redis | Output curl 401/403/200/429 là của mình chạy; tự đọc lại `auth.py`, `rate_limiter.py`, `cost_guard.py` để đối chiếu; đã che token |
| 5 | Claude Code | Giải thích Part 5 (liveness/readiness, graceful shutdown, stateless, load balancing) và lỗi 502 lúc chạy vòng lặp 5.4 | Câu trả lời 5.1–5.5; nguyên nhân 502 là agent chưa sẵn sàng | Output là của mình chạy; chạy lại vòng lặp 5.4 sau khi 3 agent healthy để kiểm chứng |
| 6 | Claude Code | Hoàn thành các TODO Part 6 trong `06-lab-complete/` theo bảng tiêu chí của README và `check_production_ready.py` | Code `app/` (config, auth, Redis, rate limit sliding window bằng ZSET, cost guard theo ngày/tháng, log JSON, lifespan), Dockerfile multi-stage, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Tự chạy `python check_production_ready.py` bản đầy đủ trong WSL có Docker → PASS 12/12; đọc lại từng file để giải thích được với Lab Coach |
| 7 | Claude Code | Hướng dẫn deploy agent Part 6 lên Railway, xử lý lỗi `railway up` đẩy nhầm vào service Redis; viết `DEPLOYMENT.md`, so sánh `railway.toml`/`render.yaml`, tóm tắt pipeline Cloud Run | Các lệnh Railway CLI, cách đặt `REDIS_URL=${{Redis.REDIS_URL}}`, nội dung Part 3 | Tự chạy lệnh deploy và 4 lệnh tự kiểm (200/200/401/200); tự kiểm tra trên dashboard Redis vẫn chạy image gốc; tự đọc lại `cloudbuild.yaml`, `service.yaml` |
| 8 | Claude Code | Part 7 bước 1 (CP7): script chạy load test + eval, thêm metric `agent_question_length_chars`, tìm câu v2 sai và bổ sung `KNOWLEDGE_BASE`, điền mục 1–3 `BENCHMARK_REPORT.md` | Script `run_cp7.sh`, 2 entry KB mới (JWT, HPA), phần phân tích số liệu | Tự chạy script và eval cuối trên máy mình; mọi số trong report lấy từ file JSON trong `reports/`; kiểm tra lại eval cuối 23/23, 0 regression |
| 9 | Claude Code | Part 7 bước 2 (CP8): script chạy Locust headless scale 1/3 và đo thời gian alert firing; tìm lỗi agent crash `No module named 'uvicorn'`; điền mục 4–6 `BENCHMARK_REPORT.md` | Script `cp8_1_scale.sh`, `cp8_2_break.sh`; sửa `production/Dockerfile` (`PYTHONUSERBASE`); phân tích số liệu và câu trả lời Exercise 7.4–7.6 | Tự chạy script, tự chụp 4 ảnh Grafana/Jaeger/Prometheus; kiểm tra trace_id trong Jaeger khớp dòng log JSON; mọi số trong report đối chiếu với CSV Locust, `alert_timeline.txt` và truy vấn Prometheus |
| 10 | | | | |
