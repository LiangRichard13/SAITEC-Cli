# PROGRESS.md — 项目进度

> 倒序时间线。格式约定见 [AGENTS.md](AGENTS.md) §5。接手 Agent 先读本文件建立上下文。
> 条目与 commit 一一对应；`教训:` 行是防重踩的关键。
>
> **命名变更**：2026-11-11 起项目更名 SAITEC-Cli；下方历史条目中的 `ssgc` / `saitec-safe-guard-cli` 是当时的真实记录，保留原貌。

## 2026-11-12

### feat: core 子命令直连 Core 平台（检测/评测/文件/任务） （待提交）
新增 `saitec-cli core` 命令组，直接调用 Core `/api/v1/skills/*`、`/api/v1/tasks*`、
`/api/v1/tested-models` 与 file-manage 接口，覆盖 MCP 的检测/评测/文件/任务/模型/
凭证能力；连接复用 `detector.url` + `detector.api_key`，不引入第二套 base_url/Key。
新增 L2 `core_api/`（aiohttp、BaseResponse 解包、multipart、流式下载、错误分类）、
L5 `runtime/core_ops.py`（任务等待/取消/产物、上传→检测/评测、dataset 构造）、
L6 `cli/commands/core/`（status/models/credentials/file/detect/eval/task/skill）。
评测请求全部字段展开为 flags；`judge_callers` 用可重复 `--judge-caller-json`。
`init --no-service` 支持只配置平台连接；`config set detector.api_key` 输出改为脱敏。
经 4 个子代理两轮审查，修复下载路径/中文 filename*、网络错误分类、rich markup、restart/monitor 空 services、dataset/judge/shape 校验等；验证：全量 pytest 390 passed（新增 122）；真实 Key 只读 E2E 通过（status/models/
task list/get/wait/cancel/artifacts/file list/read/download），文件上传→列表→读取→
下载→REST 删除 round-trip 字节一致；未创建检测/评测任务。
教训: Core 业务失败大量是 HTTP 200 + `success=false`，只看状态码会把"任务/文件不存在"
当成功；任务取消是 `failed + "Cancelled by user"`，不是独立状态。

### docs: Core API 契约 + skill/用户文档同步 （待提交）
新增 `docs/integration/core-api.md`（端点/任务状态机/文件/评测字段/错误码/示例）；
SKILL.md 增 core 命令速查与 3 个 recipe；references/operations.md 增 §6；
README/user-guide/AGENTS/设计文档/PROGRESS 同步。真实 E2E 输出均用 `--json` 保存到
`/tmp/core-smoke`，扫描确认无 API Key 明文。

### feat: reporter 响应双格式解析 + detector.timeout_sec 可配 + init --endpoint-path <f6c7a21>
对接 SAITEC-Products Core 新落的真实检测接口（`POST /api/v1/cli/safe-guard/detect`，检测逻辑即 mock llm 模式的移植，BaseResponse 包装 `data.results`）：`_parse_response` 双格式兼容——顶层 `results` 缺失时解包 `data.results`，mock 裸格式与旧服务端不破坏；新配置字段 `detector.timeout_sec`（默认 30，env `SAITEC_CLI_TIMEOUT_SEC`，models/config/config_cmd/init 四处同步），reporter 上报超时改读配置；init 增 `--endpoint-path`（校验以 / 开头，json 输出增 endpoint_path 字段）。全量 265 绿（+11）。
教训: 服务端换响应封装时，客户端按"顶层优先、缺则解包"做双格式兼容，可同时服务新旧两端，契约文档只需标注推荐格式；新增 CLI flag 后 `--json` 输出要同步加字段（契约只增不减）。

### docs: detector-api 双格式契约 + Core safe_guard 对接参考 <e4e0cb0>
detector-api.md §4 改双格式（BaseResponse 包装推荐 + 裸 results 兼容）并写明"两种封装皆无 → 游标照常推进结论丢失"的服务端红线；§7 超时改可配；§8 拆 Core 生产实现 / mock 联调桩两节（X-API-Key 语义 = Core api_keys 表密钥，401 响应体 `{success,message}` 只看状态码）。user-guide init 参数与配置表/env 列表同步（顺补历史漏项 SAITEC_CLI_MAX_QUEUE_SIZE）；SKILL.md 命令速查、operations.md mock 节、mock README 迁移标注。README 不动（命令计数与速查粒度无变化点）。
教训: 文档矩阵"新命令/新参数"行落到 README 的只有速查表与命令计数——flag 级变化不必动 README，硬凑反而制造噪音；检查 env 列表时顺带核对 `_DETECTOR_ENV_MAP` 全集，发现 MAX_QUEUE_SIZE 历史漏项。

