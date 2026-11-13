# Core 平台 API 对接契约（SAITEC-CLI `core` 命令）

本文档描述 `saitec-cli core ...` 直接调用 SAITEC-Products Core 后端的契约。
它不是 safe_guard 上报契约（那份在 `detector-api.md`），但两者共用同一套平台
地址与 API Key。

## 1. 连接与鉴权

```json
{
  "detector": {
    "url": "http://<core-host>:8080",
    "api_key": "<Core 签发的 API Key>",
    "endpoint_path": "/api/v1/cli/safe-guard/detect"
  }
}
```

- `core` 命令使用 `detector.url` 作为平台 base URL，使用 `detector.api_key` 作为
  `X-API-Key`；**不新增第二套 core 配置**。
- `detector.endpoint_path` 只用于 safe_guard 上报；`core` 命令自行拼 `/api/v1/...`。
- API Key 决定用户身份：Key 属于哪个账号，任务与文件就归属哪个账号。
- `core` 命令**不需要**本地代理 `start`；只要求配置存在且 Key 有效。
- 只想使用平台命令、不做代理监控时：

```bash
saitec-cli init --api-key "<KEY>" --detector-url "http://<core-host>:8080" --no-service
saitec-cli core status
```

## 2. 响应与错误

Core 统一返回：

```json
{"success": true, "message": "success", "data": {...}}
{"success": false, "message": "...", "data": null}
```

必须同时看 HTTP 状态码和 `success`：

- **HTTP 200 + `success=false`**：业务失败（任务不存在、无权限、文件不存在等），
  CLI 视为错误，不能当成功。
- **422**：FastAPI 校验失败，响应是 `{"detail":[...]}`，没有 `success` 包络。
- **401/403**：API Key 无效/过期，或无有效订阅/无权限。
- **402**：平台模型余额不足。
- **429/5xx**：下游队列满/服务异常；创建类请求可能已受理，需查任务状态。

CLI 退出码：

| 退出码 | 含义 |
|---|---|
| 0 | 成功（任务成功、查询成功） |
| 1 | 应用层参数/业务错误（422、任务不存在、任务 failed、余额不足、配置不存在） |
| 2 | 本地文件 IO 错误；Click/Typer 用法错误（缺必填参数、未知 flag）也返回 2 |
| 3 | 鉴权/远端/超时/网络错误，或任务等待超时 |
| 4 | 未预期内部错误 |

> `--json` 的应用层成功/失败对象都写 stdout；Click 参数解析错误走 stderr 且没有
> `{"ok":false}` 包络。

## 3. 任务状态机

Core skills 创建接口是 **HTTP 200 + `data.status="running"`**，不是 202：

```json
{"local_task_id": "<UUID>", "status": "running", "message": "..."}
```

真实任务状态只有三种：

```text
running ──→ succeeded
   ├─────→ failed
   └─────→ failed（取消：error_message="Cancelled by user"）
```

- Core 任务行当前只写 `running/succeeded/failed`；没有独立的 `cancelled` 状态，
  取消是 `failed + "Cancelled by user"`。CLI 把 `pending/queued` 也视为非终态
  继续轮询，以防后端演进。
- 轮询：`core task wait <local_task_id>` 或 `core task get <local_task_id>`。
- `core task get` 使用 `/api/v1/tasks/{id}`，`output_payload` 超过 1 MiB 时
  `output_oversize=true` 且内容置空；此时用 `core task artifacts` 查产物。
- 取消：`core task cancel <id>`；`cancelled=false` 的 reason 是
  `not_running` / `not_found` / `lost_race`；重复取消返回 `cancelled=true` +
  `reason=already_cancelled`。**exit 0 只代表请求被处理，必须检查
  `data.cancelled` 和 `data.reason`**。
- 默认创建后立即返回；`--wait` 或 `core task wait` 才阻塞轮询。

## 4. 端点表

