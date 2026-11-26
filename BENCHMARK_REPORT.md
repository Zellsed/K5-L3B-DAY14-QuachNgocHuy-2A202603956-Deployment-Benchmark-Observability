# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON / CSV / screenshot thật trong `reports/`, `benchmark-results/`, `screenshots/`.

**Họ tên:** Quach Ngoc Huy — 2A202603956
**Ngày chạy:** 2026-11-26
**Môi trường:** Laptop Windows 11 Home, WSL2 Ubuntu 24.04, Python 3.12.3, Docker Desktop (Engine 29.9.3, Compose v5.7.1). Load test và agent chạy trên cùng một máy.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Mock LLM mất ~120–180 ms + retrieval ~20 ms, nên khi tải bình thường p95 đo được ~230–450 ms (concurrency 1–50). 500 ms vẫn là "phản hồi tức thì" với người dùng và chừa biên cho jitter. Đặt theo p95 chứ không theo average vì average che mất tail latency (mục 2.2) |
| Error rate `/ask` | < 2 % | Upstream LLM lỗi ngẫu nhiên ~1% (`MOCK_ERROR_RATE=0.01`), giống provider thật thỉnh thoảng trả 429/503. SLO 2% = gấp đôi mức nền, error budget 2% đủ hấp thụ dao động nhưng bắt được sự cố thật (vd lỗi 20%) |
| Accuracy trên golden dataset | ≥ 85 % | Gate trong CI để chặn merge khi chất lượng tụt. Không đặt 100% cho gate thường ngày vì đổi prompt/model có thể làm lệch 1–2 câu chấp nhận được; nhưng **không cho phép regression** so với bản trước. Bản cuối đạt 100% (23/23) |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.0 Metrics của agent (Exercise 7.1)

Output `curl http://localhost:8002/metrics` sau 1 request `/ask` (rút gọn):

```text
http_requests_total{method="POST",route="/ask",status="200"} 1.0                        ← Counter
agent_in_flight_requests 0.0                                                             ← Gauge
http_request_duration_seconds_bucket{le="0.2",method="POST",route="/ask"} 1.0            ← Histogram
llm_cost_usd_total{model="mock-llm-small"} 4.6649999999999996e-05                        ← Counter
agent_question_length_chars_bucket{le="50.0"} 1.0                                        ← Histogram mới (7.1)
agent_question_length_chars_count 1.0
agent_question_length_chars_sum 40.0
agent_info{model="mock-llm-small",version="v2"} 1.0                                      ← Gauge "info"
```

- **Counter** (chỉ tăng, dùng với `rate()`): `http_requests_total`, `llm_cost_usd_total`, `llm_tokens_total`.
- **Gauge** (lên xuống): `agent_in_flight_requests` = số request đang xử lý (saturation).
- **Histogram** (đếm theo bucket + `_sum` + `_count`, tính percentile bằng `histogram_quantile`): `http_request_duration_seconds`; request vừa gọi mất 0.1–0.2 s nên rơi vào bucket `le="0.2"` trở lên.
- **Metric mới `agent_question_length_chars`** (Histogram, bucket 20/50/100/200/500/1000/2000, không có label): câu hỏi "Prometheus thu thập metrics như thế nào?" dài 40 ký tự → `_sum 40`, rơi vào bucket `le="50"`. Không làm label `length=...` vì độ dài có vô số giá trị.
- **Vì sao label `route` dùng `route.path` chứ không dùng URL thật:** mỗi giá trị label khác nhau là một time series riêng. Nếu dùng `request.url.path`, bot quét gọi `/wp-admin`, `/.env`, `/a1b2c3`... sẽ tạo ra hàng nghìn series mới (cardinality bùng nổ): Prometheus tốn RAM/ổ đĩa, query chậm, thậm chí bị OOM, tức là kẻ tấn công có thể đánh sập luôn hệ thống giám sát. Dùng route template thì mọi URL lạ gộp vào một giá trị `unmatched`, số series luôn nhỏ và cố định.

### 2.1 Concurrency vs latency (agent v2)

