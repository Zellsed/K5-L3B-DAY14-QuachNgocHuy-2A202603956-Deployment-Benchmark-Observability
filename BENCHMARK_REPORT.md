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

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...