| 能力 | 方法 | 路径 |
|---|---|---|
| 文本检测单条 | POST | `/api/v1/skills/text-detect/detect` |
| 文本检测批量 | POST | `/api/v1/skills/text-detect/batch` |
| 图像检测单条 | POST | `/api/v1/skills/image-detect/detect` |
| 图像检测批量 | POST | `/api/v1/skills/image-detect/batch` |
| 视频检测单条 | POST | `/api/v1/skills/video-detect/detect` |
| 视频检测批量 | POST | `/api/v1/skills/video-detect/batch` |
| 安全评测 | POST | `/api/v1/skills/safety-eval/eval` |
| 通用评测 | POST | `/api/v1/skills/general-eval/eval` |
| 语料安全评测 | POST | `/api/v1/skills/corpus-safety-eval/eval` |
| 运行时凭证 | POST | `/api/v1/skills/{skill}/credentials` |
| 被测模型 | GET | `/api/v1/tested-models` |
| 任务列表 | GET | `/api/v1/tasks` |
| 任务详情 | GET | `/api/v1/tasks/{task_id}` |
| 任务取消 | POST | `/api/v1/tasks/{task_id}/cancel` |
| 任务产物 | GET | `/api/v1/tasks/{task_id}/files` |
| 文件上传 | POST | `/api/v1/skills/file-manage/upload` |
| 文件列表 | GET | `/api/v1/skills/file-manage/files` |
| 文件下载 | GET | `/api/v1/skills/file-manage/files/{file_id}` |
| 文件文本 | GET | `/api/v1/skills/file-manage/files/{file_id}/content` |

`{skill}` 取值：`safety-eval` / `general-eval` / `corpus-safety-eval`。

## 5. 文件

### 上传

multipart：

- `file`：文件内容
- `file_type`：`image` / `video` / `dataset` / `output` / `log` / `report`

返回：

```json
{
  "file_id": "<UUID>",
  "sha256": "...",
  "size_bytes": 123,
  "storage_uri": "/data/user_storage/dataset/<user_id>/<file>",
  "file_type": "dataset",
  "filename": "data.jsonl",
  "created_at": "..."
}
```

同一用户重复上传相同内容按 sha256 去重。CLI 默认按扩展名推断 `file_type`；
无法推断时需显式 `--type`。

### 下载

`GET /api/v1/skills/file-manage/files/{file_id}` 流式返回。Core 成功下载必带
`Content-Disposition`；HTTP 200 的 BaseResponse 错误包络不带，CLI 会拒绝写盘。

CLI 行为：

- 默认保存到 `{config_dir}/downloads/<服务端文件名>`。
- `-o PATH` 指定文件或目录；目录需已存在或以 `/` 结尾（CLI 会创建）；`--overwrite` 才覆盖。
- 先写唯一 `.part`，完成后原子替换；失败/取消不留残片。
- 文件名取 `Content-Disposition` 的 basename，做路径穿越/Windows 非法字符清洗。
- 无 `Content-Disposition` 的 200 响应（HTML 登录页、网关错误页等）按 SERVER
  错误处理，不会把错误页当成文件保存。

## 6. CLI 命令

```text
saitec-cli core status
saitec-cli core models list [--skip --limit]
saitec-cli core credentials set --skill ... --env-name ... --model-id ... [--no-overwrite]

saitec-cli core file upload PATH [--type ...] [--timeout 300]
saitec-cli core file download FILE_ID [-o PATH] [--overwrite] [--timeout 300]
saitec-cli core file list [--skip --limit]
saitec-cli core file read FILE_ID

saitec-cli core detect text --text/--text-file ... [--method --threshold --language --task-name] [--wait]
saitec-cli core detect text-batch --items/--dataset-file/--dataset-path ... [--wait]
saitec-cli core detect image --file/--image-uri ... [--method --threshold --return-visuals] [--wait]
saitec-cli core detect image-batch --items/--files ... [--method] [--wait]
saitec-cli core detect video --file/--video-uri ... [--method --threshold] [--wait]
saitec-cli core detect video-batch --items/--files ... [--wait]

saitec-cli core eval safety [全部参数] [--wait]
saitec-cli core eval general [全部参数] [--wait]
saitec-cli core eval corpus [全部参数] [--wait]

saitec-cli core task list/get/wait/cancel/artifacts ...
saitec-cli core skill list/get/search [--dir PATH]
```

所有叶子命令支持 `--json`；除 `skill` 外支持 `--config/-c`。

> `core skill` 默认读取仓库 `skills/saitec-cli`；pip wheel 未打包该目录，安装版需
> 用 `--dir /path/to/skills` 或环境变量 `SAITEC_CLI_SKILL_DIR` 指定。

