#!/usr/bin/env python3
"""
Claude Code Hook: Bash Command Validator
=========================================
This hook runs as a PreToolUse hook for the Bash tool.
It validates bash commands against a set of rules before execution.
In this case it changes grep calls to using rg.

Read more about hooks here: https://docs.anthropic.com/en/docs/claude-code/hooks

Make sure to change your path to your actual script.

{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/claude-code/examples/hooks/bash_command_validator_example.py"
          }
        ]
      }
    ]
  }
}

"""

# 【业务功能说明】
# 本脚本是一个 Claude Code 的 Hook（钩子）示例，用于在 AI 助手执行 Bash 命令之前进行校验。
# 核心业务场景：当 AI 助手尝试执行 grep 或 find 等低效命令时，本钩子会拦截并建议使用更优的工具（如 rg/ripgrep）。
#
# 【Hook 机制说明】
# Claude Code 的 Hook 机制允许用户在特定事件（如工具调用前/后）插入自定义逻辑。
# Hook 类型包括：
#   - PreToolUse：工具执行前触发，可拦截/阻止工具调用
#   - PostToolUse：工具执行后触发，可对结果进行后处理
#   - Stop：AI 助手准备停止时触发
#   - UserPromptSubmit：用户提交提示词时触发
#
# 【退出码约定】
# exit(0)：允许操作继续执行，不干预
# exit(1)：向用户显示 stderr 内容，但不告知 AI 助手（用于非阻塞性警告）
# exit(2)：阻止工具调用，并将 stderr 内容告知 AI 助手（用于阻断性校验）

import json  # 【语法】导入 Python 标准库 json 模块，用于解析和生成 JSON 数据
import re    # 【语法】导入 Python 标准库 re 模块，提供正则表达式匹配功能
import sys   # 【语法】导入 Python 标准库 sys 模块，提供对解释器相关功能的访问（如 stdin/stderr/exit）

# Define validation rules as a list of (regex pattern, message) tuples
# 【业务功能】定义命令校验规则列表，每条规则由"正则表达式"和"提示消息"组成
# 【语法】以 _ 开头的变量名表示模块级私有变量（Python 约定，非强制）
# 【语法】列表中的每个元素是一个元组 (tuple)，元组包含两个字符串：正则模式和提示信息
_VALIDATION_RULES = [
    (
        r"^grep\b(?!.*\|)",
        # 【正则解析】
        # ^       - 匹配字符串开头（确保 grep 是命令的第一个词，而非管道后面的 grep）
        # grep    - 匹配字面量 "grep"
        # \b      - 单词边界，确保匹配的是完整的 "grep" 而非 "grepfoo" 等
        # (?!.*\|) - 负向前瞻断言（negative lookahead）：从当前位置向后看，不允许出现 "|"
        #           即排除管道中的 grep（如 "cat file | grep xxx"），因为管道中的 grep 可能是合理的
        # 【业务含义】检测直接使用 grep 命令的场景（非管道中），建议替换为更高效的 rg
        "Use 'rg' (ripgrep) instead of 'grep' for better performance and features",
    ),
    (
        r"^find\s+\S+\s+-name\b",
        # 【正则解析】
        # ^       - 匹配字符串开头
        # find    - 匹配字面量 "find"
        # \s+     - 匹配一个或多个空白字符（空格、tab等）
        # \S+     - 匹配一个或多个非空白字符（即搜索路径，如 /home/user）
        # \s+     - 再匹配空白
        # -name   - 匹配字面量 "-name"（find 命令的按名称查找参数）
        # \b      - 单词边界
        # 【业务含义】检测使用 find -name 的场景，建议替换为 rg 的文件搜索功能
        "Use 'rg --files | rg pattern' or 'rg --files -g pattern' instead of 'find -name' for better performance",
    ),
]


