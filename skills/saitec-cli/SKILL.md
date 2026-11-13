---
name: saitec-cli
description: 操作 saitec-cli（命令名 saitec-cli）——大模型 API 流量的反向代理监控工具。当用户提到 saitec-cli、监控 LLM API 调用、把 Claude Code/Codex/脚本的请求代理到本地端口、对接 SAITEC Core 真实检测后端、直接调用 Core 平台做检测/评测、文件上传下载、任务查询、上报流量到安全检测服务器、查检测结果（violation/clean）、或要求配置/启停/排错这个工具时，使用本 skill。含初始化（Core 的 IP/API Key/endpoint 配置）、服务增删改、启停、检测查询、定时心跳监控、排错的完整操作方法。
---

# SAITEC-Cli 操作指南（Agent 版）

反向代理监控工具：客户端把大模型 API 请求指向本地端口，saitec-cli 透明转发到真实上游，同时记录请求/响应并周期上报到检测服务器，检测结果存本地 SQLite。

```
客户端 ──→ 127.0.0.1:<port> ──→ upstream（真实端点）
              │ 记录 Record
              ▼
           JSONL 落盘 ──周期上报──→ detector（检测服务器）
              ▼
           results.db（SQLite）←── saitec-cli report 查询
```

## 三个核心概念

1. **service**：一个监控端点 = `{name, port, upstream, endpoint_type}`。可配任意多个（官方/厂商兼容口/本地模型均可）。
2. **upstream 是 URL 前缀**：完整转发地址 = `upstream + 客户端请求路径`。CLI 不改写路径。配成完整端点 URL（如 `.../v1/chat/completions`）会导致路径重复 404——CLI 会警告但不阻止。
3. **detector**：检测服务器，`POST {detector.url}{detector.endpoint_path}`（默认 `/detect`），`X-API-Key` 鉴权。对接 SAITEC Core 时 `endpoint_path` 必须是 `/api/v1/cli/safe-guard/detect`（默认 `/detect` 会 404），Key 必须是 Core 签发且属于目标账号——**Key 决定记录落库归属，必须与你要在网页端查看审计记录的账号一致**。401/403 → CLI **停止上报**直到用户重配。

## Agent 操作契约（重要）

- **一律加 `--json`**：输出为稳定 JSON（`{"ok": bool, "data": ..., "error": {code, message}}`），成功/失败都从 **stdout** 解析；stderr 用于人类提示、进度和 Click 参数错误。
- **退出码**：`0` 命令执行成功 · `1` 用户错误（应用层参数/校验）· `2` 运行时错误（未运行/端口占用/不存在）；Click/Typer 用法错误（缺必填参数、未知 flag）也返回 `2` · `3` 检测器/Core 远端错误 · `4` 内部错误。
  ⚠️ exit 0 ≠ 服务健康：`status` 在服务未运行时也返回 exit 0（`ok:true, running:false`）——**健康判断必须查 `data.running` 字段**，不能只看退出码。
- **非交互**：`init` 在**非 `--no-service`** 时必须给全 `--api-key`（≥8 字符）`--detector-url` `--upstream`；只做平台调用时用 `--no-service`（此时不要传 `--upstream`）。
- **对接 SAITEC Core 时**：`init` 还必须给 `--endpoint-path /api/v1/cli/safe-guard/detect`；init 后执行 `config set detector.timeout_sec 120`（真实 LLM 检测慢于 mock，默认 30s 会让大批次反复超时重试）。
- **`core` 命令与代理监控共用一套连接**：`detector.url` + `detector.api_key` 同时用于 safe_guard 上报和 `saitec-cli core ...`；`core` 命令不需要 `start`。只做平台调用时用 `init --api-key KEY --detector-url URL --no-service`。
- **配置路径**：默认 `~/.saitec-cli/config.json`；用环境变量 `SAITEC_CLI_CONFIG=/path/config.json` 隔离实例（测试/多套配置必备）。数据（records/logs/results.db/downloads）跟随 config 所在目录。
- **改配置不热生效**：`config set` / `service add/set/remove` 后需 `restart`（纯 core 配置不需要 restart）。
- **Windows Git Bash 陷阱**：以 `/` 开头的参数（如 `/detect`、`/api/v1/...`、core 的 `--image-uri /data/...`、`--dataset-path /data/...`）会被 MSYS 转成 `C:/Program Files/Git/...`。命令前加 `MSYS_NO_PATHCONV=1`。