`python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json`

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.0 | 196.1 | 232.8 | 1714.1 | 0.67% (2/300) | ✅ PASS |
| 10 | 42.2 | 201.9 | 237.5 | 1674.3 | 0.67% (2/300) | ✅ PASS |
| 50 | 92.71 | 341.9 | 450.5 | 1761.7 | 1.00% (3/300) | ✅ PASS |
| 100 | 39.61 | 1630.6 | 5180.2 | 5586.7 | 1.00% (3/300) | ❌ FAIL (p95) |

**Nhận xét:**
- **1 → 10: gần như tuyến tính** (4.0 → 42.2 RPS, ×10.5) vì latency mỗi request gần như không đổi (p50 196 → 202 ms). Theo định luật Little, RPS ≈ concurrency / latency = 10 / 0.226 s ≈ 44, khớp số đo.
- **10 → 50: tăng nhưng không tuyến tính** (42.2 → 92.71, ×2.2 thay vì ×5). p50 tăng từ 202 lên 342 ms: agent chỉ có **1 process uvicorn**, mọi request dùng chung một event loop và một CPU core cho phần việc không phải "chờ LLM" (parse JSON, middleware, đo metrics, ghi log), nên bắt đầu phải xếp hàng. Vẫn đạt SLO nhưng p95 450.5 ms đã sát ngưỡng 500 ms.
- **50 → 100: bão hoà, RPS còn giảm** (92.71 → 39.61) trong khi p50 1630 ms, p95 5180 ms (gấp ~10 lần SLO). Hàng đợi dài hơn khả năng xử lý; load generator cũng chạy trên cùng máy và tranh CPU với agent. Điểm "gãy" nằm giữa 50 và 100 concurrency với một instance.
- **Error rate gần như không đổi** (~1%, đều là 503 do mock lỗi upstream) dù hệ thống đã quá tải: sự cố bão hoà chỉ lộ ra ở **latency**, không ở error rate. Vì vậy cần cả alert theo p95 chứ không chỉ alert theo lỗi.
- **p99 luôn ~1700 ms kể cả khi tải nhẹ** do ~3% request bị chậm thêm 1500 ms (`MOCK_SLOW_RATE=0.03`), xem mục 2.2.
- Muốn tăng sức chịu tải: chạy nhiều worker/instance sau load balancer (mục 5), hoặc đặt giới hạn concurrency để từ chối sớm (429/503) thay vì để hàng đợi kéo latency của mọi người lên.

### 2.2 Tail latency

Bật lại v2 với từng giá trị `MOCK_SLOW_RATE`, mỗi lần `--requests 300 --concurrency 20` (`reports/load_v2_slow_<rate>.json`):

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) | max (ms) |
|---|---|---|---|---|
| 0 | 318.4 | 632.1 | 764.3 | 783.2 |
| 0.03 (mặc định) | 263.8 | 293.6 | 1717.3 | 1744.2 |
| 0.1 | 386.7 | 1727.2 | 1759.8 | 1794.2 |

**Nhận xét:**
- **p99 nhạy nhất.** Với 3% request chậm, số request chậm (~9/300) nhiều hơn 1% nên p99 rơi đúng vào nhóm chậm: 1717 ms, trong khi p95 vẫn 294 ms vì 3% < 5%. Lên 10% thì vượt cả ngưỡng 5%, **p95 cũng nhảy lên 1727 ms** (gấp ~5.9 lần so với 0.03) và vi phạm SLO.
- **Average gần như không phản ánh:** từ 0.03 lên 0.1, avg chỉ tăng 263.8 → 386.7 ms (+47%, vẫn dưới 500 ms) trong khi cứ 10 người dùng thì 1 người phải chờ ~1.7 giây. Nếu SLO đặt theo average, sự cố này sẽ "PASS". Đó là lý do SLO đặt theo p95/p99: percentile mô tả trải nghiệm của nhóm người dùng tệ nhất, average bị số đông request nhanh kéo xuống.
- **Lưu ý về lần đo `MOCK_SLOW_RATE=0`:** không có request chậm bất thường nào (max chỉ 783 ms, so với ~1750 ms ở hai lần kia; p99 764 ms so với ~1720 ms), đúng như kỳ vọng. Tuy nhiên p50/p95 của lần này (277/632 ms) lại cao hơn lần 0.03 (222/294 ms). Đây là nhiễu môi trường: lần đo này chạy ngay sau khi agent vừa khởi động lại, agent và load generator cùng tranh CPU trên một laptop (p90 487 ms cho thấy cả phân bố bị đẩy lên, không phải vài request lẻ). Kết quả một lần chạy 300 request chưa đủ ổn định; muốn kết luận chắc chắn cần chạy lặp nhiều lần và lấy trung vị. Số trong bảng giữ nguyên như file JSON.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 57.9 | 108.7 | 116.9 |
| Tổng cost (USD) | 0.000415 | 0.000756 | 0.000818 |
| Cost / câu đúng (USD) | 0.00041535 | 0.00003599 | 0.00003557 |
| Latency p50 / p95 (ms) | 174.7 / 1491.7 | 195.7 / 1516.0 | 199.1 / 286.8 |
| Quality gate | ❌ FAIL (cần ≥ 80%) | ✅ PASS (≥ 80%, 0 regression so với v1) | ✅ PASS (≥ 100%, 0 regression so với v2) |

