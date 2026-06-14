# Claude Code 项目技术方案

> 本文档旨在帮助你快速熟悉 Claude Code 项目的整体架构、模块设计与核心机制，便于借鉴到自己的项目中。

---

## 一、项目概述

### 1.1 项目定位

**Claude Code** 是 Anthropic 推出的 AI 编程助手终端工具（Agentic Coding Tool），用户在终端中通过自然语言与 AI 协作完成编码任务。

**本仓库**（`anthropics/claude-code`）是 Claude Code 的**官方插件与示例仓库**，包含：

| 内容 | 说明 |
|------|------|
| **插件系统** | 13 个官方插件，展示 Hook/Command/Agent/Skill 四大扩展能力 |
| **Hook 机制** | 核心扩展能力，支持在 AI 工作流的关键节点注入自定义逻辑 |
| **示例配置** | MDM 企业部署模板、安全策略配置示例 |
| **自动化脚本** | GitHub Issues 全生命周期管理（去重、分类、清扫） |
| **技术设计文档** | 中文技术设计文档（`docs/technical_design.md`），详述 Hook 机制与插件体系 |

> **注意**：本仓库不包含 Claude Code 核心 CLI 源码。核心工具通过 npm 包 `@anthropic-ai/claude-code` 分发。

### 1.2 技术栈

| 类别 | 技术 |
|------|------|
| Hook 脚本 | Python 3（零第三方依赖，仅用标准库） |
| 自动化脚本 | TypeScript（Bun 运行时）+ Bash |
| 插件元数据 | JSON（plugin.json、hooks.json） |
| 规则配置 | Markdown + YAML Frontmatter |
| 安全策略 | JSON（settings.json） |
| 企业部署 | macOS: plist/mobileconfig; Windows: ADMX/PowerShell |
| CI/CD | GitHub Actions（12 个工作流） |

---

## 二、整体架构

### 2.1 架构全景图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Claude Code CLI（核心）                        │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌────────────────────┐  │
│  │ 对话引擎  │ │ 工具执行器 │ │ 权限系统  │ │ 上下文管理        │  │
│  └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └────────┬───────────┘  │
│        │             │             │                  │              │
│  ┌─────┴─────────────┴─────────────┴──────────────────┴──────────┐  │
│  │                      Hook 生命周期系统                         │  │
│  │  UserPromptSubmit → PreToolUse → [工具执行] → PostToolUse → Stop │
│  └──────────────────────────┬────────────────────────────────────┘  │
│                             │ IPC (stdin/stdout JSON)               │
└─────────────────────────────┼───────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
  ┌─────┴──────┐    ┌────────┴────────┐    ┌───────┴───────┐
  │ Hook 脚本   │    │ 插件系统        │    │ 自动化脚本    │
  │ (Python)    │    │ (Plugin)        │    │ (TS/Bash)     │
  └────────────┘    └─────────────────┘    └───────────────┘
```

### 2.2 核心交互模型

Claude Code 的核心是一个 **Agent Loop**（智能体循环）：

```
用户输入 → AI 思考 → 选择工具 → 执行工具 → 观察结果 → 继续思考 → ... → 任务完成
```

在这个循环中，Hook 系统提供了 4 个拦截点，让外部脚本可以干预 AI 的行为。

---

## 三、模块详解

本项目可划分为以下 **8 个核心模块**，每个模块有清晰的边界和独立职责：

```
┌─────────────────────────────────────────────────────┐
│                    模块全景                           │
├──────────────┬──────────────┬───────────────────────┤
│  1. Hook 系统 │  2. 插件体系  │  3. 规则引擎          │
├──────────────┼──────────────┼───────────────────────┤
│  4. 安全审查  │  5. 会话状态  │  6. 命令/Agent/Skill  │
├──────────────┼──────────────┼───────────────────────┤
│  7. 企业部署  │  8. Issue自动化│                      │
└──────────────┴──────────────┴───────────────────────┘
```

---

### 模块 1：Hook 生命周期系统

**模块边界**：定义 Hook 事件的触发时机、通信协议、退出码语义，是所有插件扩展的底层基础设施。

**功能概述**：Hook 是 Claude Code 的核心扩展机制，允许在 AI 工作流的关键节点注入自定义逻辑。

#### 1.1 四个生命周期事件

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| **UserPromptSubmit** | 用户提交提示词时 | 注入上下文、敏感词检查、保存基线 |
| **PreToolUse** | AI 调用工具执行前 | 拦截危险命令、阻止不安全操作 |
| **PostToolUse** | 工具执行完成后 | 审查执行结果、注入安全警告 |
| **Stop** | AI 认为任务完成、准备停止时 | 阻止过早停止、要求完成额外检查 |

#### 1.2 通信协议

Hook 脚本与 Claude Code 之间采用 **IPC（进程间通信）** 模型：

```
Claude Code (父进程)
    ├── stdin  ──→  传入 JSON 数据
    ├── stdout ←──  接收 JSON 响应
    ├── stderr ←──  接收提示消息
    └── 退出码  ←──  0=放行 / 1=警告(用户可见) / 2=阻断(AI可见)