## 2026-11-11

### refactor: 项目全面改名 SAITEC-Cli <ec619a9>
PyPI/GitHub `saitec-cli`、CLI 命令 `saitec-cli`、Python 包 `saitec_cli`、环境变量 `SAITEC_CLI_*`、数据目录 `~/.saitec-cli`、日志/PID/stop flag `saitec-cli.*`、skill 目录 `skills/saitec-cli` 全部同步；README/文档/CLI banner/help/导出报告改新名，旧名不留兼容。全量 pytest 253 passed / 1 skipped，CLI help/init/status/banner 冒烟通过，grep 旧名 0 残留（本文件历史条目保留原貌）。
教训: Windows 下 ZCode 自身及 node 服务的 cwd 持有项目根目录时，顶层文件夹 rename 必报 Device or resource busy——需关闭会话后从外部改名；替换审计要同时查旧名、旧名超集和替换产物（如 `saitec-cli-cli`）。

## 2026-09-10

### fix(mock): 前缀缓存只写新前缀，防 redo 复用被污染结论 <f88ba46>
Richard 报告 redo 把历史 clean 记录误标异常。根因在 detector 侧前缀去重缓存：原实现把"本次综合结论"写到会话每个前缀 hash 上，第 2 轮 partial hit 判出 violation 时覆写第 1 轮前缀的 clean，redo 重报旧记录走 full-hit 复用被污染缓存。mock_detector 落缓存改为 `chain[known:]`（仅新增轮次前缀），缓存语义修正为"审到第 k 轮时该前缀自身的判定"；detector-api.md 附录同步修正并把"禁止回写旧前缀"列为要点首条（云端真实检测服务器的实现依据）。+1 回归测试（clean → 次轮违规 → 重放断言）。
教训: ssgc 侧先自证清白再向外找——核查 redo（JSONL 重建该记录自身快照）与 reporter（只发本条 asdict）后确认上报链路干净，问题在 detector 侧缓存；"参考实现 + 契约建议文档"会被对接方照抄，示例代码里的语义缺陷会传染到真实服务。

### fix(store): save_results 改 INSERT OR REPLACE 支持 redo 更新结论 <c0e2265>
原实现普通 INSERT（docstring 写 INSERT OR IGNORE，两者均与行为不符），redo 新结论写本地撞 record_id UNIQUE 无法更新已有行。改 OR REPLACE：重复上报幂等不增行，redo/detector 修正判定后重报即生效。+1 覆盖更新用例。
教训: docstring 声称的幂等语义要和 SQL 实现对过才可信——"文档说 OR IGNORE、代码是裸 INSERT"这种不一致，直到用户真实触发 redo 更新场景才暴露。

### fix(cli): init 交互输入 api_key 改为明文回显 <03438c7>
Richard 反馈交互式 init 输入 X-API-Key 时终端不回显，输错无法察觉。删除 `_prompt` 的 secret 分支（getpass.getpass），api_key 走普通 input 回显；非 TTY 自动化路径、`--api-key` 参数、`SSGC_API_KEY` 注入均不受影响。CLI 77 绿 + 全量 251 绿。

### fix(cli): purge --dry-run 预览 SQLite 待删行数 <492a582>
原实现 `if db_path.exists() and not dry_run` 使 dry-run 完全跳过 SQLite 查询，`removed_sqlite_rows` 恒为 0，与 JSONL/日志可预览不一致。改为 dry-run 用 `SELECT COUNT(*)` 统计，实测 retention 0 正确报 7 行且未真删。
教训: purge 只清"超过保留期"的数据（JSONL 严格 `<` cutoff、活跃 ssgc.log 永不删、切割备份须跨午夜才产生）——当天活跃数据为空是正确行为；极端清理用 `--retention-days -1`（cutoff 落到明天，天然包含当天），但服务运行中删当天 JSONL 会因句柄占用失败，先 stop 再清。