File: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` "JWT khác gì API key trong xác thực?" | `token`, `hết hạn`, `chữ ký` | Không có tài liệu nào trong `KNOWLEDGE_BASE` khớp từ "jwt" nên v2 rơi về câu trả lời chung chung. Thêm entry kích hoạt bằng `["jwt"]`: API key cố định, không tự hết hạn; JWT là token có chữ ký số, chứa user/role/thời điểm hết hạn |
| `scaling-03` "Kubernetes HPA scale dựa trên những chỉ số nào?" | `cpu`, `memory`, `custom metrics` | Thêm entry kích hoạt bằng `["hpa", "kubernetes", "autoscal"]`: HPA tăng giảm số pod theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi) |

Chỉ sửa `KNOWLEDGE_BASE` trong `develop/utils/mock_llm.py` (và chép sang `production/utils/mock_llm.py` cho stack Docker), không sửa `golden_dataset.jsonl`. Hai entry mới thêm ở cuối danh sách và dùng từ khoá không xuất hiện trong câu hỏi nào khác, nên không đổi tài liệu được chọn cho 21 câu đã đúng. Lệnh kiểm tra cuối:

```text
$ python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json
  Accuracy      : 100.0%  (23/23)
  ✨ Câu được cải thiện : 2
  ⚠️  Câu bị regression : 0
  Quality gate (accuracy ≥ 100%, không regression): ✅ PASS
```

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- **Chất lượng:** v1 hỏi thẳng LLM nên chỉ đúng 1/23 (câu "Deploy nghĩa là gì?" trùng câu trả lời có sẵn); v2 có bước retrieval đưa tài liệu vào prompt nên đúng 21/23, sau khi bổ sung KB đúng 23/23.
- **Chi phí:** prompt của v2 dài hơn vì kèm context → avg tokens tăng 57.9 → 108.7 (×1.9), tổng cost tăng ×1.8 (0.000415 → 0.000756 USD). Nhưng tính theo **cost / câu đúng** thì v2 rẻ hơn **~11.5 lần** (0.00041535 → 0.00003599 USD): trả tiền cho câu trả lời sai là lãng phí hoàn toàn. Đây là lý do so sánh hai phiên bản bằng cost / câu đúng chứ không bằng cost / câu.
- **Latency:** p50 tăng ~21 ms (174.7 → 195.7 ms) do thêm bước retrieval (~20 ms) và prompt dài hơn. Không đáng kể so với SLO 500 ms.
- **p95 trong eval không dùng để kết luận về latency:** chỉ 23 mẫu nên 1 request rơi vào nhóm chậm là p95 nhảy (v2 1516 ms vs v2 sau sửa 286.8 ms dù code giống hệt). Latency phải đo bằng load test (mục 2).
- Kết luận: với agent hỏi đáp kỹ thuật, v2 đáng giá: tăng ~2 lần token để đổi lấy accuracy từ 4% lên 100%.

**Quality gate trong CI (Exercise 7.3):** `run_eval.py` thoát với **exit code 1** khi accuracy < `--min-accuracy` hoặc có regression so với `--baseline`, exit code 0 khi đạt. CI coi bước có exit code ≠ 0 là fail → PR bị đánh dấu ❌, và nếu bật branch protection ("Require status checks to pass") thì không merge được. Job mẫu (GitHub Actions):

```yaml
# .github/workflows/eval.yml
name: quality-gate
on: [pull_request]
jobs:
  eval:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: 06-lab-complete/benchmark-observability/develop
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - name: Start agent v2
        run: |
          AGENT_VERSION=v2 MOCK_ERROR_RATE=0 PORT=8000 python app.py &
          for i in $(seq 1 30); do curl -sf http://localhost:8000/health && break; sleep 1; done
      - name: Eval + quality gate (fail nếu accuracy < 85% hoặc có regression)
        run: python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json --out eval_pr.json