## 对接 SAITEC Core（真实后端，优先）

saitec-cli 可以用本地 mock 联调，但生产监控应指向 SAITEC Core（真实 LLM 扫上下文检测 + 落库 + 网页审计）。

1. **拿 Key**：在 SAITEC 网页端（API Keys / 管理端）获取 API Key。Key 属于哪个账号，检测记录就落在哪个账号下面；网页端要用同一账号登录才能看到。
2. **初始化**（非交互，`--json` 给 Agent 解析）：

   ```bash
   export SAITEC_CLI_CONFIG=$WORK_DIR/config.json
   saitec-cli init \
       --api-key "$CORE_API_KEY" \
       --detector-url "http://<core-host>:8080" \
       --endpoint-path "/api/v1/cli/safe-guard/detect" \
       --upstream "https://api.deepseek.com" \
       --json
   saitec-cli config set detector.timeout_sec 120 --json
   ```

   `detector.url` 只写 `scheme://host:port`，不要带路径（路径由 `endpoint_path` 拼）。
3. **启动并接客户端**：`saitec-cli validate --json && saitec-cli start --json`；把 Claude Code / Codex / 脚本的 base_url 指到 start 输出里的 `data.client_hint`（如 `http://127.0.0.1:9001`）。
4. **确认上报链路**：
   - CLI 侧：`status --json` 看 `data.running`；发一条真实请求，等一个上报周期后 `report --json` 应出现记录。
   - Core 侧：`curl -H "X-API-Key: $CORE_API_KEY" http://<core-host>:8080/api/v1/cli/safe-guard/status`；也可用 `GET /api/v1/cli/safe-guard/records?limit=20` 查最近记录。
5. **在网页端分析**：登录同一账号 → 安全审计页查看/筛选/删除；或直接在网页 Agent 里说“分析我最近 7 天的 LLM 网关安全审计，给出风险分布和处置建议”（Agent 会调用 `query_audit_records` / `get_audit_record`）。
6. **失败语义**：401/403 = Key 无效或不属于当前 Core，CLI 停止上报（记录不丢，JSONL 会续传），修 Key 后 `restart`；404 = `endpoint_path` 错；超时 = `detector.timeout_sec` 太小或 Core 端 LLM 过载。
7. **隐私**：默认上报完整对话原文并落库；敏感环境可 `service set NAME --no-record-body` 只上报元数据骨架（Core 无法审内容，检测质量下降）。

> mock 仅用于本地开发联调（见 [references/operations.md](references/operations.md) §2）；`mock-test-key` 只能对 mock 用，对 Core 一律 401。

## 直接调用 Core 平台（core 命令）

除了本地代理监控，CLI 也可以像 MCP 一样直接调 Core（同一套 `detector.url` / `detector.api_key`）：

```bash
saitec-cli core status --json
saitec-cli core models list --json
saitec-cli core detect text --text "待检测文本" --wait --json
saitec-cli core detect image --file ./x.png --wait --json
saitec-cli core eval safety --task-name t --prompt "..." --wait --json
saitec-cli core file upload ./data.jsonl --type dataset --json
saitec-cli core task list --json
saitec-cli core task wait <task_id> --json
saitec-cli core task artifacts <task_id> --role report --json
saitec-cli core file download <file_id> -o ./report.md --json
saitec-cli core skill list
```

命令分组：`core status|models|credentials|file|detect|eval|task|skill`。

- 创建类默认只返回 `local_task_id`；加 `--wait` 或 `core task wait` 才轮询。
- 任务状态只有 `running/succeeded/failed`；取消 = `failed + "Cancelled by user"`。
- 文件上传返回的 `storage_uri` 可直接用于 `--image-uri` / `--video-uri` / `--dataset-path`。
- `core eval` 的所有请求字段都展开为 flags；`judge_callers` 用可重复 `--judge-caller-json '{...}'`。
- 完整契约（端点/字段/错误码）见 `../../docs/integration/core-api.md` 与 [references/operations.md](references/operations.md) §6。

## 命令速查