## 2026-09-03

### refactor: 清理注释中的任务编号与过时骨架期标记 <3d34b34>
8 个源文件纯注释清理：删除旧任务单号前缀（P0-3/P0-5/P0-7/P1-9/P1-11/P1-12）与已过时的骨架期描述（"骨架阶段：命令占位""骨架阶段已实装"）。逻辑零改动，全量 251 绿（该工作树状态下运行）。

### feat: export HTML 报告详情卡片默认全部折叠 <d761c4b>
Richard 反馈导出报告首屏被全部展开的详情卡片淹没——`<details>` 卡片不再输出 open 属性，默认全部折叠（顶部汇总表承担概览，逐条点击展开）；打印链路不变（onbeforeprint 自动全展开，转 PDF 不受影响）。顺修 test_export fixture 时间炸弹：种子日期硬编码 2026-08-26，随时间推移滑出 `--since 7d` 查询窗口致 8 例查空失败，改为相对运行时 now 播种。user-guide §4.3 同步。全量 251 绿。
教训: 测试 fixture 凡涉及"相对 now 的时间窗查询"，种子时间必须相对运行时 now 计算——硬编码日期是定时炸弹，写死那天就在倒计时。

## 2026-08-27

### chore: PyPI 首发材料补齐（LICENSE/元数据/py.typed/skill 迁出 .claude） <efde220>
SKILL 从 `.claude/skills/ssgc/` 迁到 `skills/ssgc/` 入库（.claude/ 整目录进 gitignore，本地 Claude Code 用 junction 自建——mklink 相对路径按当前目录而非 junction 所在目录解析，**必须用绝对路径目标**）；README 增 Agent Skill 节说明自建方法。PyPI 材料：新增 LICENSE（Proprietary 全权利保留）、pyproject 补 classifiers + Project-URLs、py.typed 标记（mypy strict 类型标注供下游使用）。`python -m build` + `twine check` 双 PASSED，wheel 元数据（License/License-File/Project-URL/py.typed/entry point ssgc）核实齐全。顺带收尾遗留：test_chat 改名入库（chat_probe.py→probe_openai_completions.py，b2b87d8 漏提的半截状态）、清空壳目录 tests/test_chat/tests/verification、移除残留 worktree cozy-enchanting-parnas。
教训: ①pyproject 插入 [project.urls] 小节时必须注意其后裸写的 dependencies 会从 [project] 表"掉进" urls 表（TOML 表归属由位置决定）——本次 build 报错才暴露；②目录项表格里 `[project.urls]` 不能插在表与数组之间；③git worktree 残留用 `git worktree list` 盘点、干净则 remove。

## 2026-08-27

### fix: export 报告时间本地化 + 判断理由去截断 <affceb4>
用户实测发现两处输出问题：①报告时间（timestamp/detected_at/generated_at）显示 UTC，本地 UTC+8 差 8 小时对不上——加 `_to_local` 帮助函数（容错 Z 后缀/naive 时区/解析失败），md+html 所有时间显示处统一转本地时区，表头「时间 (UTC)」改「时间（本地）」；②汇总表/详情摘要的 reason 硬截断 `[:60]`/`[:42]`——去除截断全量显示。另清理 `_collect_rows` 无意义三元残留。核实 mock random 模式 reason 会正常显示（store 读出来 json.loads 成 dict，`_detail_reason` 容错），导出不会出错。251 pytest 绿，真实数据导出验证时间本地化 + reason 全量。
教训: 面向人类的报告时间一律本地时区，UTC 只在机器接口（--json / JSONL）保留；"数据层 UTC + 渲染层转本地"分层是正解。

