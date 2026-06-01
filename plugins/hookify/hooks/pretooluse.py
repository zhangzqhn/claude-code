#!/usr/bin/env python3
"""PreToolUse hook executor for hookify plugin.

This script is called by Claude Code before any tool executes.
It reads .claude/hookify.*.local.md files and evaluates rules.
"""

# 【模块功能概述】
# 本脚本是 hookify 插件的 PreToolUse（工具执行前）Hook 入口
# 当 Claude Code 即将调用任何工具（如 Bash、Edit、Write 等）时触发
# 执行流程：
# 1. Claude Code 通过 stdin 传入 JSON 格式的工具调用信息
# 2. 根据工具名称判断事件类型（bash/file）
# 3. 加载对应事件类型的规则
# 4. 用规则引擎评估是否匹配
# 5. 将评估结果以 JSON 格式输出到 stdout
#
# 【重要设计原则】
# 本脚本始终以 exit(0) 退出，永不阻断操作
# 阻断逻辑由 Claude Code 根据 JSON 输出中的 permissionDecision 来决定
# 这样确保即使 Hook 脚本崩溃，也不会影响 Claude Code 的正常运行

import os   # 【语法】操作系统接口模块
import sys  # 【语法】系统模块，提供 stdin/stderr/exit
import json # 【语法】JSON 解析模块

# CRITICAL: Add plugin root to Python path for imports
# 【业务含义】将插件根目录添加到 Python 模块搜索路径，使得可以 import hookify 包
# Claude Code 通过环境变量 CLAUDE_PLUGIN_ROOT 告知插件自身的安装路径
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
# 【语法】os.environ.get(key) 从环境变量中获取值，不存在则返回 None
if PLUGIN_ROOT:
    # Add the parent directory of the plugin
    parent_dir = os.path.dirname(PLUGIN_ROOT)
    # 【语法】os.path.dirname() 获取路径的父目录
    # 例如 PLUGIN_ROOT=/a/b/hookify → parent_dir=/a/b
    # 这样 Python 就能找到 /a/b/hookify/core/ 下的模块
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
        # 【语法】sys.path 是 Python 模块搜索路径列表
        # insert(0, ...) 将路径插入到列表开头，使其优先被搜索

    # Also add PLUGIN_ROOT itself in case we have other scripts
    if PLUGIN_ROOT not in sys.path:
        sys.path.insert(0, PLUGIN_ROOT)

try:
    from hookify.core.config_loader import load_rules
    # 【业务含义】导入规则加载函数，从 .claude/hookify.*.local.md 文件加载规则
    from hookify.core.rule_engine import RuleEngine
    # 【业务含义】导入规则评估引擎，用于将规则与输入数据进行匹配
except ImportError as e:
    # If imports fail, allow operation and log error
    # 【业务含义】导入失败时不阻断操作，而是通过 systemMessage 通知用户
    # 这是一个容错设计：即使插件安装有问题，也不影响 Claude Code 正常使用
    error_msg = {"systemMessage": f"Hookify import error: {e}"}
    print(json.dumps(error_msg), file=sys.stdout)
    # 【语法】json.dumps() 将 Python 字典序列化为 JSON 字符串
    sys.exit(0)


def main():
    """Main entry point for PreToolUse hook."""
    # 【业务功能】PreToolUse Hook 的主入口，处理工具调用前的事件
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)
        # 【语法】json.load() 从文件类对象（如 stdin）读取并解析 JSON
        # 【数据格式】Claude Code 传入的 JSON 示例：
        # {
        #   "tool_name": "Bash",           # 工具名称
        #   "tool_input": {"command": "..."}, # 工具输入参数
        #   "hook_event_name": "PreToolUse"  # Hook 事件名称
        # }

        # Determine event type for filtering
        # For PreToolUse, we use tool_name to determine "bash" vs "file" event
        tool_name = input_data.get('tool_name', '')

        event = None
        if tool_name == 'Bash':
            event = 'bash'
            # 【业务含义】Bash 工具调用对应 bash 事件类型
        elif tool_name in ['Edit', 'Write', 'MultiEdit']:
            event = 'file'
            # 【业务含义】文件编辑类工具对应 file 事件类型

        # Load rules
        rules = load_rules(event=event)
        # 【业务含义】按事件类型加载匹配的规则，减少不必要的规则评估

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)
        # 【业务含义】用规则引擎评估所有加载的规则

        # Always output JSON (even if empty)
        print(json.dumps(result), file=sys.stdout)
        # 【业务含义】将评估结果输出到 stdout
        # 空字典 {} 表示无规则匹配（允许操作）
        # 非空字典包含 systemMessage/hookSpecificOutput 等字段

    except Exception as e:
        # On any error, allow the operation and log
        # 【业务含义】异常时允许操作继续，并记录错误信息
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        # ALWAYS exit 0 - never block operations due to hook errors
        # 【业务含义】始终以 exit(0) 退出，永不因 Hook 错误而阻断操作
        # 阻断与否由输出 JSON 中的 permissionDecision 字段决定
        sys.exit(0)


if __name__ == '__main__':
    main()