```bash
# 生命周期
saitec-cli init --api-key KEY --detector-url URL --upstream URL [--endpoint-type T] [--name N] [--port P] [--endpoint-path P] [--force]
                                       # Core 真实后端（推荐）：
                                       #   --detector-url http://<core-host>:8080
                                       #   --endpoint-path /api/v1/cli/safe-guard/detect
                                       #   随后 config set detector.timeout_sec 120
saitec-cli start [--report-interval N] [--batch-size N]   # 后台子进程 + PID 文件
saitec-cli monitor [--report-interval N]                  # 前台实时监控（人盯）：异常彩色输出，Ctrl+C 退出
saitec-cli stop [--timeout N]
saitec-cli restart
saitec-cli status --json        # running/pid/services/最近日志
saitec-cli doctor --json        # 自检（config/端口/磁盘/SQLite/JSONL/api_key）

# 监控服务管理
saitec-cli service list --json
saitec-cli service add NAME --upstream URL [--endpoint-type T] [--port N]
saitec-cli service set NAME [--upstream URL] [--port N] [--endpoint-type T] [--record-body B]
saitec-cli service remove NAME

# 配置
saitec-cli config get KEY [--json]          # api_key 自动脱敏
saitec-cli config set KEY VALUE [--json]    # 原子写入：校验失败不落盘，自动备份
saitec-cli config unset KEY
saitec-cli config list --json
saitec-cli validate

# 检测结果与运维
saitec-cli report [--since 1h|30m|7d|ISO8601] [--service NAME] [--limit N] --json
saitec-cli export [-f md|html] [-o PATH] [--since 7d] [--status suspicious,violation,error|all] --json
                                       # 导出报告（默认只导异常；含完整对话，从 JSONL 关联原文）
saitec-cli redo RECORD_ID --json            # 重报单条（支持 UUID 前缀；歧义时列候选）
saitec-cli purge [--retention-days N] [--dry-run]   # 清 JSONL/日志备份/SQLite
saitec-cli logs --tail N [--service NAME]
saitec-cli tail [--service NAME]           # 实时跟踪当日 records JSONL 新增行（每行一条 Record）

# 直接调用 Core 平台（与 safe_guard 共用 detector.url/api_key）
saitec-cli core status --json              # 健康 + 鉴权 + 任务数
saitec-cli core models list --json
saitec-cli core detect text --text "..." [--wait] --json
saitec-cli core detect image --file ./x.png [--wait] --json
saitec-cli core eval safety|general|corpus [全部参数] [--wait] --json
saitec-cli core file upload PATH [--type image|video|dataset|output|log|report] --json
saitec-cli core file download FILE_ID [-o PATH] [--overwrite] --json
saitec-cli core task list|get|wait|cancel|artifacts ... --json
saitec-cli core skill list|get|search [--dir PATH] --json
```

`--endpoint-type` 枚举：`openai-chat-completions` / `openai-responses` / `anthropic-messages`。缺省按 upstream URL 猜测（含 `anthropic` → anthropic-messages，否则 openai-chat-completions），输出里有 `endpoint_type_guessed` 标记。

## 常见任务

### 1a. 接入 SAITEC Core 做 LLM 网关监控（推荐）

```bash
export SAITEC_CLI_CONFIG=$WORK_DIR/config.json
saitec-cli init --api-key "$CORE_API_KEY" \
    --detector-url "http://<core-host>:8080" \
    --endpoint-path "/api/v1/cli/safe-guard/detect" \
    --upstream "https://api.deepseek.com/anthropic" --json
saitec-cli config set detector.timeout_sec 120 --json
saitec-cli validate --json && saitec-cli start --json
```

start 输出的 `data.client_hint` 给出各服务客户端应配的 base_url（如 `ANTHROPIC_BASE_URL=http://127.0.0.1:9001`）。

可选验证（真实流量走一遍代理）：`curl -s -X POST http://127.0.0.1:<port>/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"x","messages":[]}'`——上游不通也返回 502/401，但记录已生成，等一个上报周期后 `report --json` 可见（证明记录-上报链路通）。Core 侧用 `GET /api/v1/cli/safe-guard/records` 或网页安全审计页核对同一条记录。

### 1b. 本地 mock 联调（仅开发）

```bash
# 另开进程：uvicorn server:app --app-dir tests/mock_detector --port 8000
saitec-cli init --api-key mock-test-key --detector-url http://127.0.0.1:8000 \
    --upstream "https://api.deepseek.com" --json
saitec-cli start --json
```