### test: test_chat 三协议联调探针（共享题库 + anthropic 实测） <b2b87d8>
chat_probe.py 内嵌 PROMPTS 抽到 test_questions.json（每条带 tag），新增 probe_anthropic_messages.py（官方 anthropic SDK，共享 TEST_BASE_URL/APIKEY/MODEL）与 probe_openai_responses.py（脚本已就位，待用户拿到 Responses API 上游 key 后补测）。现役 DeepSeek /anthropic 口实测 12 条全绿（流式 2 + 非流式 10）；JSONL 归一化正确（request.messages/response.content/usage/finish_reason 完整）；上报链路双端验证通过（mock detector 端 13 条 anthropic 记录收到判定结论）。
教训: 三协议 finish_reason 保留原生值（end_turn/stop/completed）不翻译——detector 侧自己解释；控制台的"空回复"可能是渲染截断而非数据丢失，JSONL 原始内容为准。

### feat: export 检测报告导出命令（md/html 双格式） <4602744>
`ssgc export`：SQLite 结论 + JSONL 完整对话首次 join（`_load_records_bulk` 单次遍历防 O(N×M)；JSONL 缺失标注仅结论）。默认只导 suspicious/violation/error（clean 占 95%+ 会稀释重点；全量显式 `--status all`）。单份数据双渲染器保证 md/html 内容对齐；HTML 自包含、异常全展开/all 导出时 clean 折叠、onbeforeprint 展开适配打印转 PDF、全文 escape 防 XSS。store.query 加 SQL 层 status IN 过滤。+13 测试共 250 绿。
教训: asyncio.run(_q) 少调用括号会被 CLI 的 except-emit 吞成 ok=False 静默失败——CLI 错误路径输出必须进测试断言 stdout payload，不能只看 exit code。


## 2026-08-27

### fix(help): 移除 help 里的项目内 docs 路径引用 <d7951bc>
pip 安装的用户看不到项目内 docs，且 docs 路径未来可能变化——help 文本必须自包含。顶层 help 的 📖 Documentation 节直接删掉；命令 See also 节只保留相关命令引用（不再写 `docs/user-guide.md §X`）。模块 docstring（源码内注释，不影响 help 输出）保留。
教训: CLI help 是给"已安装用户"看的，项目内 docs 是给"源码阅读者/仓库访问者"看的——两个受众不重叠（pip 用户看不到项目内文件），help 里写 docs 路径是引用失效隐患。

### feat: --help 全面增强（emoji + Examples + Troubleshooting + 白色正文） <af32b9e>
typer monkey-patch（`typer.rich_utils.STYLE_HELPTEXT = "white"`）解决 help 正文灰色看不清的问题。顶层 `ssgc --help` 重写为 Quick start + Documentation 双节；21 处命令 docstring 全部重写为模板化结构（🎯 emoji 简介 + 详情 + `\b` Examples + Troubleshooting + See also），覆盖 init/start/monitor/stop/restart/status/report/validate/doctor/logs/tail/redo/purge/export + config×4 + service×4。docstring 修改不影响测试（测试用 CliRunner --json）。251 pytest 全绿。
教训: typer 默认 help 样式 `STYLE_HELPTEXT="dim"`（灰色），rich 接管渲染时正文仍按 click 默认 dim——需要 monkey-patch 内部常量，无官方 API。

## 2026-08-27

### fix: P2 redo 命令支持 UUID 前缀匹配（修复 report→redo 链路断裂） <f28cc70>
用户报告：`ssgc redo 69f1285a`（从 report 表格复制 8 位前缀）报"在 JSONL 中未找到记录"。根因：`report.py:130` 人类输出截断 `record_id` 为前 8 位（表格紧凑），`redo.py:30` 用 `==` 严格匹配完整 UUID——命令链路断裂。修复：`_find_record` 改为支持完整 UUID 与前缀匹配（唯一/多条歧义/未找到三态），歧义返回 `RECORD_ID_AMBIGUOUS` 错误列出候选完整 ID；未找到时报错提示用 `ssgc report --json` 拿完整 ID。真实验证 `ssgc redo 69f1285a` 成功返回完整 UUID + 检测结果。
教训: 命令链路上下游（report 输出 ID 给人看 → redo 接收 ID 重报）必须闭环——人类输出截断 N 位时下游就该支持 N 位前缀匹配，否则用户只能先 `--json` 拿全 ID 再粘回去。

## 2026-08-26

### docs: 新增 how-it-works.md 实现原理通俗导览 <0cc1502>

### fix: banner ASCII art 字母顺序拼错 SGCS→SSGC（手拼顺序笔误） <61e70d7>