```

**关键约束**：
- Hook 必须在 **10 秒超时**内完成
- stdin 中的 JSON 只能读取一次

#### 1.3 输出协议两种模式

| 模式 | 适用场景 | 行为 |
|------|---------|------|
| **简单协议**（stderr + 退出码） | 独立 Hook 脚本 | exit(0)放行 / exit(1)警告 / exit(2)阻断 |
| **JSON 协议**（stdout） | 插件式 Hook | `{}`放行 / `{systemMessage}`警告 / `{hookSpecificOutput:{permissionDecision:"deny"}}`阻断 |

#### 1.4 设计理念

| 原则 | 实现 |
|------|------|
| **容错优先** | `try/except/finally: sys.exit(0)` — 任何异常都不阻断操作 |
| **最小权限** | 通过 `matcher` 过滤只拦截特定工具 |
| **幂等设计** | 同一输入多次执行结果一致 |
| **快速响应** | 10 秒超时保护 |

**借鉴要点**：如果你要在项目中实现类似的 Hook 系统，核心要素是：
1. 明确的生命周期事件定义
2. 标准化的 IPC 通信协议（stdin/stdout JSON）
3. 三级退出码语义（放行/警告/阻断）
4. 强制容错（永不因 Hook 错误阻断主流程）

---

### 模块 2：插件体系

**模块边界**：定义插件的目录结构、元数据声明、发现与加载机制，是 Hook 之上的一层组织框架。

**功能概述**：将 Hook、Command、Agent、Skill 等扩展能力打包为可分发、可安装的插件单元。

#### 2.1 插件标准目录结构

```
my-plugin/
├── .claude-plugin/
│   └── plugin.json          # 插件元信息（名称/版本/作者）
├── hooks/
│   ├── hooks.json            # Hook 注册配置
│   └── *.py / *.sh           # Hook 脚本
├── commands/                 # 斜杠命令（Markdown + YAML frontmatter）
│   └── my-command.md
├── agents/                   # 自主 Agent（Markdown 提示词）
│   └── my-agent.md
├── skills/                   # 技能（SKILL.md + references/ + examples/）
│   └── my-skill/SKILL.md
├── .mcp.json                 # 外部工具配置（可选）
└── README.md
```

#### 2.2 四大扩展能力

| 扩展类型 | 入口 | 功能 | 示例 |
|---------|------|------|------|
| **Hook** | `hooks/hooks.json` | 生命周期拦截 | security-guidance 的安全审查 |
| **Command** | `commands/*.md` | 自定义斜杠命令 | `/commit`、`/code-review` |
| **Agent** | `agents/*.md` | 自主子代理 | code-explorer、code-reviewer |
| **Skill** | `skills/*/SKILL.md` | 渐进式技能指导 | writing-rules、frontend-design |

#### 2.3 插件注册与发现

```json
// hooks.json — Hook 注册
{
  "hooks": {
    "PreToolUse": [{
      "hooks": [{
        "type": "command",
        "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pretooluse.py",
        "timeout": 10
      }]
    }]
  }
}
```

- `${CLAUDE_PLUGIN_ROOT}` 环境变量在运行时替换为插件根目录
- 插件通过市场安装或手动配置 `.claude/settings.json`

**借鉴要点**：
1. 插件是扩展能力的**打包单元**，不是运行单元
2. 每种扩展能力有独立的入口格式和加载机制
3. 环境变量占位符解决路径可移植性

---

### 模块 3：规则引擎（hookify）

**模块边界**：从配置文件到规则匹配决策的完整链路，是"声明式规则 → 运行时拦截"的中间层。

**功能概述**：用 Markdown + YAML frontmatter 定义 Hook 规则，无需编码即可实现命令拦截、文件编辑警告等行为。

#### 3.1 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| **配置加载器** | `core/config_loader.py` (~290行) | 从 `.claude/hookify.*.local.md` 加载和解析规则 |
| **规则评估引擎** | `core/rule_engine.py` (~310行) | 将规则与 Hook 输入数据匹配，输出决策结果 |
| **Hook 入口脚本** | `hooks/pretooluse.py` 等 4 个 | 读取 stdin → 加载规则 → 评估 → 输出 stdout |

#### 3.2 数据模型

```python
@dataclass
class Condition:
    field: str       # 要检查的字段名（command/file_path/new_text 等）
    operator: str    # 匹配操作符
    pattern: str     # 匹配模式