mock 只用于验证代理/记录/上报链路，不代表真实检测结论；生产必须切回 Core（见 1a）。

### 2. 加 / 改 / 删监控端点

```bash
saitec-cli service add local-llm --upstream http://localhost:23333 --port 9010 --json
saitec-cli service set local-llm --upstream http://localhost:11434 --json
saitec-cli service remove local-llm --json
saitec-cli restart --json
```

`add` 不给 `--port` 时自动从 9001 起分配空闲端口；name 重复报 `NAME_EXISTS`。

### 3. 查检测结果（violation 优先）

```bash
saitec-cli report --since 1h --json     # data.results[]: record_id/detection_status/risk_level/model/detail
saitec-cli report --since 7d --limit 500 --json | jq '[.data.results[] | select(.detection_status=="violation")]'
```

`detection_status`：`clean` / `suspicious` / `violation` / `error`。`error` 表示检测服务器自身失败（detail.reason 有原因），不是流量违规。

> 以上 `report` 查的是 CLI 本地 SQLite（只含已上报并回写的结论）。云端/全量记录用 Core 的 `GET /api/v1/cli/safe-guard/records`（X-API-Key，仅本人）或网页安全审计页；网页 Agent 也能直接查（`query_audit_records` / `get_audit_record`）。

### 4. 心跳定时监控汇报

单次检查（适合被宿主调度器/cron/Monitor 周期调用）：

```bash
saitec-cli status --json && saitec-cli report --since 10m --limit 500 --json
```

要点：
- **查 `data.running` 字段**判断存活（exit 0 不代表在跑）
- **`--limit 500`**：report 默认 100 会截断高流量窗口，监控场景必须显式调大
- 汇报优先级：服务掉线 > violation（含 `detail.reason`）> `detection_status=error`（detector 自身故障）
- **盲区兜底**：detector 401 后 CLI 故意停摆，此时 `running=true` 且 report 无 error——健康巡检时可加 `saitec-cli logs --tail 50` 检查有无 `X-API-Key 失效`
- **Core 侧健康**：`curl -H "X-API-Key: $KEY" http://<core-host>:8080/api/v1/cli/safe-guard/status`；401/403 说明 Key 失效或不属于该 Core
- 周期建议 ≥ detector 的 `report_interval_sec`（默认 60s），否则看到的总是旧数据；持久的宿主级定时任务用 Claude Code 的 cron/Monitor 机制做，本 CLI 自身不提供调度
- 人盯实时场景用 `saitec-cli monitor`（前台进程 + 事件驱动彩色输出，与 start 互斥）——它给人类看，Agent 巡检仍用上面的 JSON 组合

### 4.5 导出异常报告留档

发现异常后把可读报告交付给人（审计/转发/归档）：

```bash
saitec-cli export -f html -o violation-report.html --since 24h    # 人看/打印转 PDF
saitec-cli export --json --since 24h                              # Agent 拿摘要（count/by_status/output_path）
```

要点：
- **默认只导 suspicious/violation/error**（不含 clean）——想全量存档才显式 `--status all`
- 报告含**完整对话**，属敏感文件：放进用户指定目录，不要主动推到外部服务；JSONL 已 purge 的条目自动标"仅结论"
- `-o` 缺省落当前目录、文件名带时间戳——Agent 调用时总是显式给 `-o` 避免散落文件

### 5. 上传文件并做图像/视频检测

```bash
saitec-cli core file upload ./photo.png --type image --json
# 取 data.storage_uri
saitec-cli core detect image --image-uri <storage_uri> --wait --json

# 或一步到位（--file 自动上传）：
saitec-cli core detect image --file ./photo.png --wait --json
saitec-cli core detect video --file ./clip.mp4 --wait --json
```

### 6. 跑 LLM 评测（上传 dataset → 注入凭证 → 提交 → 等待）

```bash
saitec-cli core models list --json
saitec-cli core credentials set --skill safety-eval \
  --env-name DEEPSEEK_API_KEY --model-id <model_id> --json
saitec-cli core eval safety --task-name safety-001 \
  --dataset-file ./cases.jsonl --dataset-data-format case_list \
  --caller-adapter-type openai --caller-model deepseek-chat \
  --caller-base-url https://api.deepseek.com \
  --caller-api-key-env DEEPSEEK_API_KEY \
  --judge-model-name echo --wait --timeout 5400 --json
```

