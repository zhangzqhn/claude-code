# Learn Claude Code — 从源码到实战的完整课程

> 基于 Claude Code 开源项目源码整理的深度学习课程，涵盖 19 个核心主题，每个主题包含架构设计、源码分析、配置示例和实战要点。

---

## 目录

- [S01 Agent Loop — 智能体循环](#s01-agent-loop--智能体循环)
- [S02 Tool Use — 工具使用系统](#s02-tool-use--工具使用系统)
- [S03 Permission — 权限系统](#s03-permission--权限系统)
- [S04 Hooks — 钩子系统](#s04-hooks--钩子系统)
- [S05 TODO Write — 任务写入系统](#s05-todo-write--任务写入系统)
- [S06 Subagent — 子代理系统](#s06-subagent--子代理系统)
- [S07 Skill Loading — 技能加载系统](#s07-skill-loading--技能加载系统)
- [S08 Context Compact — 上下文压缩](#s08-context-compact--上下文压缩)
- [S09 Memory — 记忆系统](#s09-memory--记忆系统)
- [S10 System Prompt — 系统提示词](#s10-system-prompt--系统提示词)
- [S11 Error Recovery — 错误恢复](#s11-error-recovery--错误恢复)
- [S12 Task System — 任务协调系统](#s12-task-system--任务协调系统)
- [S13 Background Tasks — 后台任务](#s13-background-tasks--后台任务)
- [S14 Cron Scheduler — 定时调度器](#s14-cron-scheduler--定时调度器)
- [S15 Agent Teams — 多智能体团队](#s15-agent-teams--多智能体团队)
- [S16 Team Protocols — 团队通信协议](#s16-team-protocols--团队通信协议)
- [S17 Autonomous Agents — 自治智能体](#s17-autonomous-agents--自治智能体)
- [S18 Worktree Isolation — 工作树隔离](#s18-worktree-isolation--工作树隔离)
- [S19 MCP Plugin — Model Context Protocol 插件](#s19-mcp-plugin--model-context-protocol-插件)

---

# S01 Agent Loop — 智能体循环

## 1.1 核心概念

Claude Code 的本质是一个 **Agent Loop（智能体循环）**——一个持续运行的"思考-行动-观察"循环：

```
用户输入 → AI 思考 → 选择工具 → 执行工具 → 观察结果 → 继续思考 → ... → 任务完成
```

这不是简单的请求-响应模式，而是一个**自主迭代**的过程：AI 在每一轮决定是继续调用工具还是结束任务。

## 1.2 两层循环架构

Claude Code 存在两层循环：

### 主循环（Main Agent Loop）

Claude Code 核心引擎执行的循环，Hook 系统提供了 4 个拦截点：

```
用户输入提示词
    │
    ▼
[UserPromptSubmit Hook] ← 可注入上下文/捕获基线
    │ (放行)
    ▼
═══ 工具调用循环开始 ═══
    │
    ▼
[PreToolUse Hook] ← 可拦截/阻止工具调用
    │ (放行)          │ (deny)
    ▼                  ▼
  工具执行          通知 AI 调整行为
    │
    ▼
[PostToolUse Hook] ← 可审查结果/注入安全警告
    │
    ▼
─── 循环或结束 ───
    │
    ▼
[Stop Hook] ← 可阻止 AI 过早停止
    │ (approve)     │ (block)
    ▼                ▼
 返回最终结果      继续工作(回到工具调用循环)
```

### 外层循环（Ralph Loop）

通过 `ralph-wiggum` 插件实现，是一个基于 Stop Hook 的**自引用迭代循环**：

```bash
# 核心机制：
# 1. Claude 工作于任务
# 2. Claude 尝试停止
# 3. Stop Hook 阻止退出，将同一 prompt 再次注入
# 4. Claude 看到已修改的文件，继续改进
# 5. 重复直到输出 <promise>COMPLETE</promise> 或达到最大迭代
```

**源码位置**: `plugins/ralph-wiggum/hooks/stop-hook.sh`

```bash
# stop-hook.sh 核心逻辑（简化）
STATE_FILE=".claude/ralph-loop.local.md"

if [ -f "$STATE_FILE" ]; then
    # 解析 frontmatter 获取迭代信息
    ITERATION=$(parse_frontmatter "iteration" "$STATE_FILE")
    MAX_ITERATIONS=$(parse_frontmatter "max_iterations" "$STATE_FILE")
    PROMPT=$(parse_body "$STATE_FILE")
    PROMISE=$(parse_frontmatter "completion_promise" "$STATE_FILE")

    # 检查是否完成
    if echo "$TRANSCRIPT" | grep -q "<promise>$PROMISE</promise>"; then
        # 完成，允许停止
        rm "$STATE_FILE"
        exit 0
    fi

    # 检查是否超过最大迭代
    if [ "$ITERATION" -ge "$MAX_ITERATIONS" ]; then
        exit 0  # 允许停止
    fi

    # 阻止停止，注入提示继续工作
    jq -n --arg prompt "$PROMPT" --arg msg "Iteration $ITERATION/$MAX_ITERATIONS" \
        '{"decision": "block", "reason": $prompt, "systemMessage": $msg}'
fi
```

## 1.3 Agent Loop 的状态管理

### 会话状态文件

**源码位置**: `plugins/security-guidance/hooks/session_state.py`

```python
# 会话状态数据结构
STATE_SCHEMA = {
    "baseline_sha": str,       # Git 基线 SHA，追踪增量变更
    "touched_paths": list,     # 本次会话编辑的文件列表
    "fire_count": int,         # Stop Hook 触发次数（防止无限循环）
    "pending_warnings": list,  # 待处理的模式警告
    "shown_warnings": list,    # 已显示的警告（用于去重）
    "rate_limits": dict,       # 滚动窗口限速计数
}
```

### 文件锁原子性操作

```python
def with_locked_state(session_id, callback):
    """文件锁保护的状态读写"""
    lock_path = state_path + ".lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)    # 加锁
        state = _load_state(state_path)      # 读取
        result = callback(state)              # 执行回调
        _save_state(state_path, state)       # 保存
        fcntl.flock(lock, fcntl.LOCK_UN)      # 解锁
    return result
```

## 1.4 停止条件与迭代控制

| 控制参数 | 来源 | 作用 |
|---------|------|------|
| `maxTurns` | Agent frontmatter | 限制子代理最大迭代轮次 |
| `stopReason` | API 响应 | AI 主动判断任务完成 |
| `--max-turns` | CLI 标志 | 全局最大轮次限制 |
| `completion_promise` | Ralph Loop | 精确字符串匹配退出条件 |
| `max_iterations` | Ralph Loop 状态文件 | 防止无限循环 |
| Stop Hook `decision: "block"` | Hook 输出 | 阻止过早停止 |

## 1.5 实战要点

1. **Agent Loop 是所有功能的基石**：Hook、Permission、Tool Use 都嵌套在循环内部
2. **两层循环的设计值得借鉴**：内层是原子级"思考-行动"循环，外层是迭代级"改进-验证"循环
3. **Stop Hook 是实现自治循环的关键**：通过拦截退出信号实现"不让 AI 停下来"
4. **状态管理必须原子化**：文件锁 + JSON 是简单有效的跨进程状态共享方案

---

# S02 Tool Use — 工具使用系统

## 2.1 核心架构

工具系统基于 Anthropic API 的 `tool_use` / `tool_result` 协议：

```
AI 生成 tool_use block (含 tool_name + tool_input)
    │
    ▼
[PreToolUse Hook] ── 权限检查 + 安全拦截
    │
    ▼
[权限系统] ── 用户确认 / 自动批准 / 拒绝
    │
    ▼
工具执行器 ── 实际执行
    │
    ▼
[PostToolUse Hook] ── 结果审查
    │
    ▼
生成 tool_result block (返回给 AI)
```

## 2.2 内置工具完整列表

| 工具 | tool_input 结构 | 说明 |
|------|----------------|------|
| **Bash** | `{command: str, timeout?, run_in_background?}` | 执行 Shell 命令 |
| **Read** | `{file_path, offset?, limit?}` | 读取文件 |
| **Write** | `{file_path, content}` | 写入整个文件 |
| **Edit** | `{file_path, old_string, new_string, replace_all?}` | 搜索替换编辑 |
| **MultiEdit** | `{file_path, edits: [{old_string, new_string}]}` | 批量编辑文件 |
| **Grep** | `{pattern, path?, type?, output_mode?}` | 搜索代码内容 |
| **Glob** | `{pattern, path?, limit?, offset?}` | 文件名模式匹配 |
| **WebFetch** | `{url, prompt}` | 抓取并分析网页 |
| **WebSearch** | `{query, topic?}` | 网络搜索 |
| **TaskCreate** | `{subject, description, activeForm?}` | 创建任务 |
| **TaskUpdate** | `{taskId, status?, description?, ...}` | 更新任务 |
| **TaskList** | `{}` | 列出所有任务 |
| **TaskGet** | `{taskId}` | 获取任务详情 |
| **Agent** | `{prompt, subagent_type?, mode?, ...}` | 启动子代理 |
| **Skill** | `{skill, args?}` | 触发技能 |
| **ToolSearch** | `{tool_names?, queries?}` | 搜索延迟加载的 MCP 工具 |
| **DeferExecuteTool** | `{toolName, params}` | 执行延迟加载的工具 |
| **NotebookEdit** | `{notebook_path, cell_number, new_source}` | 编辑 Jupyter Notebook |

## 2.3 工具参数提取策略

**源码位置**: `plugins/hookify/core/rule_engine.py` 第 236-326 行

```python
def _extract_field(self, tool_name, tool_input, field):
    """根据工具类型提取对应字段"""
    if tool_name == 'Bash':
        return tool_input.get('command', '')
    elif tool_name in ['Write', 'Edit']:
        if field in tool_input:
            return tool_input[field]
    elif tool_name == 'MultiEdit':
        # 拼接所有 edits 的 new_string
        edits = tool_input.get('edits', [])
        return ' '.join(e.get('new_string', '') for e in edits)
    return ''
```

## 2.4 工具权限配置

### allowed-tools 语法（Command/Agent frontmatter）

```yaml
---
allowed-tools: Bash(gh pr comment:*), Bash(gh pr diff:*), Read, Grep
description: Code review a pull request
---
```

支持的模式：
- `Bash(gh pr comment:*)` — 限定 Bash 只能执行特定命令模式
- `Read|Write|Edit` — 多种工具组合
- `Task(AgentName)` — 限定只能启动特定类型的子代理

### disallowed-tools（禁用特定工具）

```yaml
---
disallowed-tools: WebSearch, WebFetch, Bash(rm:*)
description: Safe read-only agent
---
```

`--disallowedTools` CLI 标志和 `settings.json` 中的 `disallowedTools` 字段也可以禁用特定工具。

## 2.5 工具错误处理

| 错误类型 | 处理方式 |
|---------|---------|
| tool_use/tool_result ID 不匹配 | 自动修复映射关系 |
| 并行工具结果丢失 | 修复 `--resume` 丢失并行工具结果 |
| MCP 工具超时 | `MCP_TOOL_TIMEOUT` 配置（默认 60s） |
| 工具延迟加载 | `ToolSearch` 发现 → `DeferExecuteTool` 执行 |
| Bash 命令交互式挂起 | ~45 秒后发出通知，可 Ctrl+B 后台化 |
| 工具输出过大 | Hook 输出 >50K 字符时保存到磁盘 |

## 2.6 实战要点

1. **工具是 AI 与世界交互的唯一通道**：所有能力都通过工具暴露，权限控制也在工具层面
2. **allowed-tools 的模式匹配语法**：`Bash(git:*)` 这种语法精确控制工具使用范围
3. **Hook 在工具执行的两侧拦截**：Pre 控制是否执行，Post 审查执行结果
4. **延迟加载机制**：当 MCP 工具描述超过上下文窗口 10% 时，自动启用延迟加载

---

# S03 Permission — 权限系统

## 3.1 多层防御架构

```
┌────────────────────────────────────────────────────┐
│  第0层：MDM 企业策略 (最高优先级，用户不可覆盖)       │
├────────────────────────────────────────────────────┤
│  第1层：managed-settings.json (IT 管理员配置)       │
├────────────────────────────────────────────────────┤
│  第2层：settings.json / settings.local.json        │
├────────────────────────────────────────────────────┤
│  第3层：Hook 系统拦截 (PreToolUse Hook)            │
├────────────────────────────────────────────────────┤
│  第4层：运行时权限提示 (用户交互确认)                │
├────────────────────────────────────────────────────┤
│  第5层：沙箱隔离 (Bash 命令沙箱化)                  │
└────────────────────────────────────────────────────┘
```

## 3.2 权限模式

| 模式 | 说明 | 安全级别 | 启用方式 |
|------|------|---------|---------|
| **plan** | 只规划不执行，需用户审批后执行 | 最高 | `/plan` 或 `--permission-mode plan` |
| **ask** | 每次工具调用需用户确认 | 高 | 默认模式 |
| **acceptEdits** | 自动接受文件编辑，其他操作仍需确认 | 中 | Shift+Tab |
| **auto** | AI 自动分类决定是否需要确认 | 中低 | `--permission-mode auto` |
| **bypassPermissions** | 跳过所有权限检查 | 最低 | `--dangerously-skip-permissions` |

## 3.3 权限规则配置

**源码位置**: `examples/settings/settings-strict.json`

```json
{
  "permissions": {
    "disableBypassPermissionsMode": "disable",
    "allow": ["Bash(git:*)", "Read"],
    "ask": ["Bash"],
    "deny": ["WebSearch", "WebFetch"]
  }
}
```

**规则优先级**：`deny` > `ask` > `allow`。即使用户在 `allow` 中配置了某工具，如果托管设置中 `ask` 或 `deny` 了该工具，托管设置优先。

## 3.4 沙箱权限集成

**源码位置**: `examples/settings/settings-bash-sandbox.json`

```json
{
  "sandbox": {
    "enabled": true,
    "autoAllowBashIfSandboxed": false,
    "allowUnsandboxedCommands": false,
    "excludedCommands": [],
    "network": {
      "allowUnixSockets": [],
      "allowAllUnixSockets": false,
      "allowLocalBinding": false,
      "allowedDomains": [],
      "deniedDomains": [],
      "httpProxyPort": null,
      "socksProxyPort": null
    },
    "filesystem": {
      "allowWrite": [],
      "denyRead": [],
      "allowRead": []
    }
  }
}
```

沙箱关键配置项：
- `network.allowedDomains` / `deniedDomains` — 网络白名单/黑名单
- `filesystem.allowWrite` — 文件系统写白名单
- `autoAllowBashIfSandboxed` — 沙箱中是否自动允许 Bash
- `dangerouslyDisableSandbox` — 单个命令跳过沙箱

## 3.5 MDM 企业部署

### macOS

**源码位置**: `examples/mdm/macos/com.anthropic.claudecode.mobileconfig`

```xml
<key>permissions</key>
<dict>
    <key>disableBypassPermissionsMode</key>
    <string>disable</string>
</dict>
```

### Windows

**源码位置**: `examples/mdm/windows/ClaudeCode.admx`

写入注册表 `HKLM\SOFTWARE\Policies\ClaudeCode\Settings`（REG_SZ，单行 JSON），通过 Group Policy 或 Intune ADMX 部署。

### PowerShell 部署脚本

**源码位置**: `examples/mdm/windows/Set-ClaudeCodePolicy.ps1`

```powershell
# 写入托管设置到 Program Files
$managedSettingsPath = "C:\Program Files\ClaudeCode\managed-settings.json"
$policy = @{
    permissions = @{
        disableBypassPermissionsMode = "disable"
        deny = @("WebSearch", "WebFetch")
    }
} | ConvertTo-Json -Depth 10
Set-Content -Path $managedSettingsPath -Value $policy
```

## 3.6 企业级权限管控

```json
{
  "allowManagedPermissionRulesOnly": true,
  "allowManagedHooksOnly": true,
  "strictKnownMarketplaces": [],
  "permissions": {
    "disableBypassPermissionsMode": "disable",
    "ask": ["Bash"],
    "deny": ["WebSearch", "WebFetch"]
  }
}
```

**关键安全约束**：
- `deny` 规则优先级最高，Hook 返回 `allow` 也不能覆盖 `deny`
- 用户 `allow` 规则不能覆盖托管 `ask` 规则
- `allowManagedPermissionRulesOnly: true` 阻止用户自定义 `allow`/`ask`/`deny`

## 3.7 实战要点

1. **多层防御是核心设计**：MDM > 托管设置 > 用户设置 > Hook > 运行时 > 沙箱
2. **deny 优先原则**：一旦 deny，任何层级都无法覆盖
3. **沙箱是最后的防线**：即使权限通过了，Bash 命令仍在沙箱中受限执行
4. **MDM 部署让企业管控成为可能**：Windows ADMX + macOS mobileconfig 覆盖主流平台

---

# S04 Hooks — 钩子系统

## 4.1 架构概述

Hook 系统是 Claude Code 的核心扩展机制，采用 **stdin/stdout JSON IPC 协议**，Hook 脚本作为独立子进程执行：

```
Claude Code (主进程)
    │
    ├── stdin  ──→ 传入 JSON 数据
    ├── stdout ←── 接收 JSON 响应
    ├── stderr ←── 接收提示消息
    └── 退出码  ←── 0=放行 / 1=警告 / 2=阻断
```

## 4.2 完整 Hook 事件列表

| 事件 | 触发时机 | 核心用途 | Matcher |
|------|---------|---------|---------|
| **PreToolUse** | 工具执行前 | 拦截/修改/阻止工具调用 | 工具名 |
| **PostToolUse** | 工具执行后 | 审查结果/注入警告 | 工具名 |
| **Stop** | 主代理准备停止 | 阻止过早停止 | `*` |
| **SubagentStop** | 子代理准备停止 | 确保子代理完成 | `*` |
| **UserPromptSubmit** | 用户提交提示词 | 注入上下文/校验 | `*` |
| **SessionStart** | 会话开始 | 加载上下文/设置环境 | `*` |
| **SessionEnd** | 会话结束 | 清理/日志 | `*` |
| **PreCompact** | 上下文压缩前 | 保留关键信息 | `*` |
| **Notification** | 通知发送时 | 日志/响应 | `*` |
| **TeammateIdle** | 团队成员空闲 | 任务分配 | `*` |
| **TaskCompleted** | 任务完成时 | 流程推进 | `*` |
| **TaskCreated** | 任务创建时 | 阻止/注入上下文 | `*` |
| **WorktreeCreate** | 工作树创建 | 自定义工作树 | `*` |
| **WorktreeRemove** | 工作树移除 | 清理 | `*` |
| **PermissionRequest** | 权限请求 | 修改权限决策 | 工具名 |
| **PermissionDenied** | 权限被拒 | 允许重试 | 工具名 |
| **InstructionsLoaded** | CLAUDE.md 加载后 | 后处理 | `*` |
| **MessageDisplay** | 消息显示 | 转换/隐藏消息 | `*` |
| **CwdChanged** | 工作目录变化 | 环境管理 | `*` |
| **FileChanged** | 文件变化 | 反应式管理 | `*` |
| **StopFailure** | API 错误导致停止 | 错误恢复 | `*` |
| **Setup** | 初始化设置 | 环境配置 | `*` |

## 4.3 两种通信协议

### 简单协议（stderr + 退出码）

```python
# 放行
sys.exit(0)

# 警告（用户可见，AI 不可见）
print("建议使用 rg 替代 grep", file=sys.stderr)
sys.exit(1)

# 阻止（AI 可见并调整行为）
print("检测到危险命令 rm -rf", file=sys.stderr)
sys.exit(2)
```

**源码示例**: `examples/hooks/bash_command_validator_example.py`

```python
_VALIDATION_RULES = [
    (r"^grep\b(?!.*\|)", "Use 'rg' instead of 'grep'"),
    (r"^find\s+\S+\s+-name\b", "Use 'rg --files' instead of 'find -name'"),
]

for pattern, message in _VALIDATION_RULES:
    if re.search(pattern, command):
        print(message, file=sys.stderr)
        sys.exit(2)  # 阻止

sys.exit(0)  # 放行
```

### JSON 协议（stdout）

```json
// 放行
{}

// 警告
{"systemMessage": "警告消息"}

// 阻止 - PreToolUse
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "updatedInput": {"field": "修改后的值"}   // 可选：修改工具输入
  },
  "systemMessage": "阻止原因"
}

// 阻止 - Stop
{
  "decision": "block",
  "reason": "继续工作的提示词",
  "systemMessage": "阻止原因"
}

// 注入上下文
{
  "hookSpecificOutput": {
    "additionalContext": "安全警告信息"
  }
}

// 替换工具输出
{
  "hookSpecificOutput": {
    "updatedToolOutput": "修改后的输出"
  }
}
```

## 4.4 Matcher 匹配模式

```json
"matcher": "Bash"              // 精确匹配
"matcher": "Edit|Write"        // 多工具匹配（管道符分隔）
"matcher": "*"                 // 通配符（所有工具）
"matcher": "mcp__.*__delete.*" // 正则模式
```

## 4.5 高级 Hook 特性

### asyncRewake（异步唤醒）

**源码位置**: `plugins/security-guidance/hooks/hooks.json`

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "if": "Bash(git commit:*)",
        "asyncRewake": true,
        "hooks": ["${CLAUDE_PLUGIN_ROOT}/hooks/posttooluse.py"]
      }
    ],
    "Stop": [
      {
        "asyncRewake": true,
        "hooks": ["${CLAUDE_PLUGIN_ROOT}/hooks/stop.py"]
      }
    ]
  }
}
```

- `asyncRewake: true`：Hook 完成后异步发送结果，不阻塞当前流程
- `if` 条件：如 `Bash(git commit:*)` 只在特定命令时触发
- `continueOnBlock`：PostToolUse 专属选项，block 后继续当前 turn

## 4.6 Hook 注册格式

**源码位置**: `plugins/*/hooks/hooks.json`

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": ["${CLAUDE_PLUGIN_ROOT}/hooks/sessionstart.py"],
        "type": "command"
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash|Edit|Write",
        "hooks": ["${CLAUDE_PLUGIN_ROOT}/hooks/pretooluse.py"],
        "type": "command"
      }
    ]
  }
}
```

## 4.7 环境变量

| 变量 | 可用范围 | 说明 |
|------|---------|------|
| `$CLAUDE_PLUGIN_ROOT` | command hooks | 插件根目录（便携路径） |
| `$CLAUDE_PROJECT_DIR` | command hooks | 项目根路径 |
| `$CLAUDE_ENV_FILE` | SessionStart only | 持久化环境变量 |
| `$CLAUDE_CODE_REMOTE` | command hooks | 远程运行标识 |
| `$CLAUDE_SESSION_ID` | Bash subprocess | 会话 ID |
| `$CLAUDE_EFFORT` | Bash/Hooks | 当前努力级别 |

## 4.8 设计原则

| 原则 | 实现 |
|------|------|
| **容错优先** | `try/except/finally: sys.exit(0)` — Hook 错误永不阻断主流程 |
| **快速响应** | 默认 10 秒超时，防止 Hook 阻塞 |
| **最小权限** | `matcher` 过滤只拦截特定工具 |
| **幂等设计** | 同一输入多次执行结果一致 |
| **零依赖** | 仅使用 Python 标准库 |
| **可观测性** | stderr 日志记录关键信息 |

## 4.9 实战要点

1. **两种协议选择**：简单场景用 stderr+退出码，复杂场景用 JSON 协议
2. **容错是第一原则**：Hook 必须在 finally 中 `sys.exit(0)`，绝不能因 Hook 崩溃影响主流程
3. **asyncRewake 解决慢操作**：LLM 审查等耗时操作用异步唤醒，不阻塞当前工具调用
4. **20+ 个 Hook 事件**：几乎在每个关键节点都有拦截点，扩展能力极强

---

# S05 TODO Write — 任务写入系统

## 5.1 核心工具集

| 工具 | 功能 | 关键参数 |
|------|------|---------|
| **TaskCreate** | 创建新任务 | `subject`, `description`, `activeForm`, `owner` |
| **TaskUpdate** | 更新任务状态 | `taskId`, `status`, `addBlocks`, `addBlockedBy`, `metadata` |
| **TaskList** | 列出所有任务摘要 | 无参数 |
| **TaskGet** | 获取单个任务详情 | `taskId` |

## 5.2 任务数据模型

```json
{
  "subject": "Fix authentication bug",
  "description": "Implement a toggle component... 验收标准...",
  "activeForm": "Fixing authentication bug",
  "status": "pending | in_progress | completed | deleted",
  "owner": "agent-name",
  "addBlocks": ["taskId2"],
  "addBlockedBy": ["taskId1"],
  "metadata": {
    "priority": "high",
    "pr_number": 1234
  }
}
```

**字段说明**：
- `subject`：祈使句标题（如 "Run tests"）
- `activeForm`：进行时形式，in_progress 时显示于 spinner（如 "Running tests"）
- `addBlocks`：此任务阻塞哪些任务
- `addBlockedBy`：此任务被哪些任务阻塞
- `metadata`：任意键值对，设置值为 null 可删除键

## 5.3 任务生命周期

```
pending → in_progress → completed
                     ↘ deleted
```

**关键规则**：
- `blockedBy` 列表不为空的任务不能被认领
- 完成阻塞任务后，需检查 TaskList 查看新解除阻塞的任务
- 建议按 ID 顺序认领任务（低 ID 先做）

## 5.4 任务依赖系统

```
Task 1: 设置数据库 (pending)
    │
    ├── blocks ──→ Task 2: 创建用户模型 (blockedBy: [1])
    │                   │
    │                   ├── blocks ──→ Task 3: 实现认证 (blockedBy: [2])
    │                   │
    │                   └── blocks ──→ Task 4: 编写测试 (blockedBy: [2])
    │
    └── blocks ──→ Task 5: 配置 CI (blockedBy: [1])
```

## 5.5 与 Agent Loop 的集成

```
用户输入 → AI 思考 → [TaskCreate 创建多个任务]
    → [TaskUpdate 标记第一个 in_progress]
    → 选择工具 → 执行工具 → [TaskUpdate 标记 completed]
    → [TaskList 检查下一个可认领任务]
    → 继续执行 → ... → 所有任务完成
```

## 5.6 TaskCreated Hook

```json
// TaskCreate 触发时
{
  "hook_event_name": "TaskCreated",
  "task_id": "1",
  "subject": "Fix auth bug",
  "description": "..."
}
```

Hook 可以阻止任务创建或注入额外上下文。

## 5.7 实战要点

1. **任务系统是规划→执行追踪的核心**：先规划创建任务，再逐个执行
2. **依赖系统让复杂工作流有序**：`blockedBy` 确保前置任务完成后再开始
3. **`activeForm` 让 UI 更友好**：进行时形式在 spinner 中显示进度
4. **metadata 可扩展**：任何额外信息都可以存入 metadata

---

# S06 Subagent — 子代理系统

## 6.1 Agent 定义文件格式

**源码位置**: `plugins/plugin-dev/skills/agent-development/SKILL.md`

```markdown
---
name: agent-identifier           # 必需：标识符，小写+连字符
description: Use this agent when [触发条件]. Examples:

<example>
Context: [场景描述]
user: "[用户请求]"
assistant: "[AI 响应]"
<commentary>[为何触发此 agent]</commentary>
</example>

model: inherit|sonnet|opus|haiku  # 必需：使用的模型
color: blue|cyan|green|yellow|magenta|red  # 必需：UI 颜色
tools: ["Read", "Write", "Grep"]  # 可选：限制工具范围
isolation: worktree               # 可选：在独立工作树中运行
maxTurns: 20                     # 可选：最大迭代轮次
effort: high                     # 可选：努力级别
mcpServers:                       # 可选：内联 MCP 服务器
  my-server:
    command: npx
    args: ["-y", "my-mcp-server"]
memory: project                   # 可选：记忆作用域 user/project/local
background: true                  # 可选：始终作为后台任务运行
---

You are [agent 角色描述]...

**Your Core Responsibilities:**
1. [职责 1]

**Analysis Process:**
[步骤式工作流]

**Output Format:**
[返回格式]
```

## 6.2 真实 Agent 示例

### code-explorer（只读分析型）

**源码位置**: `plugins/feature-dev/agents/code-explorer.md`

```yaml
---
name: code-explorer
description: Deeply analyzes existing codebase features by tracing execution paths...
tools: Glob, Grep, LS, Read, NotebookRead, WebFetch, TodoWrite, WebSearch, KillShell, BashOutput
model: sonnet
color: yellow
---
```

### code-reviewer（高精度审查型）

**源码位置**: `plugins/pr-review-toolkit/agents/code-reviewer.md`

```yaml
---
name: code-reviewer
description: Use this agent when you need to review code for adherence to project guidelines...
model: opus        # Opus 模型，最高精度
color: green
---
```

## 6.3 Agent Tool 调用方式

```
Agent({
  prompt: "审查这段代码的安全性",      // 任务描述
  subagent_type: "code-reviewer",     // Agent 类型（大小写不敏感）
  mode: "bypassPermissions",           // 权限模式
  model: "reasoning",                  // 模型变体
  name: "security-reviewer",           // 名称（用于团队通信）
  run_in_background: true,             // 后台运行
  max_turns: 20                       // 最大迭代轮次
})
```

## 6.4 子代理权限模式

| 模式 | 说明 | 行为 |
|------|------|------|
| **default** | 标准权限 | 需用户审批 |
| **plan** | 计划模式 | 只读访问 |
| **acceptEdits** | 自动批准编辑 | 文件编辑自动批准 |
| **bypassPermissions** | 跳过权限 | 所有操作自动批准 |

**关键约束**：
- Teammate 自动继承 Leader 的权限模式
- `disableBypassPermissionsMode: "disable"` 可阻止绕过

## 6.5 SubagentStop Hook

子代理准备停止时触发，可阻止过早停止：

```json
// Hook 输入
{
  "hook_event_name": "SubagentStop",
  "reason": "Task completed",
  "session_id": "abc123",
  "background_tasks": [...],    // v2.1.145 新增
  "session_crons": [...]         // v2.1.145 新增
}

// Hook 输出 - 阻止停止
{
  "decision": "block",
  "reason": "还有未完成的测试",
  "systemMessage": "请继续执行"
}
```

## 6.6 Worktree 隔离

```yaml
---
name: isolated-agent
isolation: worktree    # 声明式：agent 自动在独立工作树中运行
---
```

- 自动从 `origin/<default>` 创建新分支
- 在独立工作目录中执行，防止干扰主工作副本
- 完成后可清理 worktree

## 6.7 实战要点

1. **Agent 定义是 Markdown + YAML frontmatter**：极低门槛，非程序员也能定义 Agent
2. **模型选择很关键**：分析用 Sonnet（性价比），审查用 Opus（精度），简单任务用 Haiku（速度）
3. **工具限制控制能力边界**：`tools: ["Read", "Grep"]` 创建只读 Agent
4. **isolation: worktree 是安全网**：防止 Agent 修改主工作目录
5. **SubagentStop Hook 确保质量**：可以阻止 Agent 在未完成时停止

---

# S07 Skill Loading — 技能加载系统

## 7.1 Skill 的定义

Skill 是**知识注入模块**，为 AI 提供领域专业指导。可以理解为"给 AI 的专项培训手册"：

> "Skills transform Claude from a general-purpose agent into a specialized agent equipped with procedural knowledge that no model can fully possess."
> — `plugins/plugin-dev/skills/skill-development/SKILL.md`

## 7.2 Skill 目录结构

```
skill-name/
├── SKILL.md          (必需) — YAML frontmatter + Markdown 指令
└── Bundled Resources (可选)
    ├── scripts/      — 可执行代码（Python/Bash 等）
    ├── references/   — 按需加载的参考文档
    └── assets/       — 输出中使用的文件（模板、图标等）
```

## 7.3 SKILL.md Frontmatter 格式

```yaml
---
name: Skill Name                    # 必需
description: This skill should be used when the user asks to "specific phrase 1", "specific phrase 2"...
version: 0.1.0                      # 可选
context: fork                       # 可选：在子代理上下文中运行
effort: high                        # 可选：覆盖努力级别
allowed-tools: Bash(git:*)          # 可选：限制可用工具
disallowed-tools: WebSearch         # 可选：移除特定工具
---
```

**description 写作要求**：
- 必须使用第三人称："This skill should be used when..."
- 必须包含具体触发短语：用户可能说的精确措辞
- 不能使用模糊描述

## 7.4 Skill 三级层级

| 层级 | 路径 | 作用域 | 特点 |
|------|------|--------|------|
| **用户级** | `~/.claude/skills/` | 所有项目 | 个人定制 |
| **项目级** | `.claude/skills/` | 当前项目 | 团队共享 |
| **插件级** | `plugin-name/skills/` | 安装插件后 | 随插件分发 |

## 7.5 渐进式披露设计

```
Level 1: Metadata (name + description) → 始终在上下文中 (~100词)
Level 2: SKILL.md body               → Skill 触发时加载 (<5k词)
Level 3: Bundled resources            → 按需加载 (无限制)
    ├── scripts/  → 可直接执行，无需加载到上下文
    ├── references/ → Claude 判断需要时才读取
    └── assets/  → 在输出中使用，不加载到上下文
```

**SKILL.md body 目标长度**：1,500-2,000 词（最大 <5,000 词）

## 7.6 触发机制

1. **用户主动**：用户使用斜杠命令 `/skill-name` 触发
2. **Claude 主动**：Claude 根据 description 中的触发短语自动激活
3. **嵌套触发**：其他 Skill 通过 `context: fork` 触发

**OTel 事件追踪**：
```
claude_code.skill_activated
  invocation_trigger = "user-slash" | "claude-proactive" | "nested-skill"
```

## 7.7 热重载

```bash
# 自动热重载 — 修改后立即可用，无需重启
# 适用于 ~/.claude/skills/ 和 .claude/skills/

# 手动重新扫描
/reload-skills
```

SessionStart Hook 也可以返回 `reloadSkills: true` 触发重新扫描。

## 7.8 项目中的 Skill 示例

| Skill 名称 | 插件 | 用途 |
|------------|------|------|
| hook-development | plugin-dev | Hook 开发完整 API 指导 |
| agent-development | plugin-dev | Agent 定义与创建指导 |
| command-development | plugin-dev | 斜杠命令开发指导 |
| mcp-integration | plugin-dev | MCP 服务器集成指导 |
| plugin-settings | plugin-dev | 插件配置指导 |
| plugin-structure | plugin-dev | 插件目录组织指导 |
| skill-development | plugin-dev | Skill 创建方法论 |
| writing-rules | hookify | Hookify 规则编写指导 |
| frontend-design | frontend-design | 高品质前端设计指导 |
| claude-opus-4-5-migration | claude-opus-4-5-migration | 模型迁移指导 |

## 7.9 实战要点

1. **Skill 是"知识注入"而非"代码执行"**：本质是给 AI 提供领域专业指导
2. **渐进式披露节省上下文**：100 词元数据始终在，5k 词正文按需加载
3. **description 的精确触发短语是关键**：决定了 AI 何时自动激活该 Skill
4. **`context: fork` 隔离 Skill 执行**：防止 Skill 执行污染主对话上下文
5. **热重载让开发体验流畅**：修改 SKILL.md 后立即可用

---

# S08 Context Compact — 上下文压缩

## 8.1 触发机制

| 触发方式 | 说明 |
|---------|------|
| **Auto-compact** | 上下文接近 token 限制时自动触发 |
| **手动** | 执行 `/compact` 命令 |
| **Rewind** | "Summarize up to here" 选项 |
| **响应式** | API 返回 "prompt too long" 时触发 |

## 8.2 压缩策略

Claude Code 使用**摘要化（Summarization）**策略，而非简单截断：

```
1. 请求模型生成摘要
   ↓
2. 将历史对话交给模型，要求生成精简摘要
   ↓
3. 用摘要替换原始对话历史
   ↓
4. 保留近期几轮对话原样
   ↓
5. CLAUDE.md 内容在压缩后重新注入
```

**压缩提示词改进**（v2.1.139）：
> "Compaction prompt now asks the model to preserve sensitive user instructions"

## 8.3 PreCompact Hook

上下文压缩前触发，可用于在压缩前注入需要保留的关键信息：

```json
// Hook 输入
{
  "hook_event_name": "PreCompact",
  "session_id": "abc123"
}

// Hook 输出 - 阻止压缩
{
  "decision": "block",
  "reason": "关键操作尚未完成，请勿压缩上下文"
}

// Hook 输出 - 注入保留信息
{
  "hookSpecificOutput": {
    "additionalContext": "必须保留的关键信息：..."
  }
}
```

## 8.4 相关命令

| 命令 | 功能 |
|------|------|
| `/compact` | 手动触发上下文压缩 |
| `/context` | 查看当前上下文使用情况 |
| `/context all` | 显示所有 Skill 的详细 token 估算 |

## 8.5 断路器机制

```
连续压缩 3 次 → 上下文立即填满 → 检测到抖动循环
    ↓
停止自动压缩，显示可操作的错误信息
    ↓
"请使用 /compact 手动压缩，或简化当前任务"
```

**源码引用**（CHANGELOG）：
> "Fixed autocompact thrash loop — now detects when context refills to the limit immediately after compacting three times in a row and stops with an actionable error"

## 8.6 压缩对各种内容的影响

| 内容类型 | 压缩后行为 |
|---------|-----------|
| 早期对话 | 被摘要化替代，丢失具体细节 |
| 近期对话 | 保持原样 |
| CLAUDE.md | 压缩后重新注入 |
| 敏感用户指令 | 压缩提示词要求保留 |
| 工具调用结果 | 可能被精简或移除 |
| Skill 内容 | 压缩前加载的可能需重新触发 |

## 8.7 实战要点

1. **摘要化优于截断**：保留语义而非丢掉信息
2. **CLAUDE.md 是压缩安全的**：每次压缩后都会重新注入
3. **PreCompact Hook 是信息保护口**：可在压缩前注入必须保留的信息
4. **断路器防止 API 费用浪费**：3 次连续填满后停止自动压缩
5. **1M 上下文模型也要注意**：autocompact 阈值需要与模型上下文窗口匹配

---

# S09 Memory — 记忆系统

## 9.1 四种记忆机制

```
┌─────────────────────────────────────────────────────────────┐
│                    Claude Code 记忆系统                       │
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │  CLAUDE.md    │  │  Transcript  │  │   Auto-Memory      │  │
│  │  (静态记忆)   │  │  (动态记忆)   │  │   (自动记忆)       │  │
│  ├──────────────┤  ├──────────────┤  ├────────────────────┤  │
│  │ 启动时加载    │  │ 运行时追加   │  │ AI 主动保存        │  │
│  │ 多级层级      │  │ JSONL 格式   │  │ 跨会话持久化       │  │
│  │ 压缩后重注入  │  │ 只读回放     │  │ /memory 管理       │  │
│  └──────────────┘  └──────────────┘  └────────────────────┘  │
│                                                               │
│  ┌──────────────────┐                                        │
│  │  Session State   │                                        │
│  │  (Hook 间记忆)   │                                        │
│  ├──────────────────┤                                        │
│  │ 文件锁+JSON      │                                        │
│  │ 跨 Hook 调用共享  │                                        │
│  │ 滚动窗口限速     │                                        │
│  └──────────────────┘                                        │
└─────────────────────────────────────────────────────────────┘
```

## 9.2 CLAUDE.md — 静态记忆

### 四级层级加载

```
~/.claude/CLAUDE.md                    (用户级 — 所有项目共享)
    ↓ 覆盖
项目根/CLAUDE.md                        (项目级 — 团队共享)
    ↓ 覆盖
项目根/CLAUDE.local.md                  (项目本地级 — 个人不提交)
    ↓ 覆盖
子目录/CLAUDE.md                        (嵌套目录级 — 按路径加载)
```

**后加载的覆盖先加载的**：项目级约定可以覆盖用户级偏好。

### @include 指令

```markdown
<!-- 项目根/CLAUDE.md -->
# 项目规范

@include docs/coding-standards.md
@include docs/api-conventions.md
```

### .claude/rules/*.md 条件加载

```yaml
# .claude/rules/typescript-rules.md
---
paths:
  - "src/**/*.ts"
  - "test/**/*.ts"
---

# TypeScript 规范
- 使用 strict 模式
- 优先使用 interface 而非 type
```

只有当操作涉及匹配路径的文件时，对应规则才会被加载。

### HTML 注释隐藏

```markdown
<!-- 这段注释对 Claude 不可见（auto-inject 时隐藏）-->
<!-- 但用 Read 工具读取时仍然可见 -->
```

## 9.3 Transcript — 动态记忆

Transcript 是会话的**完整 JSONL 记录文件**，每行一条 JSON 记录：

```
{"type": "user", "content": "帮我修复认证 bug", "timestamp": "..."}
{"type": "assistant", "content": "...", "tool_uses": [...]}
{"type": "tool_result", "tool_use_id": "...", "content": "..."}
...
```

**在 Hook 中的使用**：通过 `transcript_path` 参数访问，实现只读回放。

## 9.4 Auto-Memory — 自动记忆

- **触发方式**：AI 自动判断哪些上下文值得保存
- **管理命令**：`/memory` 查看和编辑所有记忆文件
- **存储目录**：`autoMemoryDirectory` 设置可自定义
- **作用域**：`user`（跨项目）、`project`（项目内）、`local`（个人本地）
- **时间戳**：带 last-modified 时间戳，帮助 AI 判断记忆新鲜度

## 9.5 Session State — Hook 间记忆

**源码位置**: `plugins/security-guidance/hooks/session_state.py`

```python
# 跨 Hook 调用的状态持久化
state = {
    "baseline_sha": "abc123",          # Git 基线
    "touched_paths": ["src/auth.py"],  # 编辑过的文件
    "fire_count": 3,                   # Stop Hook 触发次数
    "pending_warnings": [...],         # 待处理警告
    "shown_warnings": [...],           # 已显示警告（去重用）
    "rate_limits": {"key": 5}          # 滚动窗口限速
}
```

## 9.6 记忆相关的 Hook 触发点

| Hook 事件 | 与记忆的关系 |
|-----------|-------------|
| **SessionStart** | 加载 CLAUDE.md 级联、注入插件上下文 |
| **InstructionsLoaded** | CLAUDE.md/规则加载后触发 |
| **PreCompact** | 可阻止压缩，保留关键上下文 |
| **Stop** | 读取 Transcript 检查历史 |
| **PostToolUse** | 写入 session_state |

## 9.7 实战要点

1. **CLAUDE.md 是最重要的记忆**：项目约定、编码规范、偏好设置都应该写在这里
2. **四种记忆互补**：静态（CLAUDE.md）+ 动态（Transcript）+ 自动（Auto-Memory）+ 临时（Session State）
3. **条件加载节省上下文**：`.claude/rules/*.md` 只在匹配时加载
4. **Transcript 是"只读回放"**：Hook 可以回放检查历史，但不能修改
5. **Auto-Memory 是 AI 的学习笔记**：跨会话持久化，用 `/memory` 管理

---

# S10 System Prompt — 系统提示词

## 10.1 系统提示词的组成

```
┌─────────────────────────────────────────────────────────┐
│                   System Prompt                          │
│  ┌─────────┐ ┌──────────┐ ┌────────┐ ┌───────────────┐  │
│  │ Built-in │ │CLAUDE.md │ │ Rules  │ │  --system-   │  │
│  │(lean/std)│ │(nested)  │ │(cond.) │ │  prompt*     │  │
│  └─────────┘ └──────────┘ └────────┘ └───────────────┘  │
│  ┌──────────┐ ┌────────┐ ┌─────────┐ ┌────────────┐   │
│  │ Agent     │ │ Skills │ │ Effort  │ │ Git instr. │   │
│  │ prompts   │ │        │ │ level   │ │            │   │
│  └──────────┘ └────────┘ └─────────┘ └────────────┘   │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Tool Descriptions (MCP cap 2KB, ToolSearch defer)│  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## 10.2 组装层次

| 层次 | 来源 | 可控性 |
|------|------|--------|
| 1. 基础层 | Claude Code 内置（lean/standard 模式） | `CLAUDE_CODE_SIMPLE` 简化 |
| 2. 配置层 | CLAUDE.md（用户级→项目级→本地级→嵌套级） | 直接编辑 |
| 3. 规则层 | `.claude/rules/*.md`（`paths:` 条件加载） | 直接编辑 |
| 4. Agent 层 | Agent 定义的 system prompt | frontmatter + body |
| 5. 自定义层 | `--system-prompt` / `--append-system-prompt` | CLI 参数 |
| 6. 工具层 | 工具描述（可延迟加载） | ToolSearch, MCP 配置 |

## 10.3 Lean vs Standard 模式

- **Lean 模式**：默认（除 Haiku/Sonnet/Opus 4.7 及更早版本），减少了 1.4k tokens
- **Standard 模式**：更详细的指令，用于需要更强引导的模型

## 10.4 Agent 系统提示词的四种模式

**源码位置**: `plugins/plugin-dev/skills/agent-development/references/system-prompt-design.md`

| 模式 | 用途 | 结构 |
|------|------|------|
| **分析型** | 代码/PR/文档分析 | 收集上下文 → 初始扫描 → 深度分析 → 综合发现 → 优先级排序 → 生成报告 |
| **生成型** | 创建代码/测试/文档 | 理解需求 → 收集上下文 → 设计结构 → 生成内容 → 验证 → 文档化 |
| **验证型** | 检查/验证 | 加载标准 → 扫描目标 → 检查规则 → 收集违规 → 评估严重性 → 判定结果 |
| **编排型** | 协调多步骤/工具 | 计划 → 准备 → 执行阶段 → 监控 → 验证 → 报告 |

**长度指南**：
- 最小可行 Agent: ~500 字
- 标准 Agent: ~1,000-2,000 字
- 综合型 Agent: ~2,000-5,000 字
- 避免超过 10,000 字

## 10.5 提示词缓存机制

| 机制 | 说明 |
|------|------|
| 1h vs 5m TTL | 默认 5 分钟，`ENABLE_PROMPT_CACHING_1H` 可启用 1 小时 |
| 日期移出系统提示词 | 提高缓存命中率 |
| 工具 schema 稳定性 | 修复工具 schema 变更导致缓存失效 |
| `--exclude-dynamic-system-prompt-sections` | 移除动态内容，提高跨用户缓存命中 |
| MCP 描述 2KB 上限 | 防止 OpenAPI 生成的服务器膨胀上下文 |
| ToolSearch 兼容 | 全局缓存在 ToolSearch 启用时也可工作 |

## 10.6 实战要点

1. **系统提示词是 AI 行为的"宪法"**：所有行为都受其约束
2. **Lean 模式节省 token**：对于强模型，精简指令即可
3. **缓存优化很重要**：日期移出、schema 稳定、动态内容排除都是提高缓存命中率的手段
4. **Agent 提示词有明确的模式**：四种模式对应不同场景，避免"万能提示词"
5. **MCP 工具描述有上限**：2KB cap 防止单个工具描述膨胀上下文

---

# S11 Error Recovery — 错误恢复

## 11.1 错误分类与处理策略

```
┌──────────────────────────────────────────────────────────┐
│                   Error Recovery 系统架构                   │
│                                                            │
│  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │
│  │ Rate limit  │  │ Non-stream │  │ Circuit breaker    │  │
│  │ (429/exp.   │  │ fallback   │  │ (3x compact fails) │  │
│  │  backoff)   │  │ (5m abort) │  │                    │  │
│  └────────────┘  └────────────┘  └────────────────────┘  │
│  ┌────────────┐  ┌────────────────────────────────────┐  │
│  │StopFailure │  │ MCP reconnect (3x transient retry, │  │
│  │ Hook       │  │ SSE auto-reconnect)                │  │
│  └────────────┘  └────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

## 11.2 429 速率限制处理

```
429 错误
    ↓
指数退避（最少 13 秒）
    ↓
显示具体限制和重置时间
    ↓
5xx/529 错误显示 status.claude.com 链接
```

**关键修复**（CHANGELOG）：
> "Fixed 429 retries burning all attempts in ~13s when the server returns a small `Retry-After` — exponential backoff now applies as a minimum"

## 11.3 流式超时与非流式回退

```
流式请求 → 5 分钟无数据 → 中止 → 重试非流式请求
    ↓
非流式参数：
  token cap: 64k
  timeout: 300s
  环境变量 CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK 可禁用
```

## 11.4 StopFailure Hook

API 错误导致 turn 中止时触发：

```json
// 触发条件：429 速率限制、认证失败、其他 API 错误
{
  "hook_event_name": "StopFailure",
  "error_type": "rate_limit",
  "session_id": "abc123"
}
```

**防无限循环**：修复了 API 错误触发 Stop Hook 后又重新喂入错误导致循环的问题。

## 11.5 断路器

```python
# 自动压缩断路器
MAX_COMPACT_RETRIES = 3

consecutive_failures = 0
while consecutive_failures < MAX_COMPACT_RETRIES:
    try:
        compact_context()
        break
    except Exception:
        consecutive_failures += 1

if consecutive_failures >= MAX_COMPACT_RETRIES:
    show_actionable_error("请手动 /compact 或简化任务")
```

## 11.6 MCP 服务器重连

| 机制 | 说明 |
|------|------|
| 瞬态错误重试 | 最多 3 次自动重试 |
| SSE 自动重连 | 连接断开后自动重连 |
| `tools/list` 失败 | 重试一次 |
| 非 `-p` 模式 4xx | 不重试非瞬态连接失败 |

## 11.7 工具错误传递

```
Bash 工具执行失败
    ↓
错误信息作为 tool_result 返回给 AI
    ↓
AI 根据错误信息决定下一步行动
    ↓
特殊处理：
  - gh 命令 API 限制 → 提示 AI 回退
  - Exit 2 Hook 阻止 → stderr 返回给 AI
  - PermissionDenied Hook → {retry: true} 允许重试
```

## 11.8 实战要点

1. **指数退避是速率限制的标准处理**：不能立即重试，必须等待
2. **非流式回退是流式请求的安全网**：5 分钟无数据时自动切换
3. **断路器防止无限重试**：3 次失败后停止，给用户可操作的错误信息
4. **StopFailure Hook 是错误恢复的扩展点**：可以在 API 错误时自定义恢复逻辑
5. **错误消息要区分类型**：429 vs 5xx vs 认证失败，不同错误需要不同处理

---

# S12 Task System — 任务协调系统

## 12.1 任务作为协调原语

在多 Agent 场景中，任务系统是**核心协调机制**：

```
┌──────────────────────────────────────────┐
│              Task 协调架构                │
│                                          │
│  Leader Agent                            │
│  ├── TaskCreate (创建任务并分配)          │
│  ├── TaskList  (查看全局进度)              │
│  └── TaskUpdate (标记完成/重新分配)       │
│       │                                  │
│       ▼                                  │
│  Teammate 1 ← owner: "teammate-1"       │
│  ├── TaskGet (获取自己的任务)             │
│  └── TaskUpdate (标记 in_progress/done)  │
│                                          │
│  Teammate 2 ← owner: "teammate-2"       │
│  └── ...                                 │
└──────────────────────────────────────────┘
```

## 12.2 TeammateIdle 和 TaskCompleted 事件

```json
// Teammate 空闲时
{
  "hook_event_name": "TeammateIdle",
  "teammate_name": "frontend-dev"
}
// 返回 {"continue": false, "stopReason": "..."} 可停止 teammate

// 任务完成时
{
  "hook_event_name": "TaskCompleted",
  "task_id": "1",
  "status": "completed"
}
```

## 12.3 任务面板/UI

- 任务面板显示在 prompt 下方
- `/tasks` 对话框查看所有任务
- 后台任务完成时截断为 3 行加溢出摘要
- 任务列表根据终端高度动态调整可见项数

## 12.4 任务元数据可扩展性

```json
// TaskUpdate metadata 扩展示例
{
  "taskId": "5",
  "metadata": {
    "pr_number": 1234,
    "coordinator_session": "team-leader",
    "priority": "high",
    "started_at": "2025-01-15T14:30:00Z"
  }
}
```

设置值为 null 可删除键：`{"metadata": {"priority": null}}`

## 12.5 Dynamic Workflows

```
用户："创建一个工作流来审查所有 PR"
    ↓
Claude 编排数十到数百个后台 Agent
    ↓
每个 Agent 独立审查一个 PR
    ↓
汇总结果
```

## 12.6 实战要点

1. **任务系统是 Agent 间的"共享看板"**：所有 Agent 都能查看和更新
2. **owner 字段是任务分配的关键**：明确谁负责什么
3. **metadata 是万能扩展点**：任何协调信息都可以存入
4. **Dynamic Workflows 是大规模编排**：从单 Agent 到数百个 Agent 的编排能力
5. **Hook 事件驱动协调**：TeammateIdle/TaskCompleted 自动推进工作流

---

# S13 Background Tasks — 后台任务

## 13.1 后台任务创建方式

| 方式 | 说明 |
|------|------|
| **Ctrl+B** | 统一后台化（Bash 命令和 Agent） |
| **`--bg`** CLI 标志 | 启动后台会话 |
| **`run_in_background: true`** | Bash 工具参数 |
| **`background: true`** | Agent frontmatter 字段 |
| **`! command`** | 在 `claude agents` 中执行 |

## 13.2 后台任务生命周期

```
1. 创建
   Ctrl+B / --bg / background:true / run_in_background
   ↓
2. 执行
   在后台运行，主线程继续工作
   ↓
3. 监控
   任务面板 / claude agents 查看
   ↓
4. 完成
   发出通知（含耗时，如 "3h 2m 5s"）
   结果返回主会话
   ↓
5. 清理
   Worktree 清理，状态释放
```

## 13.3 `claude agents` 命令

```
claude agents                # 查看所有后台 Agent
claude agents --json         # JSON 输出用于脚本
claude agents --cwd <path>   # 按目录过滤
claude agents --add-dir ...  # 附加目录
! <command>                  # 运行 shell 命令作为后台会话
```

**快捷键**：
- `←` 键附加到后台会话
- `Ctrl+T` 固定会话（内存压力下最后释放）
- `Ctrl+X Ctrl+K` 杀死后台 Agent

## 13.4 后台任务与主会话交互

```
主会话
    │
    ├── 后台 Agent 1 运行中... ──→ 完成通知
    ├── 后台 Agent 2 运行中... ──→ 完成通知
    │
    └── 响应后计时器："Waiting for N background agents/workflows to finish"
```

- `/clear` 仅清除前台任务，不影响后台任务
- 双击 Ctrl+C 杀死所有后台 Agent
- 后台任务输出截断到 30K 字符（超出部分保存到文件）

## 13.5 Pinned Sessions

```
Ctrl+T 固定后台会话
    ↓
会话保持活跃（空闲时也不关闭）
    ↓
Claude Code 更新时原地重启
    ↓
内存压力下最后释放（仅非固定会话先释放）
```

## 13.6 禁用后台任务

```bash
export CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1
```

禁用所有后台任务功能，包括自动后台化和 Ctrl+B 快捷键。

## 13.7 Worktree 隔离

后台 Agent 默认在独立的 git worktree 中运行：

```json
// 可通过配置禁用
{
  "worktree": {
    "bgIsolation": "none"  // 允许后台会话直接编辑工作副本
  }
}
```

## 13.8 实战要点

1. **Ctrl+B 是最常用的后台化方式**：随时将耗时操作放到后台
2. **输出截断防止上下文溢出**：30K 字符限制，大输出保存到文件
3. **Pinned Sessions 适合长期运行**：CI 监控、日志跟踪等
4. **Worktree 隔离是默认行为**：防止后台任务修改主工作目录
5. **完成通知含耗时信息**：方便评估任务执行效率

---

# S14 Cron Scheduler — 定时调度器

## 14.1 核心工具与命令

| 工具/命令 | 功能 |
|----------|------|
| **CronCreate** | 创建定时任务 |
| **CronDelete** | 删除定时任务 |
| **CronList** | 列出当前定时任务 |
| **`/loop`** | 创建循环定时任务（用户命令） |
| **`/proactive`** | `/loop` 的别名 |

## 14.2 `/loop` 命令语法

```bash
# 基本用法
/loop 5m check the deploy
/loop 10m run the test suite
/loop 30s check if the server is healthy

# /proactive 是别名
/proactive 5m check the deploy
```

## 14.3 Cron Job 生命周期

```
/loop <interval> <prompt>
    ↓
CronCreate 创建定时任务
    ↓
[等待间隔]
    ↓
触发 → 注入消息到对话 → 时间戳标记
    ↓
重复（循环任务）或完成清理（一次性任务）
```

## 14.4 一次性 vs 循环任务

```json
// CronCreate - 一次性任务
{
  "scheduleType": "once",
  "scheduledAt": "2025-01-15T14:30:00",
  "prompt": "提醒我参加站会"
}

// CronCreate - 循环任务
{
  "scheduleType": "recurring",
  "interval": "5m",
  "prompt": "检查部署状态"
}
```

## 14.5 会话作用域

Cron 任务是**会话作用域的**：
- 在会话中创建
- 随会话结束而终止
- 不会跨会话独立持久化

**恢复机制**：
```bash
# --resume 可恢复未过期的定时任务
claude --resume
claude --continue
```

## 14.6 控制与禁用

```bash
# 立即停止所有 cron
export CLAUDE_CODE_DISABLE_CRON=1

# Esc 取消待处理唤醒
# (在 /loop 等待界面按 Esc)
```

## 14.7 时间戳标记

定时任务触发时，Transcript 中会添加时间戳标记：
```
[2025-01-15T14:35:00Z] Cron fired: /loop 5m check the deploy
```

## 14.8 错误处理

| 问题 | 处理 |
|------|------|
| 一次性任务重复触发 | 修复文件监视器丢失导致清理失败 |
| 后台会话目标丢失 | 修复 `/command` 触发时分类器丢失用户目标 |
| Stop Hook 中的 cron 信息 | v2.1.145 新增 `session_crons` 字段 |

## 14.9 实战要点

1. **Cron 是会话作用域的**：不是系统级守护进程，适合会话内定时提醒
2. **`/loop` 是最简洁的接口**：`/loop 5m check the deploy` 一行搞定
3. **`--resume` 可恢复定时任务**：会话意外断开后可恢复
4. **CronCreate 是编程接口**：适合在 Hook 或 Agent 中程序化创建定时任务
5. **`CLAUDE_CODE_DISABLE_CRON` 是紧急制动**：需要时立即停止所有定时任务

---

# S15 Agent Teams — 多智能体团队

## 15.1 Leader-Follower 架构

```
用户 (Leader)
  │
  ├── Teammate 1 (独立进程 / tmux) ── 独立任务
  ├── Teammate 2 (独立进程 / tmux) ── 独立任务
  └── Teammate 3 (独立进程 / tmux) ── 独立任务
```

**启用条件**：
```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

此功能为 **token 密集型**，需要 Max/Team/Enterprise 订阅。

## 15.2 三种协调模式

| 模式 | 描述 | 适用场景 |
|------|------|---------|
| **Team Leader** | 一个 Agent 协调其他 Agent | 复杂项目，需要中央调度 |
| **Collaborative** | Agent 之间对等协调 | 中等复杂度，互相依赖 |
| **Autonomous** | 独立工作，最小协调 | 简单并行任务 |

## 15.3 多 Agent 状态共享

**源码位置**: `plugins/plugin-dev/skills/plugin-settings/references/real-world-examples.md`

**状态文件** (`.claude/multi-agent-swarm.local.md`)：

```yaml
---
agent_name: auth-implementation
task_number: 3.5
pr_number: 1234
coordinator_session: team-leader
enabled: true
dependencies: ["Task 3.4"]
additional_instructions: "Use JWT tokens, not sessions"
---

# Task: Implement Authentication

Build JWT-based authentication for the REST API.

## Coordination
Depends on Task 3.4 (user model).
Report status to 'team-leader' session.
```

## 15.4 tmux 协调通知

```bash
#!/bin/bash
SWARM_STATE_FILE=".claude/multi-agent-swarm.local.md"

# 解析 frontmatter
FRONTMATTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$SWARM_STATE_FILE")
COORDINATOR_SESSION=$(echo "$FRONTMATTER" | grep '^coordinator_session:' | sed 's/coordinator_session: *//')
AGENT_NAME=$(echo "$FRONTMATTER" | grep '^agent_name:' | sed 's/agent_name: *//')

# 通过 tmux 向协调者发送通知
NOTIFICATION="🤖 Agent ${AGENT_NAME} is idle."
if tmux has-session -t "$COORDINATOR_SESSION" 2>/dev/null; then
  tmux send-keys -t "$COORDINATOR_SESSION" "$NOTIFICATION" Enter
fi
```

## 15.5 Teammate 相关 Hook 事件

| Hook 事件 | 触发时机 | 输出格式 |
|-----------|---------|---------|
| `TeammateIdle` | Teammate 空闲时 | `{"continue": false, "stopReason": "..."}` 可停止 |
| `TaskCompleted` | 任务完成时 | 同 Stop Hook 行为 |

## 15.6 关键约束

- Teammate 不能嵌套生成新的 Teammate（安全限制）
- Teammate 自动继承 Leader 的权限模式和模型
- 非 ASCII 名字的 Teammate 会导致 API 调用失败（已修复）
- 内存泄漏：长期运行的 Teammate 会保留全部消息（已修复 GC）

## 15.7 实战要点

1. **Leader-Follower 是最常用的模式**：Leader 做规划和分配，Follower 做执行
2. **状态文件是轻量级协调方案**：YAML frontmatter + Markdown body，简单高效
3. **tmux 是跨进程通信的桥梁**：Agent 间通过 tmux 会话传递消息
4. **权限继承是安全基线**：Follower 不能获得比 Leader 更高的权限
5. **Dynamic Workflows 是大规模版本**：从 2-3 个 Agent 扩展到数百个

---

# S16 Team Protocols — 团队通信协议

## 16.1 核心通信机制

```
Claude Code 主进程
    │
    ├── stdin/stdout JSON ── Hook 通信
    ├── SendMessage ── Agent 间消息传递
    ├── tmux 会话 ── 跨终端通信
    ├── 文件系统 ── 状态共享
    └── MCP Channels ── 服务端推送
```

## 16.2 Hook 通信协议

### 输入格式（Claude Code → Hook）

```json
{
  "session_id": "abc123",
  "transcript_path": "/path/to/transcript.txt",
  "cwd": "/current/working/dir",
  "permission_mode": "ask|allow",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {"command": "..."},
  "user_prompt": "...",
  "reason": "...",
  "effort.level": "high"
}
```

### 输出格式（Hook → Claude Code）

```json
// 放行
{}

// 警告
{"systemMessage": "警告消息"}

// 阻止 + 修改
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "updatedInput": {"field": "modified_value"}
  },
  "systemMessage": "阻止原因"
}

// 注入上下文
{
  "hookSpecificOutput": {
    "additionalContext": "安全警告信息"
  }
}
```

## 16.3 SendMessage — Agent 间消息传递

```
SendMessage({
  to: "agent-id-123",       // 目标 Agent 的 ID
  message: "继续执行任务"    // 可选消息
})
```

- 用于继续先前生成的 Agent（取代旧的 `resume` 参数）
- 自动恢复停止的 Agent 到后台运行
- 恢复 Agent 时保留原始 `cwd`

## 16.4 MCP Channels — 服务端推送

```bash
claude --channels
```

- MCP 服务器声明 `permission` 能力后可转发工具审批提示到手机
- 支持 `allowedChannelPlugins` 管理设置控制插件白名单
- `--channels` 下禁用 `AskUserQuestion` 和 plan-mode 工具

## 16.5 文件系统状态共享

```bash
# 原子更新状态文件
TEMP_FILE="${FILE}.tmp.$$"
sed "s/^field: .*/field: $NEW_VALUE/" "$FILE" > "$TEMP_FILE"
mv "$TEMP_FILE" "$FILE"
```

## 16.6 完整 Hook 事件输入输出规范

| 事件 | 特有输入字段 | 输出能力 |
|------|-------------|---------|
| PreToolUse | `tool_name`, `tool_input` | deny/allow/modify input |
| PostToolUse | `tool_name`, `tool_input`, `tool_output` | inject context, modify output |
| Stop | `reason`, `background_tasks`, `session_crons` | block with reason |
| SubagentStop | 同 Stop | block with reason |
| UserPromptSubmit | `user_prompt` | inject context |
| SessionStart | 无 | inject context, set env |
| PreCompact | 无 | block, inject context |
| TeammateIdle | `teammate_name` | stop teammate |
| TaskCompleted | `task_id`, `status` | continue/stop |

## 16.7 实战要点

1. **stdin/stdout JSON 是核心协议**：所有 Hook 都通过这个协议通信
2. **三种退出码语义**：0=放行、1=警告（用户可见）、2=阻断（AI 可见）
3. **SendMessage 是 Agent 间协作的桥梁**：继续、通知、委托
4. **MCP Channels 是移动端审批的通道**：手机上审批工具调用
5. **文件系统是最简单的状态共享**：原子写入 + YAML frontmatter

---

# S17 Autonomous Agents — 自治智能体

## 17.1 权限模式层级

| 模式 | 安全级别 | 行为 |
|------|---------|------|
| **Plan Mode** | 最高 | 只规划不执行 |
| **Ask Mode** | 高 | 每次操作需确认 |
| **Accept Edits** | 中 | 文件编辑自动批准 |
| **bypassPermissions** | 最低 | 跳过所有权限检查 |
| **bypassPermissions + sandbox** | 低 | 跳过权限 + 沙箱隔离 |

## 17.2 Plan Mode 详解

```
/plan "修复认证 bug"
    ↓
AI 只读取文件，不执行写入
    ↓
生成计划后需用户审批
    ↓
Shift+Tab 快速选择 "auto-accept edits"
    ↓
执行阶段
```

**Opus Plan Mode**：规划阶段用 Opus（最强模型），执行阶段用 Sonnet（性价比模型）

## 17.3 Ralph Wiggum — 自治迭代循环

**源码位置**: `plugins/ralph-wiggum/README.md`

```
/ralph-loop "Build a REST API for todos" --completion-promise "COMPLETE" --max-iterations 50
    ↓
1. Claude 工作于任务
2. Claude 尝试停止
3. Stop Hook 阻止退出
4. Stop Hook 将相同提示词再次注入
5. Claude 看到已修改的文件，继续改进
6. 重复直到输出 <promise>COMPLETE</promise> 或达到最大迭代
```

**状态文件** (`.claude/ralph-loop.local.md`)：

```yaml
---
iteration: 1
max_iterations: 10
completion_promise: "All tests passing and build successful"
started_at: "2025-01-15T14:30:00Z"
---

Fix all the linting errors in the project.
Make sure tests pass after each fix.
```

**安全机制**：
- `--max-iterations` 防止无限循环
- `--completion-promise` 精确字符串匹配退出条件
- `/cancel-ralph` 手动取消

## 17.4 Dynamic Workflows

> "Introducing dynamic workflows: ask Claude to create a workflow and it orchestrates work across tens to hundreds of agents in the background"

- `/workflows` 查看运行状态
- 可编排数十到数百个后台 Agent
- Agent 自动分配任务、协调执行

## 17.5 Agent 自治约束

| 约束 | 实现方式 |
|------|---------|
| 工具权限 | `tools: ["Read", "Grep"]` 限制可用工具 |
| 权限模式继承 | Teammate 自动继承 Leader 权限模式 |
| 模型约束 | Agent frontmatter `model` 字段 |
| 工作树隔离 | `isolation: worktree` |
| 禁止嵌套 | Teammate 不能生成嵌套 Teammate |
| 最大轮次 | `maxTurns` 限制迭代次数 |
| 努力级别 | `effort` frontmatter 字段 |

## 17.6 实战要点

1. **Plan Mode 是人机协作的关键**：规划用强模型，执行用性价比模型
2. **Ralph Loop 是"不让 AI 停下来"的实现**：Stop Hook + 状态文件的经典组合
3. **Dynamic Workflows 是大规模自治**：从单 Agent 循环到多 Agent 编排
4. **约束与自治并存**：maxTurns、tools、isolation 都在"放"和"收"之间平衡
5. **completion_promise 是精确退出条件**：比"感觉完成了"更可靠

---

# S18 Worktree Isolation — 工作树隔离

## 18.1 核心架构

```
主仓库 (main checkout)
  │
  ├── .claude/worktrees/agent-1/   ── 独立分支、独立工作目录
  ├── .claude/worktrees/agent-2/   ── 独立分支、独立工作目录
  └── .claude/worktrees/agent-3/   ── 独立分支、独立工作目录
```

Git Worktree 为 Agent 提供了**文件系统级别的隔离**。

## 18.2 两种使用方式

### CLI 标志

```bash
claude --worktree my-feature    # 或 -w
claude --worktree --tmux        # 在 tmux 分屏中
```

### Agent 声明式隔离

```yaml
---
name: isolated-agent
isolation: worktree    # 自动在独立工作树中运行
---
```

## 18.3 Worktree 工具

| 工具 | 功能 |
|------|------|
| `EnterWorktree` | 创建新工作树或切换已有工作树 |
| `ExitWorktree` | 离开工作树会话 |

## 18.4 配置选项

```json
{
  "worktree": {
    "baseRef": "fresh",                      // "fresh" = origin/<default> | "head" = 本地 HEAD
    "bgIsolation": "none",                   // "none" = 允许后台会话直接编辑工作副本
    "sparsePaths": ["src/core", "tests"]     // 大型 monorepo 稀疏检出
  }
}
```

**baseRef 说明**：
- `fresh`（默认）：新工作树从 `origin/<default>` 创建
- `head`：从本地 HEAD 创建，保留未推送的 commit

## 18.5 Worktree Hook 事件

| 事件 | 触发时机 | 用途 |
|------|---------|------|
| `WorktreeCreate` | 工作树创建时 | 自定义工作树初始化 |
| `WorktreeRemove` | 工作树移除时 | 自定义 VCS 清理 |

## 18.6 沙箱与 Worktree 的关系

```
┌─────────────────────────────────────────────────┐
│              隔离层级                              │
│                                                   │
│  Level 1: Permission (权限控制)                   │
│    ↓ 更强隔离                                     │
│  Level 2: Sandbox (沙箱隔离)                      │
│    ↓ 更强隔离                                     │
│  Level 3: Worktree (文件系统隔离)                  │
│    ↓ 最强隔离                                     │
│  Level 4: DevContainer (容器隔离)                 │
└─────────────────────────────────────────────────┘
```

沙箱配置：
```json
{
  "sandbox": {
    "enabled": true,
    "autoAllowBashIfSandboxed": false,
    "allowUnsandboxedCommands": false,
    "network": {
      "allowedDomains": [],
      "allowLocalBinding": false
    }
  }
}
```

## 18.7 已知问题与修复

- 后台 Agent 的 Worktree 隔离守卫曾被绕过（已修复）
- 沙箱写入白名单曾覆盖整个主仓库根目录（已修复，仅共享 `.git`）
- Worktree 清理不再回退到 `rm -rf`（防止丢失 gitignored 文件）
- PR 未链接到 Worktree 中的会话（已修复）
- 自定义 Agent 和 Skill 在 Worktree 中未被发现（已修复）

## 18.8 实战要点

1. **Worktree 是文件系统级隔离**：比沙箱更强，比容器更轻量
2. **声明式隔离最方便**：`isolation: worktree` 一行搞定
3. **baseRef 选择很重要**：fresh 保证干净，head 保留本地变更
4. **sparsePaths 适合大型 monorepo**：只检出需要的目录
5. **三层隔离各有用途**：权限→沙箱→Worktree，按需选择

---

# S19 MCP Plugin — Model Context Protocol 插件

## 19.1 MCP 架构概览

```
Claude Code
  │
  ├── 内置工具 (Bash, Read, Write, Edit, Grep, Glob...)
  │
  ├── MCP 服务器 1 (stdio) ── 本地子进程
  │   ├── tool-a → mcp__server1__tool_a
  │   └── tool-b → mcp__server1__tool_b
  │
  ├── MCP 服务器 2 (SSE) ── 云端服务 + OAuth
  │   └── tool-c → mcp__server2__tool_c
  │
  └── 插件 MCP 服务器 ── mcp__plugin_<name>_<server>__<tool>
      └── tool-d → mcp__plugin_myplug_db__query
```

## 19.2 四种 MCP 服务器类型

| 类型 | 传输 | 认证 | 最佳用例 | 重连 |
|------|------|------|---------|------|
| **stdio** | 进程 | 环境变量 | 本地工具、自定义服务器 | 进程重启 |
| **SSE** | HTTP/SSE | OAuth | 云端服务、官方 MCP | 自动 |
| **HTTP** | REST | Token | REST API | N/A |
| **WebSocket** | WS | Token | 实时通信 | 自动 |

### stdio 示例

```json
{
  "filesystem": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/allowed/path"],
    "env": {"LOG_LEVEL": "debug"}
  }
}
```

### SSE 示例

```json
{
  "asana": {
    "type": "sse",
    "url": "https://mcp.asana.com/sse"
  }
}
```

### HTTP 示例

```json
{
  "api-service": {
    "type": "http",
    "url": "https://api.example.com/mcp",
    "headers": {
      "Authorization": "Bearer ${API_TOKEN}"
    }
  }
}
```

## 19.3 MCP 配置格式

### 方法 1：专用 .mcp.json（推荐）

```json
{
  "database-tools": {
    "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server",
    "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"],
    "env": {
      "DB_URL": "${DB_URL}"
    }
  }
}
```

### 方法 2：内联在 plugin.json

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "mcpServers": {
    "plugin-api": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/api-server",
      "args": ["--port", "8080"]
    }
  }
}
```

## 19.4 MCP 工具命名规范

```
格式: mcp__<server-name>__<tool-name>
插件: mcp__plugin_<plugin-name>_<server-name>__<tool-name>

示例:
  mcp__filesystem__read_file
  mcp__asana__asana_create_task
  mcp__plugin_myplug_database__query
```

**通配符权限规则**：`mcp__server__*` 允许或拒绝某服务器全部工具

## 19.5 ToolSearch / DeferExecuteTool — 延迟工具加载

当 MCP 工具描述超过上下文窗口 10% 时，自动启用延迟加载：

```
1. ToolSearch({tool_names: ["mcp__ardot__capture_screenshot"]})
   → 加载工具 schema
2. DeferExecuteTool({toolName: "mcp__ardot__capture_screenshot", params: {...}})
   → 执行工具
```

**关键配置**：
- `auto:N` — 上下文窗口百分比阈值（N=0-100）
- `alwaysLoad: true` — 跳过延迟加载
- `allowedMcpServers` / `deniedMcpServers` — 企业级白名单/黑名单

## 19.6 MCP 认证体系

| 认证方式 | 适用类型 | 自动化程度 |
|----------|---------|-----------|
| **OAuth 2.0** | SSE/HTTP | 全自动（浏览器授权+自动刷新） |
| **Bearer Token** | HTTP/WS | 半自动（环境变量） |
| **API Key** | HTTP/WS | 半自动（环境变量） |
| **环境变量** | stdio | 手动（用户设置） |
| **headersHelper 脚本** | SSE/HTTP | 动态（每次调用生成） |

**动态 Headers 示例**：

```json
{
  "api": {
    "type": "sse",
    "url": "https://api.example.com",
    "headersHelper": "${CLAUDE_PLUGIN_ROOT}/scripts/get-headers.sh"
  }
}
```

```bash
#!/bin/bash
# get-headers.sh - 每次调用生成新的 headers
TOKEN=$(get-fresh-token)
cat <<EOF
{
  "Authorization": "Bearer $TOKEN",
  "X-Timestamp": "$(date -Iseconds)"
}
EOF
```

## 19.7 MCP 生命周期管理

```
1. 插件加载
   ↓
2. MCP 配置解析 (.mcp.json / plugin.json / 项目级 .mcp.json)
   ↓
3. 服务器进程启动 (stdio) 或连接建立 (SSE/HTTP/WS)
   ↓
4. 工具发现 (tools/list)
   ↓
5. 工具注册 (mcp__plugin_...__...)
   ↓
6. 工具可用（通过 /mcp 命令查看）
```

**关键环境变量传递给 MCP 服务器**：
- `CLAUDE_CODE_SESSION_ID` — 会话标识
- `CLAUDE_PROJECT_DIR` — 项目目录
- `CLAUDECODE=1` — 标识运行在 Claude Code 环境

## 19.8 企业级管控

| 管理设置 | 功能 |
|----------|------|
| `allowedMcpServers` | MCP 服务器白名单 |
| `deniedMcpServers` | MCP 服务器黑名单 |
| `allowAllClaudeAiMcps` | 允许加载 claude.ai 云端连接器 |
| `--strict-mcp-config` | 严格模式，禁止未显式配置的 MCP 服务器 |
| `managed-mcp.json` | 企业管理的 MCP 配置 |

## 19.9 实战要点

1. **MCP 是 Claude Code 连接外部世界的标准协议**：数据库、API、云服务都通过 MCP 接入
2. **四种传输类型覆盖所有场景**：stdio（本地）、SSE（云端）、HTTP（REST）、WS（实时）
3. **延迟加载节省上下文**：MCP 工具超过 10% 上下文窗口时自动延迟加载
4. **headersHelper 实现动态认证**：每次调用生成新 token，适合 OAuth 等短期 token 场景
5. **`--strict-mcp-config` 是企业安全的关键**：禁止未显式配置的 MCP 服务器

---

# 附录：模块间关系全景图

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Claude Code 核心架构                           │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    Agent Loop (S01)                          │    │
│  │  Think → Select Tool → Execute → Observe → Iterate           │    │
│  └────────┬──────────┬──────────┬──────────┬──────────┬────────┘    │
│           │          │          │          │          │              │
│  ┌────────▼──┐ ┌────▼────┐ ┌──▼───┐ ┌───▼────┐ ┌───▼──────┐       │
│  │Tool Use  │ │Permis-  │ │Hooks │ │Memory  │ │Context   │       │
│  │(S02)     │ │sion(S03)│ │(S04) │ │(S09)   │ │Compact   │       │
│  └────┬─────┘ └────┬────┘ └──┬───┘ └───┬────┘ │(S08)     │       │
│       │            │         │         │      └──────────┘       │
│  ┌────▼────────────▼─────────▼─────────▼──────────────────┐       │
│  │              System Prompt (S10)                        │       │
│  │  Built-in + CLAUDE.md + Rules + Agent + Skills + Tools  │       │
│  └────────────────────────────────────────────────────────┘       │
│                                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐               │
│  │TODO Write│ │Subagent  │ │Skill     │ │Error     │               │
│  │(S05)     │ │(S06)     │ │Loading   │ │Recovery  │               │
│  │          │ │          │ │(S07)     │ │(S11)     │               │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────────┘               │
│       │            │            │                                    │
│  ┌────▼────────────▼────────────▼──────────────────────────┐       │
│  │          Task System (S12) + Background Tasks (S13)     │       │
│  │  TaskCreate/Update/List/Get + Ctrl+B + --bg             │       │
│  └────────────────────────────┬────────────────────────────┘       │
│                               │                                      │
│  ┌────────────────────────────▼────────────────────────────┐       │
│  │              Cron Scheduler (S14)                       │       │
│  │  /loop + CronCreate/Delete/List                         │       │
│  └────────────────────────────────────────────────────────┘       │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │             多 Agent 协作层                                │     │
│  │  Agent Teams(S15) + Team Protocols(S16)                   │     │
│  │  + Autonomous Agents(S17) + Worktree Isolation(S18)      │     │
│  └────────────────────────────┬─────────────────────────────┘     │
│                               │                                      │
│  ┌────────────────────────────▼─────────────────────────────┐     │
│  │             MCP Plugin (S19)                              │     │
│  │  stdio / SSE / HTTP / WS + ToolSearch + DeferExecuteTool  │     │
│  └──────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────────┘
```

---

# 附录：关键源码文件索引

| 课程主题 | 关键源码文件 |
|---------|------------|
| S01 Agent Loop | `docs/technical_design.md`, `plugins/ralph-wiggum/hooks/stop-hook.sh` |
| S02 Tool Use | `plugins/hookify/core/rule_engine.py`（字段提取逻辑） |
| S03 Permission | `examples/settings/settings-strict.json`, `examples/settings/settings-bash-sandbox.json`, `examples/mdm/` |
| S04 Hooks | `docs/technical_design.md`, `plugins/hookify/`, `plugins/security-guidance/hooks/`, `examples/hooks/` |
| S05 TODO Write | CHANGELOG（v2.1.16 引入任务系统） |
| S06 Subagent | `plugins/plugin-dev/skills/agent-development/SKILL.md`, `plugins/feature-dev/agents/` |
| S07 Skill Loading | `plugins/plugin-dev/skills/skill-development/SKILL.md`, 所有 `SKILL.md` 文件 |
| S08 Context Compact | CHANGELOG（compact 相关条目） |
| S09 Memory | `plugins/security-guidance/hooks/session_state.py`, CHANGELOG（memory 条目） |
| S10 System Prompt | `plugins/plugin-dev/skills/agent-development/references/system-prompt-design.md` |
| S11 Error Recovery | CHANGELOG（error/retry/fallback 条目） |
| S12 Task System | CHANGELOG（TaskCreated/TeammateIdle/TaskCompleted 条目） |
| S13 Background Tasks | CHANGELOG（--bg/claude agents/Ctrl+B 条目） |
| S14 Cron Scheduler | CHANGELOG（CronCreate/CronDelete//loop 条目） |
| S15 Agent Teams | `plugins/plugin-dev/skills/plugin-settings/references/real-world-examples.md` |
| S16 Team Protocols | `docs/technical_design.md`（Hook 协议） |
| S17 Autonomous Agents | `plugins/ralph-wiggum/README.md` |
| S18 Worktree Isolation | CHANGELOG（worktree/isolation 条目） |
| S19 MCP Plugin | `plugins/plugin-dev/skills/mcp-integration/SKILL.md`, `plugins/plugin-dev/skills/mcp-integration/references/` |