@dataclass
class Rule:
    name: str                         # 规则名称
    enabled: bool                     # 是否启用
    event: str                        # 事件类型：bash/file/stop/prompt/all
    conditions: List[Condition]       # 条件列表（AND 语义）
    action: str = "warn"              # 动作：warn 或 block
    tool_matcher: Optional[str]       # 工具匹配器
    message: str = ""                 # 消息正文（Markdown）
```

#### 3.3 六种匹配操作符

| 操作符 | 含义 | 示例 |
|--------|------|------|
| `regex_match` | 正则匹配 | `rm\s+-rf` |
| `contains` | 包含子串 | `password` |
| `equals` | 精确相等 | `production.yml` |
| `not_contains` | 不包含 | `TODO` |
| `starts_with` | 前缀 | `sudo` |
| `ends_with` | 后缀 | `.env` |

#### 3.4 规则文件格式

```markdown
---
name: block-dangerous-rm
enabled: true
event: bash
action: block
conditions:
  - field: command
    operator: regex_match
    pattern: rm\s+-rf
---

⚠️ **Dangerous rm command detected!**
```

#### 3.5 性能优化

- **LRU 正则缓存**：`@lru_cache(maxsize=128)` 编译后的正则缓存复用
- **规则预过滤**：按事件类型加载规则，减少不必要的评估
- **轻量 YAML 解析器**：自实现，避免引入 pyyaml 依赖

**借鉴要点**：
1. Markdown + YAML frontmatter 是用户友好的规则格式
2. Condition 列表 + AND 语义提供灵活的组合条件
3. 自实现的轻量 YAML 解析器满足简单场景，零依赖
4. LRU 缓存对正则密集型场景效果显著

---

### 模块 4：安全审查系统（security-guidance）

**模块边界**：对 AI 生成的代码进行多层安全审查，从模式匹配到 LLM 语义分析到 Agentic 跨文件追踪。

**功能概述**：三层递进式安全审查架构，覆盖从单行模式到跨文件数据流的完整安全检测链路。

#### 4.1 三层审查架构

```
┌─────────────────────────────────────────────────────────┐
│ 第一层：模式匹配（实时）                                   │
│ • PostToolUse Hook，每次文件编辑后触发                      │
│ • 25+ 正则安全模式                                        │
│ • 毫秒级响应，注入 additionalContext 警告                   │
├─────────────────────────────────────────────────────────┤
│ 第二层：LLM 差异审查（停止时）                              │
│ • Stop Hook，AI 准备停止时触发                              │
│ • git diff 获取本次会话变更                                │
│ • 调用 Claude API 语义分析，发现高危漏洞时阻止停止           │
├─────────────────────────────────────────────────────────┤
│ 第三层：Agentic 提交审查（提交时）                          │
│ • PostToolUse[Bash] Hook，检测 git commit                  │
│ • SDK 驱动审查器，跨文件数据流追踪                          │
│ • 检测 IDOR/Auth Bypass/SSRF 等复杂漏洞                    │
└─────────────────────────────────────────────────────────┘
```

| 维度 | 第一层 | 第二层 | 第三层 |
|------|--------|--------|--------|
| 触发时机 | 每次文件编辑后 | AI 准备停止时 | git commit 后 |
| 检测方式 | 正则表达式 | Claude API | Claude API + 文件读取 |
| 检测范围 | 单文件单行 | 本次会话所有变更 | 跨文件数据流 |
| 检测速度 | 毫秒级 | 秒级 | 十秒级 |
| 检测深度 | 模式级 | 语义级 | 逻辑级 |

#### 4.2 核心子模块

| 子模块 | 文件 | 职责 |
|--------|------|------|
| **安全模式库** | `patterns.py` | 25+ 安全检测规则，覆盖反序列化/注入/XSS/加密等 |
| **LLM 调用封装** | `llm.py` | Claude API 调用（urllib.request，零第三方依赖） |
| **Git 工具** | `gitutil.py` | Git 差异分析、基线管理 |
| **差异状态管理** | `diffstate.py` | Git 基线 SHA 捕获与增量 diff |
| **会话状态** | `session_state.py` | 跨 Hook 调用的状态持久化（文件锁） |
| **扩展性** | `extensibility.py` | 用户自定义安全模式与组织策略注入 |
| **主入口** | `security_reminder_hook.py` | Hook 主逻辑 + 状态管理（2000+行） |

#### 4.3 Git 基线管理

security-guidance 使用 Git 基线确保只审查 AI 本次会话新增的代码变更：

1. **UserPromptSubmit** 时：`git stash create` 捕获当前状态 SHA
2. **Stop Hook** 时：`git diff baseline_sha` 只获取本次变更
3. 审查完成后更新基线，下次只审查增量

#### 4.4 安全模式覆盖范围

| 漏洞类别 | 检测模式 | 严重度 |
|----------|---------|--------|
| 不安全反序列化 | `pickle.load`, `yaml.load`, `torch.load(weights_only=False)` | 高 |
| 硬编码密钥 | `API_KEY = "..."`, `SECRET = "..."` | 高 |
| SQL 注入 | `f"SELECT ... WHERE {user_input}"` | 高 |
| 命令注入 | `os.system()`, `subprocess.call(shell=True)` | 高 |
| XSS | `innerHTML`, `dangerouslySetInnerHTML`, `document.write` | 高 |
| 不安全加密 | `createCipher`（无IV）, `AES.MODE_ECB` | 中 |
| TLS 验证禁用 | `verify=False`, `InsecureSkipVerify` | 中 |
| GitHub Actions 注入 | 未转义的 `${{ github.event.* }}` | 中 |

#### 4.5 扩展性设计

组织自定义安全策略通过 Markdown 文件注入，三级优先级：

| 文件位置 | 作用域 | 提交到 Git |
|----------|--------|-----------|
| `~/.claude/claude-security-guidance.md` | 用户级 | 否 |
| `<project>/.claude/claude-security-guidance.md` | 项目级 | 是 |
| `<project>/.claude/claude-security-guidance.local.md` | 项目本地 | 否 |

关键安全约束：自定义策略**只能增加检查，不能抑制发现**。

**借鉴要点**：
1. 三层递进式审查是从"快速粗筛"到"深度精查"的经典架构
2. Git 基线管理确保只审查增量变更，避免重复审查
3. 扩展性设计遵循"只能加不能减"的安全原则
4. 零第三方依赖（用 `urllib.request` 替代 `requests`）

---

### 模块 5：会话状态管理

**模块边界**：跨 Hook 调用的状态持久化机制，解决 Hook 无状态脚本之间的数据共享问题。

**功能概述**：Hook 脚本是独立的进程调用，本身无状态。本模块通过文件系统实现跨调用的状态共享。

#### 5.1 状态数据

| 状态字段 | 类型 | 用途 |
|---------|------|------|
| `baseline_sha` | str | Git 基线 SHA，用于增量 diff |
| `touched_paths` | list | 本次会话编辑的文件列表 |
| `fire_count` | int | Stop Hook 触发次数（防止无限循环） |
| `pending_warnings` | list | 待处理的模式警告 |
| `shown_warnings` | set | 已显示的警告（去重） |
| `rate_limits` | dict | 滚动窗口限速计数 |

#### 5.2 原子性操作

| 函数 | 功能 | 原子性保证 |
|------|------|-----------|
| `with_locked_state(session_id, fn)` | 加锁读取 → 执行 fn → 保存 | fcntl 文件锁 |
| `atomic_check_and_mark_warning()` | 去重显示警告 | 通过 with_locked_state |
| `atomic_check_rate_limit()` | 滚动窗口限速 | 通过 with_locked_state |
| `save_baseline_sha()` | 保存 Git 基线 | 通过 with_locked_state |

#### 5.3 容错机制

- 文件锁失败 → 静默放行（不阻断操作）
- 状态文件不存在 → 创建新文件
- JSON 解析失败 → 返回默认状态

**借鉴要点**：
1. Hook/插件系统中的状态管理需要原子性（文件锁）
2. 滚动窗口限速防止无限循环
3. 去重机制避免重复警告
4. 容错优先：状态管理失败不能阻断主流程

---

### 模块 6：Command / Agent / Skill 扩展

**模块边界**：插件体系中的三种扩展能力，分别解决"用户主动触发"、"自主委托执行"、"知识注入"三种需求。

#### 6.1 Command（斜杠命令）

**定位**：用户主动触发的操作，类似 CLI 子命令。

**格式**：Markdown 文件 + YAML frontmatter

```markdown
---
allowed-tools: Bash(gh pr comment:*), Bash(gh pr diff:*)
description: Code review a pull request
---