def _validate_command(command: str) -> list[str]:
    # 【业务功能】对给定的 Bash 命令字符串进行规则校验，返回所有匹配的提示信息列表
    # 【语法】def 关键字定义函数；command: str 是类型注解，表示参数应为字符串
    # 【语法】-> list[str] 是返回值类型注解，表示返回字符串列表（Python 3.9+ 内置泛型语法）
    # 【语法】以 _ 开头的函数名表示模块级私有函数（Python 约定）
    issues = []  # 【语法】初始化空列表，用于收集所有匹配的校验问题
    for pattern, message in _VALIDATION_RULES:
        # 【语法】for 循环遍历规则列表；每次迭代解包元组为 pattern（正则）和 message（提示）
        if re.search(pattern, command):
            # 【语法】re.search() 在 command 中搜索匹配 pattern 的位置
            # 与 re.match() 不同，search() 不要求从字符串开头匹配（但本例中 pattern 已用 ^ 限定开头）
            # 返回 Match 对象（匹配成功）或 None（未匹配），因此可直接用于 if 判断
            issues.append(message)  # 【语法】将匹配到的提示消息追加到问题列表
    return issues  # 【语法】返回所有匹配的校验问题列表（可能为空）


def main():
    # 【业务功能】脚本主入口函数，负责：读取输入 → 解析数据 → 校验命令 → 输出结果
    try:
        input_data = json.load(sys.stdin)
        # 【语法】json.load() 从文件类对象读取 JSON 并解析为 Python 字典
        # 【语法】sys.stdin 是标准输入流，Claude Code 通过管道（pipe）将 JSON 数据传入
        # 【数据格式】输入 JSON 示例：{"tool_name": "Bash", "tool_input": {"command": "grep foo bar.txt"}}
    except json.JSONDecodeError as e:
        # 【语法】json.JSONDecodeError 是 JSON 解析失败时抛出的异常类
        # 【语法】as e 将异常对象赋值给变量 e，以便获取错误详情
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        # 【语法】f-string 格式化字符串，花括号内为表达式
        # 【语法】file=sys.stderr 指定输出到标准错误流（而非默认的 stdout）
        # Exit code 1 shows stderr to the user but not to Claude
        sys.exit(1)
        # 【语法】sys.exit() 终止脚本并返回退出码
        # 【Hook 约定】退出码 1 = 向用户显示错误信息，但不通知 AI 助手

    tool_name = input_data.get("tool_name", "")
    # 【语法】dict.get(key, default) 安全获取字典值，key 不存在时返回默认值而非抛出 KeyError
    # 【业务含义】获取 Claude Code 正在调用的工具名称（如 "Bash"、"Edit"、"Write" 等）
    if tool_name != "Bash":
        # 【业务含义】本钩子只关心 Bash 工具的调用，其他工具直接放行
        sys.exit(0)  # 【Hook 约定】退出码 0 = 不干预，允许操作继续

    tool_input = input_data.get("tool_input", {})
    # 【业务含义】获取工具的输入参数字典，对于 Bash 工具，其中包含 "command" 字段
    command = tool_input.get("command", "")
    # 【业务含义】提取待执行的 Bash 命令字符串

    if not command:
        # 【业务含义】如果命令为空字符串，无需校验，直接放行
        # 【语法】空字符串在布尔上下文中为 False，not "" 为 True
        sys.exit(0)

    issues = _validate_command(command)
    # 【业务含义】调用校验函数，检查命令是否违反规则
    if issues:
        # 【语法】非空列表在布尔上下文中为 True，即存在校验不通过的问题
        for message in issues:
            print(f"• {message}", file=sys.stderr)
            # 【语法】f-string 中使用 Unicode 字符 "•" 作为列表项标记
            # 【语法】输出到 stderr，因为 Claude Code 的 Hook 机制从 stderr 读取拦截信息
        # Exit code 2 blocks tool call and shows stderr to Claude
        sys.exit(2)
        # 【Hook 约定】退出码 2 = 阻止工具调用，并将 stderr 内容传递给 AI 助手
        # 这样 AI 助手能看到校验提示，并据此调整行为（如改用 rg 替代 grep）


if __name__ == "__main__":
    # 【语法】__name__ 是 Python 的内置变量，当脚本被直接运行时值为 "__main__"
    # 当脚本被其他模块 import 时，__name__ 为模块名而非 "__main__"
    # 这是 Python 的惯用写法，确保脚本既可以独立运行，也可以作为模块导入
    main()