```

`MOCK_ERROR_RATE=0` để lỗi ngẫu nhiên của mock không làm CI fail oan; `--baseline` là report của bản đang chạy trên `main` để bắt regression từng câu.

---

## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability up -d --build` trong `06-lab-complete/benchmark-observability/production/` (agent x2 + Nginx + Prometheus + Grafana + Jaeger + Locust). Tải nền cho dashboard: Locust headless 30 users (`-u 30 -r 5`).

> **Sự cố khi dựng stack:** lần đầu agent crash liên tục với `ModuleNotFoundError: No module named 'uvicorn'` (Nginx trả 502). Nguyên nhân trong `production/Dockerfile`: package được `pip install --user` rồi copy vào `/home/agent/.local`, nhưng user `agent` tạo bằng `useradd -d /app` nên có `HOME=/app`, Python tìm user site ở `/app/.local` và không thấy gì. Sửa bằng một dòng `ENV PYTHONUSERBASE=/home/agent/.local`; build lại thì agent chạy bình thường. (Lần dựng đầu còn làm WSL/Docker Desktop treo, phải khởi động lại Docker.)

### 4.0 Trả lời Exercise 7.4 (đọc dashboard)

Số liệu tại thời điểm chụp `grafana-dashboard.png` (19:08, 2 instance, 30 users), lấy bằng PromQL cùng công thức với panel:

| Chỉ số | Giá trị |
|---|---|
| Traffic `/ask` | 17.3 req/s (panel), 9.11 + 9.11 req/s theo instance |
| Error rate | 0.84% |
| p50 / **avg** / **p95** / p99 | 181 ms / **237 ms** / **296 ms** (panel 298.53 ms) / 1829 ms |
| Tokens/s | input 1433.8 · output 764.6 |
| Cost ước tính | 4.52 USD/giờ (`sum(rate(llm_cost_usd_total[5m]))*3600`) |