Provide a code review for the given pull request...
```

**关键特性**：
- `allowed-tools` 限制命令可使用的工具范围
- `description` 提供命令描述
- 支持参数（如 `--comment`）

**项目中的 Command 示例**：

| 命令 | 插件 | 功能 |
|------|------|------|
| `/commit` | commit-commands | 自动生成 commit message |
| `/commit-push-pr` | commit-commands | 一键 commit + push + PR |
| `/code-review` | code-review | 自动化 PR 代码审查 |
| `/feature-dev` | feature-dev | 7 阶段功能开发工作流 |
| `/hookify` | hookify | 创建 Hook 规则 |

#### 6.2 Agent（自主子代理）

**定位**：可被 Command 或其他 Agent 调用的自主执行单元，类似子任务。

**格式**：Markdown 提示词文件

**关键特性**：
- 可并行启动多个 Agent
- 每个 Agent 有独立的工具权限
- 支持 Haiku/Sonnet/Opus 不同模型选择

**项目中的 Agent 示例**：

| Agent | 插件 | 功能 |
|-------|------|------|
| `code-explorer` | feature-dev | 代码库分析 |
| `code-architect` | feature-dev | 架构设计 |
| `code-reviewer` | feature-dev / code-review | 代码审查 |
| `conversation-analyzer` | hookify | 分析对话模式 |
| `plugin-validator` | plugin-dev | 插件验证 |
| `silent-failure-hunter` | pr-review-toolkit | 静默失败检测 |

**多 Agent 并行审查模式**（code-review 插件）：

```
/code-review
    ├── Haiku Agent: 检查 PR 状态（是否关闭/草稿/已审查）
    ├── Haiku Agent: 收集相关 CLAUDE.md 文件
    ├── Sonnet Agent: 总结 PR 变更
    ├── Sonnet Agent 1: CLAUDE.md 合规审查
    ├── Sonnet Agent 2: CLAUDE.md 合规审查
    ├── Opus Agent 1: Bug 检测
    └── Opus Agent 2: 逻辑错误检测
         ↓
    并行验证每个发现（置信度 0-100）
         ↓
    过滤低置信度 → 输出高信度问题
