# SAITEC-Cli 深入操作参考

SKILL.md 覆盖高频操作；本文件是排错手册与低频操作细节。按需阅读对应小节。

## 目录
1. [完整排错手册](#1-完整排错手册)
2. [真实检测服务器接入（SAITEC Core）与 mock 联调](#2-真实检测服务器接入saitec-core与-mock-联调)
3. [redo / purge / 日志](#3-redo--purge--日志)
4. [detector 对接契约摘要](#4-detector-对接契约摘要)
5. [配置体系细节](#5-配置体系细节)
6. [Core 平台直接调用（core 命令）](#6-core-平台直接调用core-命令)

---

## 1. 完整排错手册

### start 后 status 显示未运行
看 `{config_dir}/logs/saitec-cli.log` 尾部。最常见：端口被占（`Errno 10048`）——另一个 saitec-cli 实例还在（可能用了不同 SAITEC_CLI_CONFIG），`stop` 它或给新实例换端口。

### ALREADY_RUNNING 但实际没在跑
PID 文件残留（进程死了没清）。删 `{config_dir}/saitec-cli.pid` 后重试。`stop` 命令自身也能清理（报 STALE_PID 并自动删）。

### detector 401 / 上报停摆
日志出现 `X-API-Key 失效，停止上报：auth failed (401) at <url>`。这是**故意停摆**（避免无限重试锁死账号）。修复：

```bash
saitec-cli config set detector.api_key NEW_KEY --json
saitec-cli restart --json
```

未上报的记录不会丢：JSONL 落盘先于上报，重启后 `_replay_unreported` 按游标自动续传。

### detector 5xx / 不可达
指数退避重试（2s→4s→…→上限 60s），pending 批保留不丢。恢复后自动补报。无需干预，除非长时间不可达——查 `saitec-cli logs --tail 50` 的 `report failed (kind=SERVER)`。

### report 空结果三连查
1. 时间窗口：`--since 30m` 改 `--since 7d`
2. 上报周期：默认 60s，刚发的请求等一个周期
3. 根本没记录：`status --json` 确认 running，客户端确实把请求发到了本地端口（检查 base_url）

### SQLite 损坏（doctor 报 sqlite fail）
磁盘满或权限问题居多。**不要直接删 results.db**（丢历史）。备份后重命名让 CLI 重建：

```bash
mv {config_dir}/results.db {config_dir}/results.db.corrupt
saitec-cli restart --json
```

JSONL 是完整原始记录，历史可从 JSONL 重报（`redo`）恢复。

### Windows 中文乱码
CLI 在 pipe 模式已强制 UTF-8。若 Agent 捕获到乱码，检查是否走了老 cmd（`chcp 65001`）或输出编码被 shell 二次转换。`--json` 输出不受影响（UTF-8 JSON）。

### 端口被谁占了

```bash
netstat -ano | grep ":9001" | grep LISTENING    # 拿 PID
tasklist //FI "PID eq <pid>"                     # Windows 看进程名
```

---

## 2. 真实检测服务器接入（SAITEC Core）与 mock 联调

### 2.1 SAITEC Core（生产推荐）

Core 平台的 `safe_guard` 按本契约实现（LLM 审上下文 + 同会话前缀去重 + 落库），端点是 `/api/v1/cli/safe-guard/detect`，不是默认 `/detect`。

```bash
export SAITEC_CLI_CONFIG=$WORK_DIR/config.json
saitec-cli init --api-key "<Core签发的Key>" \
    --detector-url "http://<core-host>:8080" \
    --endpoint-path "/api/v1/cli/safe-guard/detect" \
    --upstream "<要监控的上游>" --json
saitec-cli config set detector.timeout_sec 120 --json
saitec-cli validate --json && saitec-cli start --json
```

要点：

- Key 必须是 Core 签发且属于目标账号（检测记录按 Key 归属落库，网页端要用同一账号登录才能看到）；`mock-test-key` 对 Core 无效，401 后 CLI 停止上报。
- `detector.url` 只写 `scheme://host:port`，路径由 `endpoint_path` 拼；写成完整 URL 会路径重复 404。
- 真实 LLM 检测慢于 mock：默认 `timeout_sec=30` 对大批次偏小，建议 120（CLI 单批超时 = 该值；Core 侧单批 LLM deadline 默认 25s）。
- 验证连通：`curl -H "X-API-Key: <key>" http://<core-host>:8080/api/v1/cli/safe-guard/status`。
- 查云端记录：`curl -H "X-API-Key: <key>" "http://<core-host>:8080/api/v1/cli/safe-guard/records?limit=20&status=violation"`；详情 `GET .../records/<record_id>` 含对话原文；网页端安全审计页同源；网页 Agent 可用 `query_audit_records` / `get_audit_record`。

常见失败：

| 现象 | 原因 / 处理 |
|---|---|
| 404 at `.../detect` | 漏了 `--endpoint-path /api/v1/cli/safe-guard/detect` |
| 401/403，停止上报 | Key 错或不属于该 Core；`config set detector.api_key` 后 `restart` |
| 超时 / 反复重试 | 调大 `detector.timeout_sec`，并检查 Core 端 LLM 可用性 |
| 网页看不到记录 | CLI 的 Key 与网页登录账号不是同一个用户 |

### 2.2 本地 mock detector（仅开发/联调）

无真实检测服务器时，用仓库自带 mock（`tests/mock_detector/`）：

```bash
# random 模式：5% 概率随机标 violation（无需配置）
uvicorn server:app --app-dir tests/mock_detector --host 127.0.0.1 --port 8000 &

# llm 模式：真实大模型判定（tests/mock_detector/.env 配 MOCK_LLM_API_KEY 等）
MOCK_DETECTION_MODE=llm uvicorn server:app --app-dir tests/mock_detector --host 127.0.0.1 --port 8000 &
```

接入：`saitec-cli init --api-key mock-test-key --detector-url http://127.0.0.1:8000 --upstream <端点>`。

验证上报到达：`curl -s http://127.0.0.1:8000/records | python -m json.tool`（mock 内存里的记录）。

> mock 返回裸 `results` 格式，Core 返回 BaseResponse 包装格式，CLI 两种都兼容；但 mock 的判定是随机的，不能当作真实检测结论。

联调脚本：`python tests/test_chat/chat_probe.py [N]`（经代理发 N 条混合内容消息；配置在 `tests/test_chat/.env`）。

---

## 3. redo / purge / 日志

### redo（重报单条）

```bash
saitec-cli report --since 7d --json | jq -r '.data.results[0].record_id'   # 拿全量 record_id
saitec-cli redo <record_id> --json
```

用途：detector 规则更新后重新评估历史记录。record_id 从 JSONL（`{config_dir}/records/records-*.jsonl`）或 report 查。

### purge（清理）

```bash
saitec-cli purge --retention-days 30 --dry-run --json   # 预览
saitec-cli purge --retention-days 30 --json             # 执行
```

清理三类：超期 JSONL、日志切割备份（`saitec-cli.log.YYYY-MM-DD`）、SQLite 超期行。活跃日志文件和未超期数据不动。

### 日志

- 文件：`{config_dir}/logs/saitec-cli.log`（每日午夜切割，自动保留 14 天）
- CLI：`saitec-cli logs --tail 100 [--service NAME]`（子串过滤）
- 上报循环的关键日志行：`runtime started` / `report failed (kind=...)` / `X-API-Key 失效` / `runtime stopped`

### tail（实时跟新记录）

`saitec-cli tail [--service NAME]`：从启动时刻起跟踪当日 `records-*.jsonl` 的**新增行**（跳过历史），每行一条 Record JSON。首行 `# tailing <file>` 是注释，解析时跳过 `#` 开头行；Ctrl+C 退出。适合"实时感知新流量"（Agent 挂后台跟读）；violation 结论仍滞后一个上报周期，结论查询用 `report`。注意 `--level` 过滤匹配的 `level` 字段在 Record 中不存在（对 records 流无效）。

---

## 4. detector 对接契约摘要

完整契约见 `docs/integration/detector-api.md`。Agent 视角要点：

- 请求：`POST {url}{endpoint_path}`，头 `X-API-Key`，体 `{"batch": [Record...]}`（Record 含完整归一化 request/response）
- 响应：裸格式 `{"results": [{"record_id", "detection_status", "risk_level", "detection_detail", "detected_at"}]}`；SAITEC Core 用 BaseResponse 包装 `{"success": true, "message": ..., "data": {"results": [...]}}`，CLI 两种格式都兼容
- record_id 必须回带；未知 id 被忽略；幂等（重复上报安全）
- `detection_status=error` 表示检测器自身失败（`detection_detail.reason` 有原因），不是流量违规
- 401/403 → CLI 停止上报；其他 4xx/5xx/超时 → 重试退避
- 服务端单批 LLM deadline 默认 25s（Core env `SAFEGUARD_LLM_DEADLINE_SEC`）；CLI `detector.timeout_sec` 应大于它（建议 120），否则大批次会被 CLI 先超时
- Core 运行状态：`GET /api/v1/cli/safe-guard/status`（同一 `X-API-Key` 鉴权，返回前缀去重缓存统计）

---

## 5. 配置体系细节

### 三级优先级
CLI 参数 > 环境变量（`SAITEC_CLI_*`） > config.json。

常用环境变量：`SAITEC_CLI_CONFIG`（配置路径）· `SAITEC_CLI_API_KEY` · `SAITEC_CLI_DETECTOR_URL` · `SAITEC_CLI_ENDPOINT_PATH` · `SAITEC_CLI_REPORT_INTERVAL` · `SAITEC_CLI_BATCH_SIZE` · `SAITEC_CLI_MAX_QUEUE_SIZE` · `SAITEC_CLI_TIMEOUT_SEC` · `SAITEC_CLI_LOG_LEVEL` · `SAITEC_CLI_<SERVICE_NAME>_UPSTREAM/PORT`（service 级覆盖）。

### config.json 结构（dot-path 操作对象）

```json
{
  "detector": {"url", "api_key", "endpoint_path", "report_interval_sec", "batch_size", "max_queue_size", "timeout_sec"},
  "services": [{"name", "port", "upstream", "endpoint_type", "record_body"}],
  "log_level": "INFO"
}
```

`config set` 走原子链：备份（`config.json.bak.<时间戳>`）→ 校验（失败不写入）→ 落盘。api_key 在 get / set / list 输出中自动脱敏（`***`）。

> `detector` 是历史命名，实际是**平台连接**：`detector.url` + `detector.api_key`
> 同时用于 safe_guard 上报和 `saitec-cli core ...` 全部命令。

### 数据文件布局（跟随 config 目录）

```
{config_dir}/
├── config.json
├── saitec-cli.pid            # 运行时 PID
├── records/*.jsonl           # 原始记录（按天分片，崩溃恢复源）
├── results.db                # 检测结果（SQLite WAL）
├── downloads/                # core file download 默认目录
└── logs/saitec-cli.log[.YYYY-MM-DD]
```

## 6. Core 平台直接调用（core 命令）

`core` 命令直接请求 Core `/api/v1/skills/*` 与 `/api/v1/tasks*`，不需要本地代理
`start`。完整契约见 `docs/integration/core-api.md`。

### 连接与状态

```bash
saitec-cli core status --json
saitec-cli core models list --json
```

- 使用 `detector.url` + `detector.api_key`；只做平台调用时 `init --no-service`。
- `core status` 输出 `health_ok` / `auth_ok` / `task_count`；鉴权失败 exit 3。
- `core models list` 对自有模型 `api_key` 打码；平台模型显示 `unit_price_amount`
  （分/百次调用）。

### 创建任务与等待

```bash
saitec-cli core detect text --text "..." --json            # 立即返回 local_task_id
saitec-cli core detect text --text "..." --wait --json     # 轮询到终态
saitec-cli core task wait <task_id> --interval 5 --timeout 3600 --json
saitec-cli core task get <task_id> --json
saitec-cli core task cancel <task_id> --json
```

- 创建是 HTTP 200 + `status="running"`；状态只有 `running/succeeded/failed`。
- 取消 = `failed + error_message="Cancelled by user"`；`cancelled=false` 时看 `reason`。
- `task wait`：成功 exit 0，failed exit 1，超时 exit 3；进度写 stderr。
- `task get` 的 `output_payload` 超 1 MiB 时 `output_oversize=true`，改用 artifacts。

### 文件

```bash
saitec-cli core file upload ./data.jsonl --type dataset --json
saitec-cli core file list --json
saitec-cli core file read <file_id> --json
saitec-cli core file download <file_id> -o ./downloads/ --overwrite --json
```

- 上传 multipart 字段 `file` + `file_type`；类型 `image/video/dataset/output/log/report`。
- 返回 `storage_uri` 可用于 `--image-uri` / `--video-uri` / `--dataset-path`。
- 下载默认落 `{config_dir}/downloads/`，先 `.part` 后原子替换；失败不写盘。
- 下载响应可能是 HTTP 200 + JSON `success=false`，CLI 会识别并报错。

### 检测 / 评测

```bash
saitec-cli core detect image --file ./x.png --wait --json
saitec-cli core detect video --file ./x.mp4 --wait --json
saitec-cli core eval safety --task-name t --prompt "..." --wait --json
saitec-cli core eval general --task-name t --prompt "..." --judge-model-name echo --wait --json
saitec-cli core eval corpus --task-name t --text "..." --wait --json
```

- 检测/评测的字段、默认值、互斥关系见 `docs/integration/core-api.md` §7。
- `pipeline_tier` 不暴露；服务端按订阅注入。
- 评测前用 `core credentials set` 注入 `model_id` 对应凭证。

### 任务产物

```bash
saitec-cli core task artifacts <task_id> --role report --json
saitec-cli core file download <file_id> -o ./report.md --json
```

### 技能文档

```bash
saitec-cli core skill list
saitec-cli core skill get operations
saitec-cli core skill search "wait"
saitec-cli core skill list --dir /path/to/SAITEC-Skills/skills
```

### 常见错误

| 现象 | 处理 |
|---|---|
| 401 | Key 错误/过期；`config set detector.api_key` |
| 403 | 账号无有效订阅；先创建/升级订阅 |
| 402 | 平台模型余额不足；充值或改自有模型 |
| 422 | 请求字段错误；按 `detail` 修正 |
| 200 + `success=false` | 业务失败；CLI 已按失败处理，看 message |
| 创建成功但任务 failed | 下游失败；看 `error_message` |
| `--wait` 超时 | 任务仍在跑；继续 `core task wait` |
