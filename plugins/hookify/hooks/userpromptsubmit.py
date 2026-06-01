#!/usr/bin/env python3
"""UserPromptSubmit hook executor for hookify plugin.

This script is called by Claude Code when user submits a prompt.
It reads .claude/hookify.*.local.md files and evaluates rules.
"""

# 【模块功能概述】
# 本脚本是 hookify 插件的 UserPromptSubmit（用户提交提示词）Hook 入口
# 当用户在 Claude Code 中输入提示词并提交时触发
# 这是整个 Hook 生命周期中最早触发的环节
# 典型场景：
#   - 检查用户输入中是否包含敏感信息（如密码、API Key）
#   - 对用户输入进行预处理或格式化建议
#   - 记录用户输入用于审计或分析
#   - 在用户提问前注入上下文提示（如项目规范提醒）
#
# 【Hook 生命周期顺序】
# UserPromptSubmit → PreToolUse → [工具执行] → PostToolUse → Stop

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
    """Main entry point for UserPromptSubmit hook."""
    # 【业务功能】UserPromptSubmit Hook 的主入口
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)
        # 【数据格式】UserPromptSubmit 事件的输入 JSON 示例：
        # {
        #   "hook_event_name": "UserPromptSubmit",
        #   "user_prompt": "请帮我重构这段代码",
        #   "session_id": "abc123",
        #   "cwd": "/home/user/project"
        # }

        # Load user prompt rules
        rules = load_rules(event='prompt')
        # 【业务含义】加载 event='prompt' 或 event='all' 的规则
        # 'prompt' 事件类型专门针对用户提交提示词的场景

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)

    except Exception as e:
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0
        sys.exit(0)


if __name__ == '__main__':
    main()