```

#### 6.3 Skill（技能）

**定位**：知识注入，为 AI 提供领域专业指导。

**格式**：`SKILL.md` + `references/` + `examples/`

**关键特性**：
- 渐进式披露：基础指导 → 详细参考 → 代码示例
- 可被自动触发（如检测到前端工作自动激活 frontend-design）
- 零代码，纯 Markdown 知识

**借鉴要点**：
1. **Command = 用户意图入口**，Agent = 委托执行单元，Skill = 知识注入
2. 多 Agent 并行 + 置信度过滤是高质量 AI 审查的关键模式
3. `allowed-tools` 限制工具范围是安全最佳实践
4. Skill 的渐进式披露设计平衡了上下文长度与信息丰富度

---

### 模块 7：企业部署与安全策略

**模块边界**：面向企业 IT 管理员的部署与管控能力，解决"如何在组织内安全使用 Claude Code"的问题。

#### 7.1 MDM（移动设备管理）部署

| 平台 | 文件 | 部署方式 |
|------|------|---------|
| macOS | `com.anthropic.claudecode.plist` | Jamf/Kandji Custom Settings |
| macOS | `com.anthropic.claudecode.mobileconfig` | 完整配置 Profile |
| Windows | `Set-ClaudeCodePolicy.ps1` | Intune Platform Scripts |
| Windows | `ClaudeCode.admx` + `.adml` | Group Policy ADMX 模板 |
| 通用 | `managed-settings.json` | 部署到系统配置目录 |

#### 7.2 三级安全策略

| 策略 | 文件 | 关键配置 |
|------|------|---------|
| **宽松** | `settings-lax.json` | 仅禁用 `--dangerously-skip-permissions` |
| **严格** | `settings-strict.json` | 禁用跳过权限、限制市场、拒绝 Web 工具、Bash 需审批、启用沙箱 |
| **Bash 沙箱** | `settings-bash-sandbox.json` | 限制自定义权限、启用沙箱、禁止未沙箱化命令 |

#### 7.3 DevContainer 安全隔离

- Docker 容器 + iptables 防火墙白名单
- 仅允许访问 GitHub、npm、Anthropic API、Sentry、VS Code 相关域名
- Windows 支持 Docker/Podman 后端自动启动

**借鉴要点**：
1. 托管设置具有最高优先级，用户无法覆盖（企业管控核心）
2. 多平台 MDM 模板降低企业部署门槛
3. DevContainer + 防火墙白名单是最严格的网络隔离方案

---

### 模块 8：GitHub Issues 自动化

**模块边界**：GitHub Issues 的全生命周期自动化管理，独立于 Claude Code 核心功能。

**功能概述**：自动分类、去重、标记过期、关闭不活跃的 Issue。

#### 8.1 Issue 生命周期

```
新建 Issue
    ↓