1. **p95 ≈ 296 ms, avg ≈ 237 ms.** Hai số gần nhau (chênh ~60 ms) nghĩa là 95% request khá đồng đều quanh 200–300 ms. Nhưng **p99 = 1829 ms** gấp ~6 lần p95: có một nhóm nhỏ (~3%, do `MOCK_SLOW_RATE=0.03`) chậm thêm ~1.5 giây. Khoảng cách avg ↔ p99 mới là tail latency; nhìn avg/p95 thì không thấy.
2. **Nginx chia rất đều:** 9.10976 vs 9.11008 req/s cho 2 instance (chênh < 0.01%), đúng round-robin (`server agent:8000 resolve`, Docker DNS trả IP cả 2 container).
3. **Input tokens/s (1434) > output tokens/s (765)** vì prompt gồm system prompt + tài liệu retrieval (context) + câu hỏi, thường dài hơn câu trả lời; agent v2 còn nhét nguyên tài liệu vào prompt. Giá output đắt hơn input 4 lần nên chi phí vẫn chia khá đều giữa hai phần.
4. **`curl http://localhost:8080/metrics` → `HTTP/1.1 404 Not Found`** (Nginx trả). Nginx cố ý chặn `/metrics` từ bên ngoài: metrics lộ thông tin nội bộ (route, số lỗi, model, chi phí) và không cần public. Prometheus vẫn scrape được vì nó gọi thẳng `agent:8000/metrics` trong network nội bộ `obs_net`, không đi qua Nginx.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals) → [screenshots/grafana-dashboard.png](screenshots/grafana-dashboard.png)
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian) → [screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`) → [screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với **2 alert cùng firing** (`AgentHighErrorRate`, `AgentHighLatencyP95`) → [screenshots/prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png) (tổng quan nhóm `agent-slo` FIRING (2): [screenshots/prometheus-alerts-overview.png](screenshots/prometheus-alerts-overview.png))

### 4.2 Thí nghiệm "phá hệ thống"

Làm **A và B cùng lúc**: sửa `.env` thành `MOCK_ERROR_RATE=0.2`, `MOCK_SLOW_RATE=0.3`, `docker compose -p k5-day14-observability up -d --scale agent=2 agent`; T0 = lúc agent khởi động lại với cấu hình lỗi (19:29:52), tải nền 30 users vẫn chạy. Một script hỏi `GET /api/v1/alerts` của Prometheus mỗi 5 giây và ghi lại lúc đổi trạng thái (`benchmark-results/alert_timeline.txt`):

```text
T0 = 19:29:52  (agent đã khởi động lại với cấu hình lỗi)
19:29:57  +5s  AgentInstanceDown → pending
19:30:19  +27s  AgentHighLatencyP95 → pending
19:30:24  +32s  AgentHighErrorRate → pending
19:31:21  +89s  AgentHighErrorRate → firing
19:32:20  +148s  AgentHighLatencyP95 → firing
🔥 AgentHighErrorRate + AgentHighLatencyP95 cùng FIRING sau 148s.
```

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (value 0.2079 ≈ 20.8% > 5%) | pending +32s → **firing +89s** | Golden Signals "Error rate" (đỏ) và "Requests/s theo status code" (đường 503 tăng) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (value 1.89 s > 1 s) | pending +27s → **firing +148s** | "Latency /ask — p50/p95/p99": p95 và p99 vọt lên ~1.9 s, avg tăng ít hơn |
| C | `LLM_MODEL=mock-llm-large` | Không làm riêng. Quan sát liên quan: `LLMCostBurnRateHigh` **đã firing ngay ở tải nền** (lần chạy đầu: firing tại +0s) vì chi phí 30 users với model nhỏ đã là 4.52 USD/giờ > ngưỡng 2 USD/giờ | – | "LLM cost USD/giờ" |
| D | `docker pause` 1 instance | Không làm | – | – |
| E | `AGENT_VERSION=v1` | Không làm (theo đề: không alert nào firing) | – | – |

**Phân tích thời gian:**
- Hai alert vào `pending` sau ~30s chứ không ngay lập tức: agent vừa khởi động lại nên `rate(...[1m])` cần vài lần scrape (5s/lần) mới có đủ dữ liệu, cộng chu kỳ đánh giá rule 5s.
- Từ `pending` → `firing` đúng bằng `for`: ErrorRate 32s → 89s (≈ 1 phút, `for: 1m`), LatencyP95 27s → 148s (≈ 2 phút, `for: 2m`). Thời gian phát hiện ≈ thời gian để rate window đầy + `for`. Đặt `for` dài hơn thì ít báo động giả nhưng phát hiện chậm hơn; `for: 0s` thì một spike vài giây cũng báo.
- `AgentInstanceDown → pending` tại +5s là nhiễu do khởi động lại container: IP cũ của agent còn trong DNS vài giây nên Prometheus scrape fail (`up == 0`), nhưng hết trước 30s (`for: 30s`) nên không firing.
- Ngưỡng `LLMCostBurnRateHigh` (2 USD/giờ) quá thấp so với tải lab 30 users (4.52 USD/giờ), nên alert này firing liên tục và trở thành nhiễu (alert fatigue). Trong production cần đặt ngưỡng theo ngân sách thật và burn rate so với ngân sách tháng.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đổi về v1 làm **chất lượng** tụt (accuracy 100% → 4.3% ở mục 3) nhưng hạ tầng vẫn khoẻ: không lỗi 5xx, latency còn thấp hơn, cost giảm. Metric hạ tầng (4 Golden Signals) không đo "câu trả lời có đúng không" nên không bắt được. Cách phát hiện:
1. **Chặn trước khi deploy:** chạy eval golden dataset trong CI với `--baseline` (quality gate mục 3); bản v1 sẽ fail và không được merge.
2. **Giám sát chất lượng khi chạy:** chạy eval định kỳ (canary/synthetic, vd mỗi 15 phút) với vài câu hỏi cố định trên production và đẩy accuracy thành metric (`agent_eval_accuracy`) để alert khi < 85%.
3. **Metric proxy cho chất lượng:** retrieval hit ratio (v1 = 0), độ dài câu trả lời, tỉ lệ câu trả lời "chung chung", tokens/request đột ngột giảm, cộng feedback người dùng (👍/👎).
4. **Theo dõi version:** panel/bảng `agent_info{version}` và alert khi version đang chạy khác version kỳ vọng.

### 4.3 Debug bằng trace

Trace chậm nhất tìm được (Jaeger, `Min Duration = 1s`), ảnh [screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png):

- `trace_id`: `f3c50e5ab6c1d32976435a2d00643a46`
- Tổng thời gian: **1717.4 ms** (Jaeger hiển thị 1.7 s; log `http_request` ghi `ms: 1717.4`)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small`** (LLM call) **1690.0 ms ≈ 98.4%** tổng thời gian; `agent.retrieve_context` chỉ 25.9 ms (≈ 1.5%). Kết luận: request chậm vì **LLM upstream** (tail latency của provider), không phải do code của agent hay bước retrieval. Cách xử lý thuộc về phía gọi LLM: timeout + retry, streaming, cache câu hỏi lặp lại, hoặc model/provider khác.
- Dòng log tương ứng (grep theo `trace_id` trong `docker compose -p k5-day14-observability logs agent`):

```json
{"ts": "2026-11-26T12:18:56.131002+00:00", "level": "INFO", "service": "observable-agent", "instance": "35ee1d82eea7", "event": "llm_call", "trace_id": "f3c50e5ab6c1d32976435a2d00643a46", "span_id": "47e23aee9702f2fe", "request_id": "2c5d8903ce22", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 84, "output_tokens": 46, "cost_usd": 4e-05, "llm_ms": 1690.0}
{"ts": "2026-11-26T12:18:56.131605+00:00", "level": "INFO", "service": "observable-agent", "instance": "35ee1d82eea7", "event": "http_request", "trace_id": "f3c50e5ab6c1d32976435a2d00643a46", "span_id": "47e23aee9702f2fe", "request_id": "2c5d8903ce22", "method": "POST", "route": "/ask", "status": 200, "ms": 1717.4}
```

Trace lỗi ([screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png)): `trace_id` `6cdd914c630dbd31725191d334a524c2`, 126.4 ms, span `chat mock-llm-small` có `error.type = upstream_unavailable` và event `exception` (`utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`, kèm stacktrace). Log cùng `trace_id`:

```json
{"ts": "2026-11-26T12:14:14.682037+00:00", "level": "INFO", "service": "observable-agent", "instance": "0847b98bd1c7", "event": "llm_error", "trace_id": "6cdd914c630dbd31725191d334a524c2", "span_id": "04739bba14ce3939", "request_id": "92a61cfd851d", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-26T12:14:14.682851+00:00", "level": "INFO", "service": "observable-agent", "instance": "0847b98bd1c7", "event": "http_request", "trace_id": "6cdd914c630dbd31725191d334a524c2", "span_id": "04739bba14ce3939", "request_id": "92a61cfd851d", "method": "POST", "route": "/ask", "status": 503, "ms": 126.4}
```

Đây là luồng 3 trụ cột liên kết: log thấy `llm_error` → lấy `trace_id` → mở đúng trace trong Jaeger → thấy bước nào lỗi và stacktrace.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

```bash
docker compose -p k5-day14-observability up -d --build --scale agent=1
docker compose -p k5-day14-observability run --rm locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_1
docker compose -p k5-day14-observability up -d --scale agent=3
docker compose -p k5-day14-observability run --rm locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_3
```

Số liệu dòng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 54.77 | 210 | 1500 | 2000 | 64 / 6604 (0.97%) |
| 3 | 57.36 | 200 | 250 | 1700 | 70 / 7115 (0.98%) |

(avg: 362 ms → 239 ms; max: 3434 ms → 1833 ms. Dòng Aggregated gồm cả `/health`: 59.97 → 63.13 req/s.)

**Giải thích kết quả:**
- **p95 cải thiện rất mạnh: 1500 ms → 250 ms (giảm ~6 lần)**, avg 362 → 239 ms. Với 1 instance (giới hạn `cpus: 0.5`), 100 users dồn vào một process nên request phải xếp hàng chờ CPU: từ p80 trở lên latency tăng vọt (p80 340 ms, p90 830 ms). Chia ra 3 instance thì hết xếp hàng, phân bố latency quay về đúng thời gian của LLM (p50–p95 chỉ 200–250 ms).
- **RPS gần như không đổi (54.77 → 57.36)** vì đây là tải "closed loop": 100 user, mỗi user nghỉ 0.5–2 giây (trung bình ~1.25 s) giữa các request, nên trần throughput ≈ 100 / (1.25 s + latency) ≈ 60–70 req/s, do **số user** quyết định chứ không do server. Muốn thấy RPS tăng theo số instance phải tăng số user cho tới khi 1 instance bão hoà.
- **p99 vẫn ~1700 ms dù có 3 instance**: ~3% request bị LLM chậm thêm ~1.5 s (`MOCK_SLOW_RATE=0.03`). Scale ngang không giúp được vì nút thắt nằm ở **LLM upstream** (đúng như trace ở mục 4.3: 98% thời gian là LLM call), không phải ở agent.
- **Failures ~1% ở cả hai lần**, toàn bộ là 503 do mock LLM lỗi ngẫu nhiên 1% (`MOCK_ERROR_RATE=0.01`), không liên quan tải. Muốn giảm cần retry/fallback ở phía gọi LLM.
- Kết luận: scale ngang giải quyết được **saturation của agent** (hàng đợi CPU), không giải quyết được **tail latency/lỗi của LLM**. Khi p95 cao, phải xem panel LLM latency / trace trước khi quyết định thêm instance.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile mới là trải nghiệm người dùng.** Ở MOCK_SLOW_RATE 0.1, avg chỉ 387 ms (vẫn "PASS" nếu SLO theo avg) trong khi p95 1727 ms; ở mục 2.1 hệ thống bão hoà làm p95 lên 5180 ms trong khi error rate vẫn ~1%. SLO phải đặt theo p95/p99 và phải theo dõi cả latency lẫn lỗi.
  2. **Eval chất lượng phải nằm trong CI, metric hạ tầng không thay được eval.** v1 và v2 đều "xanh" trên dashboard nhưng accuracy 4.3% vs 100%; cost / câu đúng mới là chỉ số so sánh đúng (v2 rẻ hơn ~11.5 lần trên mỗi câu đúng dù tốn gấp đôi token).
  3. **Metrics, traces, logs phải nối được với nhau.** Dashboard cho biết *có* vấn đề (p95 tăng), trace chỉ ra *ở đâu* (98% thời gian là LLM call, không phải code), log có `trace_id` cho biết *chuyện gì* (lỗi 503 upstream). Thiếu một mắt xích là phải đoán. Và alert cần `for` hợp lý + ngưỡng đúng ngân sách, nếu không sẽ thành nhiễu (như `LLMCostBurnRateHigh` firing liên tục ở tải lab).
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Chất lượng:** chạy eval định kỳ trên production (synthetic) và xuất `agent_eval_accuracy`; alert khi < 85%. Thêm `retrieval_hit_ratio` và feedback người dùng.
  - **SLO burn rate** (multi-window, vd 1h + 6h) thay cho ngưỡng tức thời, để alert theo tốc độ tiêu error budget.
  - **Giám sát từ bên ngoài:** synthetic check gọi URL public `/health` và `/ask` (bắt được lỗi ở Nginx/LB mà metric của agent không thấy, như 504 ở thí nghiệm D), và metric ở tầng Nginx (status 5xx/504, upstream latency).
  - **Phía LLM:** retry/timeout counter, `llm_requests_total{status="error"}` theo provider, alert khi error của provider > 5%; cost theo ngày so với ngân sách (vd > 80% budget ngày).
  - **Phía app Part 6:** số request bị 429 (rate limit) và 402 (cost guard), để biết người dùng bị chặn bao nhiêu; Redis latency và `/ready` fail.
  - **Saturation:** CPU/memory container, `agent_in_flight_requests` theo instance, alert khi gần giới hạn để autoscale trước khi p95 vọt như ở mục 2.1.