`--prompt` / `--prompts-file` 与 dataset 二选一；`pipeline_tier` 不传（服务端按订阅注入）。
general/corpus 的参数见 `../../docs/integration/core-api.md` §7。

### 7. 查任务、产物、下载报告

```bash
saitec-cli core task list --json
saitec-cli core task get <task_id> --json
saitec-cli core task wait <task_id> --json
saitec-cli core task artifacts <task_id> --role report --json
saitec-cli core file download <file_id> -o ./report.md --json
```

### 8. 排错决策树

1. `saitec-cli doctor --json` —— `port:*` fail = 端口冲突；`config` fail = 配置坏
2. `status` 未运行但怀疑有残留 —— 直接 `start`：CLI 对死 PID 自动覆盖（stop 也自带 STALE_PID 自愈清理）。真正报 `ALREADY_RUNNING` 说明 PID 确实活着，是另一个实例在跑（可能用了不同 SAITEC_CLI_CONFIG），先找到它再决定停谁
3. 日志出现 `X-API-Key 失效` → detector 的 api_key 不匹配，重 `init --force` 或 `config set detector.api_key`
4. `report` 空结果 → 时间窗口不对（扩大 `--since`）或上报周期未到（等 60s）
5. 更细排错（SQLite 损坏/GBK 乱码/续传机制）见 [references/operations.md](references/operations.md)

## 关键陷阱汇总

| 陷阱 | 规避 |
|------|------|
| upstream 配成完整端点 URL → 路径重复 404 | upstream 配到"客户端请求路径之前"为止（如 `http://localhost:23333` 而非 `.../v1/chat/completions`） |
| 配 Core 却用默认 `/detect` → 404 | 必须 `--endpoint-path /api/v1/cli/safe-guard/detect` |
| `detector.url` 带了路径 → 重复路径 404 | url 只写 `scheme://host:port`，路径放 `endpoint_path` |
| 真实 LLM 检测超时 → CLI 反复重试 | `config set detector.timeout_sec 120`（默认 30s 偏小） |
| 用 `mock-test-key` 打 Core → 401 | Core 只认平台签发的 Key；mock key 仅对本地 mock 有效 |
| 网页端看不到审计记录 | CLI 的 Key 必须属于网页登录账号（Key 决定落库归属） |
| 关闭 record_body 后检测质量下降 | 默认上报完整对话；`--no-record-body` 只留骨架，Core 无法审内容 |
| `core` 命令打到 mock | `detector.url` 指向了 mock；`config set detector.url http://<core-host>:8080` |
| 创建返回 200 但任务 failed | 下游执行失败；看 `error_message`（可能含 `[kind]`），不是提交失败 |
| `core models list` 的 Key 是 `sk-***` | CLI 对自有模型 Key 打码；需要明文请从网页端管理 |
| `--wait` 超时 exit 3 | 任务仍在跑；继续 `core task wait <id>` 或稍后 `core task get` |
| 下载得到 JSON 而不是文件 | HTTP 200 + `success=false`；CLI 已拒绝写盘，按 message 修正 |
| `core file upload` 报无法推断类型 | 用 `--type image|video|dataset|output|log|report` 显式指定 |
| Git Bash 把 `/detect` 等参数转成 Windows 路径 | 命令前缀 `MSYS_NO_PATHCONV=1` |
| detector 401 后上报停摆 | 日志会写明；重配 key 后 `restart` 恢复 |
| 改配置没生效 | `restart` |
| 监控需要真实模型 key | saitec-cli 不做鉴权，`Authorization` 头由客户端自带透传；测试用 mock（见 references） |

## 深入阅读

- [references/operations.md](references/operations.md) —— 排错手册、真实 Core 接入与 mock 联调、Core API 操作细节、redo/purge、配置体系
- `../../docs/integration/core-api.md` —— `core` 命令完整契约（端点/字段/任务状态机/文件/错误码/示例）
- `../../docs/integration/detector-api.md` §8.1 —— SAITEC Core `safe_guard` 生产实现与对接契约
- 云端审计：用户侧 `GET /api/v1/cli/safe-guard/records`、网页安全审计页、网页 Agent 的 `query_audit_records` / `get_audit_record`
- 项目内文档：`../../docs/user-guide.md`（人类视角手册）