自动分类（triage-issue）→ 标签：bug/enhancement/question/invalid/duplicate
    ↓
重复检测（dedupe）→ 5 个并行 Agent 搜索重复
    ↓
生命周期管理
    ├── invalid (3天) → 自动关闭
    ├── needs-repro (7天) → stale → 自动关闭
    ├── needs-info (7天) → stale → 自动关闭
    ├── stale (14天) → 自动关闭
    └── autoclose (14天) → 自动关闭
    ↓
高票保护：≥10 赞的 Issue 不会被自动关闭
```

#### 8.2 核心脚本

| 脚本 | 语言 | 功能 |
|------|------|------|
| `issue-lifecycle.ts` | TS | **单点配置**：标签、超时天数、原因消息 |
| `sweep.ts` | TS | 过期清扫：标记 stale → 关闭超时 Issue |
| `auto-close-duplicates.ts` | TS | 关闭 3 天前标记为重复但无后续活动的 Issue |
| `gh.sh` | Bash | gh CLI 安全封装：只允许特定子命令和标志 |

#### 8.3 gh CLI 安全封装

`gh.sh` 只允许以下操作：
- `gh issue view`
- `gh issue list`
- `gh search issues`
- `gh label list`

防止 Agent 通过 gh CLI 执行越权操作（如创建/删除仓库）。

**借鉴要点**：
1. **单点配置**模式（`issue-lifecycle.ts`）将策略与执行分离
2. CLI 安全封装是 Agent 系统权限控制的最佳实践
3. 高票保护机制避免自动关闭社区关注度高的问题

---

## 四、13 个官方插件功能速查

| 插件 | 版本 | 核心能力 | 扩展类型 |
|------|------|---------|---------|
| **agent-sdk-dev** | v1.0.0 | Agent SDK 应用创建与验证 | Command + Agent |
| **claude-opus-4-5-migration** | v1.0.0 | 模型迁移（Sonnet/Opus → Opus 4.5） | Skill |
| **code-review** | v1.0.0 | 多 Agent 并行 PR 审查（置信度过滤） | Command + Agent |
| **commit-commands** | v1.0.0 | Git 工作流自动化 | Command |
| **explanatory-output-style** | v1.0.0 | 教育性 Insight 注入 | Hook (SessionStart) |
| **feature-dev** | v1.0.0 | 7 阶段功能开发工作流 | Command + Agent |
| **frontend-design** | v1.0.0 | 高品质前端设计指导 | Skill |
| **hookify** | v0.1.0 | 可配置 Hook 规则引擎 | Command + Agent + Skill + Hook |
| **learning-output-style** | v1.0.0 | 交互式学习模式（做中学） | Hook (SessionStart) |
| **plugin-dev** | v0.1.0 | 插件开发工具包（7 个技能） | Command + Agent + Skill |
| **pr-review-toolkit** | v1.0.0 | 6 维度 PR 审查 | Command + Agent |
| **ralph-wiggum** | v1.0.0 | 自我参照迭代循环 | Command + Hook (Stop) |
| **security-guidance** | v2.0.0 | 三层安全审查 | Hook (全生命周期) |

---

## 五、可借鉴的设计模式

### 5.1 架构模式

| 模式 | 本项目应用 | 你的项目可借鉴 |
|------|-----------|---------------|
| **三层递进架构** | security-guidance: 模式匹配 → LLM 审查 → Agentic 审查 | 快速粗筛 + 深度精查的组合策略 |
| **Hook/插件分离** | Hook 是底层机制，插件是上层组织 | 机制与组织分离，机制稳定，插件灵活 |
| **声明式规则** | hookify: Markdown 规则文件 → 运行时拦截 | 让非程序员也能定义行为规则 |
| **Agent 并行 + 置信度过滤** | code-review: 4 Agent 并行 → 置信度评分 → 过滤 | 减少 AI 误报，提高信号质量 |
| **单点配置** | issue-lifecycle.ts: 策略与执行分离 | 配置驱动，避免逻辑散落 |

### 5.2 工程模式

| 模式 | 本项目应用 | 你的项目可借鉴 |
|------|-----------|---------------|
| **零第三方依赖** | Hook 脚本仅用 Python 标准库 | 减少供应链风险，提高可移植性 |
| **容错优先** | `try/except/finally: sys.exit(0)` | 扩展点错误不能影响主流程 |
| **Git 基线管理** | `git stash create` → 增量 diff | 只审查/处理增量变更 |
| **文件锁状态管理** | `fcntl` + JSON 文件 | 无数据库场景下的原子性状态共享 |
| **CLI 安全封装** | `gh.sh` 白名单过滤 | Agent 系统的工具权限控制 |
| **环境变量占位符** | `${CLAUDE_PLUGIN_ROOT}` | 解决插件路径可移植性 |

### 5.3 反模式警示

| 反模式 | 本项目的避免方式 |
|--------|----------------|
| Hook 阻塞主流程 | `finally: sys.exit(0)` + 超时保护 |
| 第三方依赖链风险 | 仅用 Python 标准库 |
| 规则硬编码 | hookify: 外部 Markdown 规则文件 |
| AI 误报过多 | 置信度评分 + 并行验证 |
| 无限循环 | `MAX_STOP_HOOK_FIRINGS=3` + 滚动窗口限速 |
| 自定义策略抑制安全 | "只能加不能减"原则 |

---

## 六、项目目录结构参考

```
claude-code/
├── .claude/commands/                    # 自定义斜杠命令
│   ├── commit-push-pr.md
│   ├── dedupe.md
│   └── triage-issue.md
├── .claude-plugin/marketplace.json      # 插件市场清单
├── .devcontainer/                       # DevContainer 配置
│   ├── Dockerfile
│   ├── devcontainer.json
│   └── init-firewall.sh
├── .github/
│   ├── ISSUE_TEMPLATE/                  # Issue 模板
│   └── workflows/                       # GitHub Actions (12个工作流)
├── docs/
│   └── technical_design.md              # 技术设计文档（中文，1535行）
├── examples/
│   ├── mdm/                             # MDM 企业部署模板
│   │   ├── macos/                       # macOS plist/mobileconfig
│   │   └── windows/                     # Windows ADMX/PowerShell
│   └── settings/                        # 安全策略配置示例
│       ├── settings-lax.json
│       ├── settings-strict.json
│       └── settings-bash-sandbox.json
├── plugins/                             # 官方插件集合（13个）
│   ├── agent-sdk-dev/
│   ├── claude-opus-4-5-migration/
│   ├── code-review/
│   ├── commit-commands/
│   ├── explanatory-output-style/
│   ├── feature-dev/
│   ├── frontend-design/
│   ├── hookify/                         # ★ 规则引擎插件
│   │   ├── core/
│   │   │   ├── config_loader.py         # 规则加载器
│   │   │   └── rule_engine.py           # 规则评估引擎
│   │   ├── hooks/                       # 4个生命周期 Hook 入口
│   │   ├── examples/                    # 规则文件示例
│   │   └── skills/                      # 技能指导
│   ├── learning-output-style/
│   ├── plugin-dev/
│   ├── pr-review-toolkit/
│   ├── ralph-wiggum/
│   └── security-guidance/               # ★ 安全审查插件
│       └── hooks/
│           ├── security_reminder_hook.py # 主入口 (2000+行)
│           ├── patterns.py              # 25+ 安全模式
│           ├── llm.py                   # LLM API 调用
│           ├── gitutil.py               # Git 工具
│           ├── diffstate.py             # 差异状态
│           ├── session_state.py         # 会话状态
│           ├── extensibility.py         # 用户扩展
│           └── review_api.py            # 审查 API
├── scripts/                             # GitHub Issues 自动化脚本
│   ├── issue-lifecycle.ts               # 生命周期配置
│   ├── sweep.ts                         # 过期清扫
│   ├── auto-close-duplicates.ts         # 重复 Issue 关闭
│   ├── gh.sh                            # gh CLI 安全封装
│   └── ...
├── Script/
│   └── run_devcontainer_claude_code.ps1 # Windows DevContainer 启动
├── CHANGELOG.md
├── LICENSE.md
├── README.md
└── SECURITY.md
```

---

## 七、快速上手路径

如果你要基于本项目进行借鉴开发，建议按以下顺序阅读：

| 顺序 | 内容 | 目标 |
|------|------|------|
| 1 | `README.md` | 了解项目定位和安装方式 |
| 2 | `docs/technical_design.md` 前三章 | 理解 Hook 系统核心机制 |
| 3 | `plugins/hookify/` | 学习规则引擎的声明式设计 |
| 4 | `plugins/security-guidance/` | 学习三层递进式安全审查 |
| 5 | `plugins/code-review/` | 学习多 Agent 并行审查模式 |
| 6 | `examples/settings/` | 了解安全策略配置 |
| 7 | `scripts/` + `.github/workflows/` | 了解 Issue 自动化 |

---

## 八、术语表

| 术语 | 含义 |
|------|------|
| **Hook** | 生命周期拦截点，外部脚本可在关键节点注入逻辑 |
| **Plugin** | 打包了 Hook/Command/Agent/Skill 的扩展单元 |
| **Command** | 用户通过 `/` 前缀主动触发的斜杠命令 |
| **Agent** | 可被委托自主执行的子代理 |
| **Skill** | 注入领域知识的 Markdown 文件 |
| **IPC** | 进程间通信，Hook 通过 stdin/stdout JSON 交换数据 |
| **MDM** | 移动设备管理，企业远程管控工具 |
| **matcher** | Hook 配置中的工具名称过滤器 |
| **frontmatter** | Markdown 文件顶部的 YAML 元数据区域 |
| **baseline_sha** | Git 基线 SHA，用于计算增量 diff |
| **additionalContext** | Hook 输出中注入到 AI 上下文的额外信息 |
