# model-fusion

Multi-model fusion strategy layer for **any AI agent** that can run shell commands and load skill/instruction files. Orchestrate probe → wizard → fan-out → single judge. Optional thin MCP for deterministic execution.

Works with Claude Code, MiMo Desktop, Cursor, and similar agents. See repository `README.md` for install paths.

## 核心设计（实现时禁止偏离）

1. **不做安装弹窗**。安装只落文件；新对话首次 `/fuse` 或用户表达融合意图时进入向导。
2. **自带工具优先**：`question` / 等价多选工具做会话交互，bash/curl 做 API 调用；若存在 `model_fusion` MCP tools，则优先用 MCP，否则走脚本路径。
3. **权重 = 采样次数**（1–3）；**温度不是权重**。同模型多次采样默认温度 0.4 / 0.7 / 1.0 轮转，可改。
4. **Judge 只调用一次**：取舍、判伪、组织。不做二次自审、不加第二意见。
5. **策略 A（默认）**：等权大池。**策略 B**：填空式主方案 / 补充分栏。**策略 C**：自然语言，由宿主当前模型解读后回显确认。
6. **Profile**：`default` + 命名 profile（如 `coding-review`）。后续会话先问「沿用 / 自定义」；自定义默认仅本次，可另存。
7. **职责边界**：脚本/MCP 负责 fan-out、超时、收集、拼 judge 输入、调 judge；agent 负责难度判断（hard_only）、C 模式解读、用户话术、失败解释。
8. **API 协议**：仅 `openai` 与 `anthropic` 两种 `api_format`（见 README）。

## 触发条件

- 用户明确：`/fuse`、`/model-fusion`、`模型融合`、`多模型评审`、`multi-model review`
- 用户要求：多个模型一起看 / 对比多个模型答案后给最终版
- 不触发：单模型问答、简单改错、用户只要一个模型回复

## 配置与产物路径

```
~/.config/model-fusion/
  default.json
  <profile-name>.json
  runs/
    <timestamp>-<profile>.json / .md
```

MCP 可选；本 skill **不依赖** MCP 也能跑。脚本相对本 skill 目录：`scripts/probe.py`、`scripts/fuse.py`、`scripts/save_profile.py`。

---

## 工作流（Agent 必须按顺序执行）

### Phase 0 — 是否已有配置

1. 若存在 `~/.config/model-fusion/*.json`：
   - 问一题：**沿用该配置 / 自定义本次**。
   - 沿用 → Phase 3。
   - 自定义 → Phase 1（向导），末尾问「是否写入 profile」。
2. 若无任何配置 → Phase 1。

### Phase 1 — Setup 向导

**1.1 探测**

- 若 MCP 可用：`probe_available_models`。
- 否则：`python3 scripts/probe.py --json`（可用 `--no-ping` 离线）。
- **仅接受** `openai` 与 `anthropic` 两种协议。OAuth 订阅/CLI 登录只作提示，不可进 workers。

**1.2 列表展示（正文，非多选）**

每项：`id | api_format | base_url | ping ✓/✗ | roles`

**1.3 表单式一次回复**

```text
请一次性按下面格式回复（可直接改空）：

策略: A
judge: <模型名>
workers: <模型名1, 模型名2, ...>
采样: <模型名1>=<次数>, <模型名2>=<次数>, ...
温度: 默认
profile: default

示例：
策略: A
judge: deepseek-chat
workers: deepseek-chat, qwen3-coder
采样: deepseek-chat=2, qwen3-coder=1
温度: 默认
profile: default
```

策略 B：

```text
策略: B
主方案: <模型名, ...>
补充: <模型名, ...可空>
judge: <模型名>
采样: <模型名>=<次数>, ...
温度: 默认
profile: default
```

策略 C：自然语言/JSON；**宿主模型解读**为 A 或 B，回显确认后写入。

**C 模式解读规范**

1. 只能映射为 A 或 B。
2. 解析出 judge、workers、samples、temperature。
3. 用户确认前不得写入 profile。
4. 无法解析则退回 B 填空模板。

话术：`scripts/messages.md`。

**Judge 字段**：须有 `api_key_env` / `base_url` / `model` / `api_format`。宿主内置模型若无 HTTP endpoint，说明无法作 judge。

**1.4 字段约束**

| 字段 | 约束 |
|------|------|
| 策略 | `A` \| `B` \| `C` |
| judge | 必填，探测列表中可调用项 |
| workers / 主方案 | ≥1 |
| 采样 | 整数 1–3；未写默认 1 |
| 温度 | `默认` \| `固定 <n>` \| `自定义 <a,b,c>` |
| profile | slug `[a-z0-9-]+` |

**1.5 写入**：`python3 scripts/save_profile.py <slug> --file <config.json>`，或 MCP `save_profile`。

### Phase 2 — 策略语义

- **A**：全部 ok 候选平等进 merge。
- **B**：primary 完整进主栏；supplement 进「补充视角」。
- **C**：解析为 A 或 B 并告知用户。

### Phase 3 — 执行

**3.1 hard_only**：简单任务跳过融合并说明。

**3.2 Fan-out**

- 优先 MCP `fuse_once`；否则 `python3 scripts/fuse.py --profile <name> --task "..."`（或 `--task-file`）。
- 禁止 agent 手写多路 curl 绕过统一入口。

**3.3 Judge**：脚本/MCP 内单次调用；职责固定为取舍、判伪、组织。

### Phase 4 — 回呈

```text
本次融合
- profile / strategy / judge / workers / 成功失败路数
【最终答复】
...
```

---

## 与 MCP

| 能力 | Skill | MCP |
|------|-------|-----|
| 向导 | 负责 | 不负责 |
| probe / fuse / profile IO | 脚本 | tools（优先） |
| hard_only | 负责 | 不执行 |

协议细节见 `mcp/SPEC.md`。