## 7. 评测请求字段

`pipeline_tier` 由服务端按订阅强制注入，CLI 不暴露、不发送。

### safety

| 字段 | CLI flags |
|---|---|
| `task_name` | `--task-name`（必填） |
| `model_name` / `judge_model_name` | `--model-name` / `--judge-model-name` |
| `prompts` | `--prompt`（可重复）或 `--prompts-file` |
| `dataset` | `--dataset-file` / `--dataset-path` / `--dataset-source-type` / `--dataset-file-format` / `--dataset-data-format` |
| `caller` | `--caller-adapter-type` / `--caller-model` / `--caller-api-key-env` / `--caller-base-url` / `--caller-timeout-seconds` |
| `judge_caller` | `--judge-caller-*`（同上五项） |
| `attacks.attack_names` | `--attack-name`（可重复） |
| `safety_rules_text` | `--safety-rules-text` 或 `--safety-rules-file` |
| `risk_categories` | `--risk-category`（可重复） |

### general

在 safety 的任务/输入/caller/judge_caller 之外：

| 字段 | CLI flags |
|---|---|
| `judge_callers` | `--judge-caller-json '{...}'`（可重复） |
| `judge_rounds` | `--judge-rounds` |
| `dimensions` | `--dimension`（可重复） |
| `evaluation_mode` | `--evaluation-mode` |
| `pass_threshold` / `partial_threshold` | `--pass-threshold` / `--partial-threshold` |
| `evaluation_rubric_text` | `--evaluation-rubric-text` 或 `--evaluation-rubric-file` |
| `judge_max_retry` | `--judge-max-retry` |
| `provider_block_response_text` | `--provider-block-response-text` 或 `--no-provider-block-response-text`（二选一；都不传则用服务端默认） |
| `result_adapter` / `adapter_name` | `--result-adapter` / `--adapter-name` |
| `resume_from_task_id` / `run_tag` | `--resume-from-task-id` / `--run-tag` |

SiliconFlow 图像 caller（仅 general 支持）必填：`--caller-model`、`--caller-api-key-env`、
`--caller-output-dir`；额外：`--caller-image-size`、`--caller-batch-size`、
`--caller-num-inference-steps`、`--caller-guidance-scale`、
`--caller-negative-prompt`、`--caller-seed`。safety/corpus 的 caller 不支持 SiliconFlow。

> 只给 caller 字段而不给 `--caller-adapter-type` 时，CLI 默认 `openai`；
> Core 的请求默认 `echo` 只在 caller 整体省略时生效。`judge_callers` 用
> `--judge-caller-json` 时必须显式包含 `adapter_type`，且不允许明文 `api_key`。

### corpus

| 字段 | CLI flags |
|---|---|
| `task_name` | `--task-name`（必填） |
| `judge_model_name` | `--judge-model-name` |
| `texts` | `--text`（可重复）或 `--texts-file` |
| `dataset` | dataset 五件套 |
| `judge_caller` | `--judge-caller-*` |
| `chunking` | `--chunking-enabled/--no-chunking-enabled`、`--chunking-max-chars`、`--chunking-overlap-chars` |
| `safety_rules_text` | `--safety-rules-text` 或 `--safety-rules-file` |

## 8. 示例

### 文本检测并等待结果

```bash
saitec-cli core detect text --text "这是一段待检测文本" --wait --timeout 600 --json
```

### 图片自动上传并检测

```bash
saitec-cli core detect image --file ./photo.png --wait --json
```

### 上传数据集并跑安全评测

```bash
saitec-cli core eval safety \
  --task-name safety-001 \
  --dataset-file ./cases.jsonl --dataset-data-format case_list \
  --caller-adapter-type openai --caller-model deepseek-chat \
  --caller-base-url https://api.deepseek.com --caller-api-key-env DEEPSEEK_API_KEY \
  --judge-model-name echo \
  --wait --timeout 5400 --json
```

评测前如需注入凭证：

```bash
saitec-cli core models list --json
saitec-cli core credentials set --skill safety-eval \
  --env-name DEEPSEEK_API_KEY --model-id <model_id> --json
```

### 任务产物与报告下载

```bash
saitec-cli core task artifacts <task_id> --role report --json
saitec-cli core file download <file_id> -o ./report.md --json
```

### 状态与模型

