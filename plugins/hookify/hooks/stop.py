#!/usr/bin/env python3
"""Stop hook executor for hookify plugin.

This script is called by Claude Code when agent wants to stop.
It reads .claude/hookify.*.local.md files and evaluates stop rules.
"""

# 【模块功能概述】
# 本脚本是 hookify 插件的 Stop（AI 停止）Hook 入口
# 当 AI 助手认为自己已完成任务、准备停止响应时触发
# 典型场景：
#   - 阻止 AI 过早停止（如还有未完成的检查项）
#   - 在 AI 停止前执行最终审查（如安全检查、代码规范检查）
#   - 生成会话总结或报告
#
# 【与 PreToolUse/PostToolUse 的区别】
# Stop Hook 没有具体的工具调用上下文，而是关注整个会话的状态
# 输入数据中可能包含 reason（停止原因）和 transcript_path（会话记录文件路径）

import os   # 【语法】操作系统接口模块
import sys  # 【语法】系统模块
import json # 【语法】JSON 解析模块

# CRITICAL: Add plugin root to Python path for imports
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    parent_dir = os.path.dirname(PLUGIN_ROOT)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    if PLUGIN_ROOT not in sys.path:
        sys.path.insert(0, PLUGIN_ROOT)

try:
    from hookify.core.config_loader import load_rules
    from hookify.core.rule_engine import RuleEngine
except ImportError as e:
    error_msg = {"systemMessage": f"Hookify import error: {e}"}
    print(json.dumps(error_msg), file=sys.stdout)
    sys.exit(0)


def main():
    """Main entry point for Stop hook."""
    # 【业务功能】Stop Hook 的主入口，处理 AI 准备停止的事件
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)
        # 【数据格式】Stop 事件的输入 JSON 示例：
        # {
        #   "hook_event_name": "Stop",
        #   "reason": "任务已完成",
        #   "transcript_path": "/tmp/xxx/transcript.txt",
        #   "session_id": "abc123"
        # }

        # Load stop rules
        rules = load_rules(event='stop')
        # 【业务含义】只加载 event='stop' 或 event='all' 的规则

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)
        # 【业务含义】如果规则引擎返回 {"decision": "block", ...}，
        # Claude Code 会阻止 AI 停止，让其继续工作

    except Exception as e:
        # On any error, allow the operation
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0
        sys.exit(0)


if __name__ == '__main__':
    main()
