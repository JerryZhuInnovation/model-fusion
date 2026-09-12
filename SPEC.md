# model-fusion MCP 规范（框架 + 关键实现）

薄 MCP：把多家 OpenAI-compatible API 统一成确定性 tools。**不做**策略判断、不做会话向导——那些在 skill 层。

## 包形态

```
mcp/
  package.json          # name: model-fusion-mcp; bin: model-fusion-mcp
  src/
    index.ts            # stdio MCP server 入口
    tools.ts            # tool schema 注册
    config.ts           # profile 读写
    probe.ts            # 环境扫描 + ping
    complete.ts         # 单模型调用
    fuse.ts             # fan-out + judge
    types.ts            # 共享类型
  tests/
```

技术选型（Flash 可替换等价物，但不得改 tool 名与参数语义）：
- Node.js + `@modelcontextprotocol/sdk`（stdio）
- HTTP 用内置 `fetch`
- 无重型依赖

## 启动与配置注入

在 `mimocode.jsonc`：

```jsonc
"mcp": {
  "model_fusion": {
    "type": "local",
    "command": ["node", "/abs/path/to/model-fusion-mcp/dist/index.js"],
    "environment": {
      "MODEL_FUSION_HOME": "/Users/<user>/.config/model-fusion",
      "OPENAI_API_KEY": "...",
      "DEEPSEEK_API_KEY": "...",
      "OPENROUTER_API_KEY": "..."
    },
    "enabled": true
  }
}
```

Server **只**通过 `process.env` 读取 key；配置文件里用 `api_key_env` 引用环境变量名，**永不**把 key 明文写入 json profile。

---

## Tool 契约（名称与语义锁定）

### 1. `probe_available_models`

扫描已知环境变量与可选用户配置目录，对候选做最小 ping。

**Input**

```ts
{
  ping_timeout_ms?: number   // default 4000
  ping?: boolean             // default true
}
```

**Output**

```ts
{
  candidates: Array<{
    id: string               // 稳定 id，如 "deepseek"
    display: string
    base_url: string
    model: string            // 默认模型名
    api_key_env: string      // 只回传变量名
    ping_ok: boolean
    ping_ms?: number
    roles: Array<"worker" | "judge">
    note?: string            // 如 "仅检测到 CLI 登录，无 HTTP API"
  }>
  host_model_hint?: string   // 宿主当前模型名（若能从 env 推断）
}
```

**探测名单（最低集，Flash 可扩充）**

| id | env | 默认 base_url | 默认 model | api_format |
|----|-----|---------------|------------|------------|
| openai | OPENAI_API_KEY | https://api.openai.com/v1 | gpt-4o-mini | openai |
| anthropic | ANTHROPIC_API_KEY | https://api.anthropic.com/v1 | claude-sonnet-4-5 | anthropic |
| openrouter | OPENROUTER_API_KEY | https://openrouter.ai/api/v1 | openai/gpt-4o-mini | openai |
| deepseek | DEEPSEEK_API_KEY | https://api.deepseek.com/v1 | deepseek-chat | openai |
| moonshot | MOONSHOT_API_KEY | https://api.moonshot.cn/v1 | moonshot-v1-8k | openai |
| gemini | GEMINI_API_KEY / GOOGLE_API_KEY | https://generativelanguage.googleapis.com/v1beta/openai | gemini-2.0-flash | openai |

**仅接受两类 API 协议**：

1. **openai**：`POST {base}/chat/completions`，`Authorization: Bearer`
2. **anthropic**：`POST {base}/messages`，`x-api-key` + `anthropic-version: 2023-06-01`；`system` 独立字段；`content[]` 里取 `type=text`

profile 中 worker/judge 可写 `api_format`（默认 `openai`）。OAuth 订阅/CLI 登录仍不可作 worker。

ping：`POST {base_url}/chat/completions`（或该家等价最小路径），messages=`[{role:"user",content:"ping"}]`，`max_tokens=1`。失败只标记 `ping_ok:false`，不抛给调用方中断。

`roles`：ping 通过且 key 存在 → `worker`+`judge`；无 key 但可选地发现 `~/.claude` 等 → `note` 说明仅提示，无 role。

### 2. `complete`

单模型一次对话完成。给 skill 脚本路径以外的通用出口，也便于调试。

**Input**

```ts
{
  model_id: string
  base_url: string
  api_key_env: string
  model: string
  messages: Array<{ role: "system"|"user"|"assistant", content: string }>
  temperature?: number
  max_tokens?: number
  timeout_ms?: number       // default 90000
  api_format?: "openai" | "anthropic"   // default openai
}
```

**Output**

```ts
{
  ok: boolean
  content?: string
  error?: string
  latency_ms: number
  model_id: string
}
```