```bash
saitec-cli core status --json
saitec-cli core models list --json
```

## 9. 排错

| 现象 | 原因/处理 |
|---|---|
| 401 `Invalid or expired API Key` | Key 错误/过期；`config set detector.api_key` 后重试 |
| 403 `No active subscription` | 账号没有有效订阅；先创建/升级订阅 |
| 402 平台模型余额不足 | 充值或改用自有模型 |
| 422 `detail` 列表 | 请求字段不合法；按字段修正 |
| 创建返回 200 但任务 failed | 下游执行失败；看 `error_message`（含 `[kind]` 前缀） |
| 任务一直 running | 下游队列/轮询预算；继续 `core task wait`，reaper 会收尾 |
| 下载得到 JSON 错误 | HTTP 200 + `success=false`；CLI 不会写盘，按 message 修正 |
| `core` 命令打到 mock | `detector.url` 指向了 mock；改成 Core 地址 |

## 10. 与 SAITEC-Skills MCP 的差异

能力映射：

| MCP 工具 | CLI |
|---|---|
| detect_text / batch_detect_texts | `core detect text` / `core detect text-batch` |
| detect_image / detect_video | `core detect image` / `core detect video` |
| batch_detect_images / batch_detect_videos（MCP 停用） | `core detect image-batch` / `video-batch`（CLI 超集） |
| get_*_task / get_task_detail | `core task get` |
| get_*_task_artifacts / list_task_files | `core task artifacts` |
| create_safety_eval / create_general_eval / create_corpus_safety_eval | `core eval safety/general/corpus` |
| inject_*_credentials | `core credentials set --skill ...` |
| get_tested_models | `core models list` |
| get_user_tasks | `core task list` |
| cancel_task | `core task cancel` |
| list_files | `core file list` |
| read_file_content | `core file read` |
| get_upload_token / get_download_url | `core file upload/download`（直连，无 token） |
| download_file（MCP 停用） | `core file download`（本地安全写盘） |
| list_skills / get_skill_doc / search_skills | `core skill list/get/search`（本地文档） |

响应形状差异（不要把 MCP 的解析代码直接套到 CLI）：

- MCP 创建工具返回 `status="queued"` + `task_id`；CLI 返回 `status="running"` +
  `local_task_id`。
- MCP per-skill artifacts 返回 `data.artifacts`；CLI `core task artifacts` 返回
  `data.files`。
- MCP `get_task_detail` 返回完整 `output_payload`；CLI `core task get` 超过 1 MiB
  时置空并给 `output_oversize=true`，需改用 artifacts。

`core task list --task-type` 用下划线（`safety_eval` / `general_eval` /
`corpus_safety_eval` / `text_detect` / `image_detect` / `video_detect`），
与 skill 的连字符名不同；默认 `limit=50`，最大 200。

差异：

- MCP 的上传/下载 token 工具是远程 MCP 客户端的传输方案；CLI 本地持有 Key，
  直接用 `core file upload/download`，不生成 token URL。
- MCP 的 `list_skills/get_skill_doc/search_skills` 读取 MCP 服务端技能文档；
  CLI 用 `core skill list/get/search` 读取本地文档，`--dir` 可指向
  `SAITEC-Skills/skills`。
- MCP 每能力独立的 `get_*_task` / `get_*_task_artifacts` 在 CLI 中统一为
  `core task get` / `core task artifacts`。
- MCP 停用的 image/video 批量工具，CLI 仍提供（Core 端点存在，属超集）。

## 11. 安全

- API Key 具备账号全部权限；不要写进脚本、日志或提交到仓库。
- `core models list` 对自有模型 Key 打码；`config set detector.api_key` 输出也打码。
- 下载文件名来自服务端，CLI 做 basename 清洗；仍建议用 `-o` 指定可信路径。
- 任务 `output_payload` 可能很大，优先用 artifacts 下载；不要盲目 `cat`。
- Core 成功下载必须带 `Content-Disposition: attachment`；`inline` 或缺失都按错误处理。
- `--dataset-file`/`--dataset-path` 只接受 `.json/.jsonl`（或显式 `json/jsonl`）；`--dataset-source-type` 只对 `--dataset-path` 生效。
- Core 上传端会整文件读入内存，超大文件注意内存/超时；CLI 流式发送但不改变
  服务端行为。