### fix: 复查修 4 处 ssgc-cli 误转产物（重命名遗漏） <0e08d98>
用户要求脚本+grep 双重复查：发现 sed 把 `safe-guard-cli` 误转 `ssgc-cli`——AGENTS.md 两处指向不存在的 skills/ssgc-cli/ 路径、SKILL.md frontmatter name、evals.json skill_name；另心跳脚本 docstring 5 处旧命令、worktree 目录残留。全部修正后脚本/grep/运行时 help 三层验证 0 残留。
教训: 全局替换的残留检查 pattern 必须包含"替换产物"（ssgc-cli）而不仅是旧名（safe-guard）——误转产物引用失效比旧名残留更危险；skill 目录改名时 frontmatter name 与 evals.json 的 skill_name 是独立引用点。

### test: service list 断言剥 ANSI（FORCE_COLOR 环境 flaky） <de296c3>
Claude Code/CI 会话注入 FORCE_COLOR 时 rich 对 CliRunner 非 TTY 输出也着色并把 URL 拆成多段 SGR，纯文本断言匹配不上。断言前统一 `_plain()` 剥 ANSI。
教训: 富文本 CLI 的输出断言一律先剥 ANSI 再比——环境注入的 FORCE_COLOR 会让"非 TTY 无色"的假设失效；FORCE_COLOR 的优先级高于 NO_COLOR。

### chore: 清理 platformdirs 文案残留 <f382658>
重命名提交漏改 help/docstring 里的 3 处 platformdirs 描述（依赖已删）。

### refactor: 品牌统一 SSGC——包名/命令/路径/文档全量重命名 <9b0d301>
命名体系定稿：正式名 `saitec-safe-guard-cli`（repo/PyPI 不变），代码内一律 `ssgc`（src 目录/import/CLI 命令/环境变量 SSGC_CONFIG），品牌 SSGC，数据目录 `~/.ssgc`（弃 platformdirs，删依赖），日志/PID/stop flag 改 `ssgc.*`，banner 换 SSGC，skill 目录改名 `.claude/skills/ssgc`。不保留旧名兼容（无 safe-guard 别名、不回退 SAITEC_CONFIG）。保留：conda 环境名 saitec-guard、GitHub URL、作者 SaITec、本文件历史条目。本机已完成：conda 重装（ssgc.exe 替换 safe-guard.exe）、数据迁移至 ~/.ssgc（复制→全链路验证→删旧目录）、心跳脚本 exe 路径同步。
教训: 全局替换 `safe-guard` 前必须先用占位符保护 `saitec-safe-guard-cli`（PyPI/repo 名是其超集，会误伤）；`patch("saitec.xxx")` 这类字符串模块路径不被 `from saitec`/`import saitec` 两条 sed 规则覆盖，残留检查必须独立跑。

### docs: 文档矩阵收紧——bug 修复不更新 user-guide/SKILL <208e5cf>
撤销 gzip 修复在 user-guide §7.9 / SKILL 排错树与陷阱表 / operations.md 的排错条目（issues 清单保留）。AGENTS.md §4 矩阵拆分"CLI 操作行为变化"与"bug 修复"两行并加原则注脚：已修复的 bug 用户不会再遇到，排错条目只收仍存在/需用户操作的问题。
教训: 排错文档是给"还会遇到这个问题的人"看的——修复提交里顺手写排错条目是把文档矩阵当成了"改动清单"而非"用户视角"。