### 3. `fuse_once`

按已加载 profile 做一次完整融合。**不接受**自由文本策略；策略只来自 profile（skill 向导已写好）。

**Input**

```ts
{
  profile: string           // 默认 "default"
  task: string              // 用户任务正文
  system?: string           // 可选任务级 system
  run_id?: string           // 便于幂等/追踪
}
```

**Output**

```ts
{
  ok: boolean
  profile: string
  strategy: "A" | "B"
  judge_model: string
  workers_used: Array<{ model_id: string, n: number, ok: number }>
  candidates: Array<{
    id: string
    model_id: string
    sample_index: number
    temperature: number
    content?: string
    error?: string
  }>
  final?: string            // judge 终稿
  run_path?: string         // 写入 runs/ 的路径
  error?: string
}
```

### 4. `save_profile` / `load_profile` / `list_profiles`

**Profile schema（锁定）**

```ts
{
  version: 1
  strategy: "A" | "B"
  judge: { model_id: string, api_key_env?: string, base_url?: string, model?: string }
  workers: Array<{
    model_id: string
    api_key_env: string
    base_url: string
    model: string
    samples: 1 | 2 | 3
    role: "primary" | "supplement"
    api_format?: "openai" | "anthropic"   // default openai
  }>
  temperature: {
    mode: "default" | "fixed" | "custom"
    fixed?: number
    sequence?: number[]              // custom；default 轮转 [0.4, 0.7, 1.0]
  }
  strategy_options: {
    on_partial_failure: "continue_with_available" | "abort"
    min_success: number              // default 2
    timeout_s: number                // default 90
    hard_only: boolean               // skill 层读；MCP 可存不执行
  }
  created_at: string
  updated_at: string
}
```

- `save_profile`: `{ profile, config }` → 写 `MODEL_FUSION_HOME/<profile>.json`（slug 校验：`[a-z0-9-]+`）
- `load_profile`: `{ profile }` → config
- `list_profiles`: → `{ profiles: string[] }`

---

## fuse 核心算法（Flash 必须按此实现）

```
1. load_profile(profile)
2. 温度序列生成：
   - fixed → 全部用 fixed
   - custom → sequence 按 sample_index 循环/截取，不足时回退 default 轮转
   - default → 每个 worker 的第 i 次采样用 [0.4,0.7,1.0][i % 3]
3. 展开请求列表：
   for w in workers:
     for i in 0..w.samples-1:
       enqueue(complete, w, temperature_i, sample_index=i)
4. 并发执行（建议并发上限 4），单路 timeout = strategy_options.timeout_s
5. 收集 ok / error
6. 若 ok 数 < min_success 且 on_partial_failure == abort → 失败返回
   若 ok 数 == 0 → 失败返回
   若 ok 数 == 1 → final = 该候选，标记 degraded
7. 组装 judge messages：
   A: 全部 ok 候选平等列出
   B: primary 的候选进「主方案」；supplement 进「补充视角」
   user content 含原 task + 分栏候选（编号、不写模型名，避免偏见；仅写 Sample #k）
8. 调用 judge.complete 一次（temperature 0.2 或 0）
9. final + candidates 写入 MODEL_FUSION_HOME/runs/<ts>-<profile>.json
   同时可写 .md 摘要供人读
10. 返回 fuse_once output
```

**Judge 系统提示（锁定语义，Flash 可润色语言但不得加职责）**

```
你是融合仲裁者。任务：取舍、判伪、组织。
- 从候选中吸收正确、完整、与用户任务匹配的内容。
- 发现明显事实错误或互相冲突时，做出取舍并用一两句说明理由。
- 输出一份可直接使用的最终答复，结构清晰。
- 不要复述全部候选，不要给模型打分排名，不要引入候选之外的新事实声称。
- 不要区分或猜测候选来自哪个模型。
```

**禁止（代码层不得添加）**
- 自动多轮 debate
- 第二次 judge
- 在 MCP 内做 hard_only 跳过（那是 skill 的事）
- 把 api key 打进日志或返回值

---

## 错误语义

| 情况 | 行为 |
|------|------|
| profile 不存在 | `ok:false`, `error: "profile not found"` |
| 某 worker 单路失败 | 计入 candidates.error，继续其它 |
| judge 失败 | 保留 candidates，`final` 空，`ok:false` |
| 环境缺 key | probe 阶段就标出；fuse 阶段该 worker 直接 error |

---

## Flash 待补全

见 `../FLASH_TODOS.md` 中 MCP 段。允许补全：SDK 样板、文件 IO 细节、测试、README、probe 名单扩充。**禁止**改 tool 名、参数名、profile schema 字段、judge 职责、并发/温度默认值语义。