### fix: P0 gzip 上游响应头透传导致客户端全量 Connection error <812ddeb>
DeepSeek（压缩上游）联调暴露：aiohttp `auto_decompress=True` 已解压 body，但 `Content-Encoding: gzip` 头被原样透传 → openai SDK 按 gzip 解码明文失败，报 Connection error 并重试 2 次（代理侧全 200、JSONL 同请求 ×3 是识别指纹）。修复：两处响应头过滤集合提取为 `_STRIP_RESPONSE_HEADERS` 常量并加 `content-encoding`；+2 回归测试（非流式/SSE）；236 pytest 全绿，真实 DeepSeek 端到端验证通过。
教训: 代理改写了 body（解压/重组/分块）就必须重算/剥离描述 body 表示的头（Content-Length/Transfer-Encoding/**Content-Encoding**）——之前只想到前两个；本地不压缩的上游测不出这类 bug，必须用真实压缩上游联调。

### feat: mock llm 模式同会话前缀去重（token O(N²)→O(N)） <a5044d1>
完整快照语义下单会话第 N 条 Record 含前 N-1 轮全部内容，LLM 逐条全量送审时总审读量 O(N²)。按 messages 算 sha256 hash 链做已审前缀匹配：整条命中复用结论、部分命中只送新增轮次 + "前情已审"上下文行（防漏检跨轮攻击）。契约不变（去重是 detector 内部优化）。真实 LLM 实测 10 轮会话节省 81.8%（50 轮理论 ~96%）。detector-api.md 增"实现建议"章节。
教训: 去重责任放 detector 侧而非 CLI 侧——只有有缓存的一方知道"审过什么"；CLI 判断"同会话"不可靠（客户端会编辑历史）。

### docs: README/user-guide 同步 monitor 命令与命令计数 <a6676fe>
教训: monitor 功能提交时同步了 SKILL/user-guide 但漏了 README 速查与命令计数——功能提交前必须按 AGENTS.md §4 文档矩阵逐项核对。

### feat: monitor 前台实时监控命令（人盯场景） <8d9efff>
监控点选型走进程内事件流（runtime/proxy 的 event_sink 钩子发结构化事件），非拦截响应（violation 结论在 detector 侧异步产生，响应里没有）也非 tail 日志（非结构化轮询）。与 start 双向互斥（共享 PID 文件），stop 命令可停，Ctrl+C 优雅退出。

### feat: safe-guard-cli 项目级 skill（教 Agent 操作本 CLI） <ef8cf2b>
`.claude/skills/safe-guard-cli/`（SKILL.md + references + evals）。skill-creator 流程评估：6 子代理 with/without 对照，断言通过率持平（baseline 靠读源码也能完成），效率优势显著（耗时 446s vs 1045s，-57%）。
教训: ①测试暴露 SKILL 两处事实错误（start 对死 PID 自动覆盖、status 掉线时 exit 仍 0——健康判断必须查 data.running 字段）；②评测的 with/without 并发子代理必须用独立配置目录，共目录会互相污染；③Windows 下 skill-creator 脚本要 `PYTHONUTF8=1`（GBK 读中文 JSON 崩）。

### feat: CLI 人类可读输出视觉升级（rich/emoji/banner） <60a3d15>
rich（typer 传递依赖，零新增安装）：start/restart 显示 SAFE GUARD figlet banner、doctor/report 表格、emoji 图标集。`--json` 契约零改动；非 TTY 自动无色（测试友好）。版本号从包元数据读（pyproject 单一来源）。
教训: pipe 下 GBK 无法编码 emoji/✓——main.py 的 `_configure_io_encoding`（pipe 强制 UTF-8）是 rich 可用的前提。

### feat: mock detector 支持 LLM 判定模式 <95d0b31>
`MOCK_DETECTION_MODE=random|llm`；llm 模式逐条调 OpenAI 兼容 API 判定，失败降级 error 结论不阻断。
教训: 用户在 `tests/mock_detector/.env` 配了 llm+key 后，mock 测试真调 LLM（单轮 244s 且断言失败）——test_mock 的 client fixture 必须强制 `DETECTION_MODE=random` 与用户本地 .env 解耦。

### feat: 日志按日期切割 + purge 清理日志 <86ca0de>
TimedRotatingFileHandler(午夜, backupCount=14)；purge 增加日志备份清理（活跃文件永不删）。

## 2026-08-21

### feat: 自定义监控端点配置体验重构（service 子命令组） <6f62476>
service add/remove/set/list + init 强制显式 --upstream（单服务起步，不再生成官方地址 3 服务模板）+ upstream 误配完整端点 URL 的防呆警告 + 服务映射统一输出。解决"默认配置写死官方地址、CLI 无法增删服务"的核心缺口。
教训: `_set_by_path` 按已存在 name 匹配数组——纯 config set 无法新增 service，需要专门的 service 命令组操作 services 数组。

### feat: detector endpoint_path 配置 + 服务端对接文档 <90b89db>
同一 IP:端口下不同检测接口路径（url 只含 scheme+host+port，路径放 endpoint_path）。

### docs: 用户手册（user-guide.md ~750 行）+ README 重写 <02e7d68>

### fix: 8 个 CLI 鲁棒性问题（真实使用发现） <94380f8>
P0 `_serve.py` 相对导入导致 start 子进程秒崩（改绝对导入）；doctor 端口检查语义错误（运行时 connect/未运行时 bind）；Windows GBK 编码乱码（`_configure_io_encoding`）；AUTH 错误信息透传；init 参数校验等。
教训: ①`_serve.py` 被 `python <path>` 方式调用时无父包，相对导入必炸——子进程入口一律绝对导入；②CLI 工具的测试不能只靠单元测试，必须真实跑命令行（本次 8 个问题全在实操中暴露，单测全绿）；③conda 环境的 exe 不在 PATH，全路径调用。

## 2026-08-20

### test: FastAPI mock 检测服务器 <4dcdedb>
随机 5% 标 violation + /records 查询；starlette TestClient 需要 httpx（加入 mock extras）。
教训: fastapi 与 starlette 版本必须匹配（fastapi 0.115 给 starlette 1.x 传已删除的 on_startup 直接崩）——环境级不兼容报错先查版本配对。

### test: 补齐 CLI 与 runtime 后台循环单元测试（+19，覆盖 71%→79%） <594d4b1>

## 2026-08-19

### fix: 12 个初版严重问题（3 维度 review 发现） <52cd7c4>
P0：SSE 跨 chunk 半行拼接（adapters 加行缓冲）、非流式响应解析（bare-JSON 快路径）、flush 先落盘后出队、上报失败 pending 保留、api_key 脱敏、数据目录跟随 config。P1：env 传递、exit 码、Windows stop.flag、响应体上限。
教训: SSE 流式解析必须处理 TCP 分包把一行劈两半的情况；dataclasses.replace 是浅拷贝。

### feat: 初版六阶段交付 <73089b5 及此前>
e6a3e8d 骨架 → 64f1798 core(三级覆盖) → 61fe087 store → 4af4449 recorder/reporter → 6d3f32c adapters(三协议) → fcc677c proxy(流式透传) → c5046be runtime(编排/上报循环/游标续传) → 73089b5 CLI(13 命令)。测试随阶段同步写（167 个）。

---

## 未完成 / 待办
- 真实检测服务器接口定型后：把 mock 的前缀去重经验合入其实现（见 detector-api.md 实现建议）
- 传输膨胀若成痛点：batch gzip（Content-Encoding）——见 detector-api.md 附注
- v2 可选：monitor attach 已运行实例（需 IPC）；CLI 增量上报协议（仅当 detector 不可改时）
- LAN 同机部署（2026-08-27 讨论定案）：CLI 装到 LLM 所在服务器时，客户端直连 `<llm-server>:9001`、upstream 填本机端点——唯一缺口是 `proxy/server.py` 监听地址硬编码 `127.0.0.1`，需新增 service 级配置 `listen_host`（默认不变，按 AGENTS.md 四处同步）。部署注意：ssgc 无鉴权，暴露后需防火墙/安全组限源 IP；HTTP 明文仅限可信内网。
  - 否决的备选：让 LLM 推理服务主动转发上下文给 CLI（省请求/响应对齐）——需侵入推理服务代码或另做 ingest 端点协议，收益只有本地一跳回环延迟，不值；除非未来用自研推理服务有插桩点再评估

## 2026-08-27

### fix: adapter 状态残留导致请求-响应错位（P0）<b1f61c2>
adapter 实例在服务生命周期内复用，但未在请求间重置内部状态（_content / _finish_reason / _usage / _line_buffer）。第一个请求的响应内容累积到所有后续请求，导致报告中所有记录显示相同的响应。Adapter 基类新增 reset() 抽象方法，三个具体 adapter 实现，proxy/server.py 在 _handle() 开始时调用。anthropic 探针 12/12 验证通过。
教训: 有状态的单例对象必须在每次使用前重置；adapter 的"累积"语义隐含了"从哪里开始累积"的前提，这个前提需要在调用侧显式保证。
