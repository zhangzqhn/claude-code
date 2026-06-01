#!/usr/bin/env python3
"""Rule evaluation engine for hookify plugin."""

# 【模块功能概述】
# 本模块是 hookify 插件的规则评估引擎，负责：
# 1. 将加载的规则列表与 Hook 输入数据进行匹配评估
# 2. 根据规则的 action（warn/block）决定输出格式
# 3. 支持多种匹配操作符（正则匹配、包含、相等、前缀、后缀等）
# 4. 通过 LRU 缓存优化正则编译性能
#
# 【核心流程】
# evaluate_rules() → 遍历所有规则 → _rule_matches() 检查匹配 → _check_condition() 逐条件验证
# → 根据匹配结果构造输出（阻断/警告/通过）

import re
import sys
from functools import lru_cache  # 【语法】lru_cache 装饰器实现最近最少使用缓存，自动缓存函数返回值
from typing import List, Dict, Any, Optional  # 【语法】类型注解工具

# Import from local module
from hookify.core.config_loader import Rule, Condition  # 【语法】从同包模块导入数据类


# Cache compiled regexes (max 128 patterns)
@lru_cache(maxsize=128)
# 【语法】@lru_cache(maxsize=128) 装饰器缓存函数结果
# 当相同 pattern 再次传入时，直接返回缓存的编译结果，避免重复编译
# maxsize=128 表示最多缓存 128 个不同的正则模式
# 【业务含义】同一正则可能被多条规则或多次调用重复使用，缓存可显著提升性能
def compile_regex(pattern: str) -> re.Pattern:
    """Compile regex pattern with caching.

    Args:
        pattern: Regex pattern string

    Returns:
        Compiled regex pattern
    """
    return re.compile(pattern, re.IGNORECASE)
    # 【语法】re.compile() 将正则字符串预编译为 Pattern 对象，提升重复匹配性能
    # re.IGNORECASE 标志使匹配不区分大小写


class RuleEngine:
    """Evaluates rules against hook input data."""
    # 【业务功能】规则评估引擎的核心类，负责将规则与 Hook 输入数据进行匹配

    def __init__(self):
        """Initialize rule engine."""
        # No need for instance cache anymore - using global lru_cache
        pass
        # 【语法】pass 是空操作占位符，表示什么也不做
        # 这里因为缓存已移至模块级的 lru_cache，实例无需额外初始化

    def evaluate_rules(self, rules: List[Rule], input_data: Dict[str, Any]) -> Dict[str, Any]:
        # 【业务功能】评估所有规则并返回综合结果，是规则引擎的主入口方法
        # 【输入】rules = 规则列表；input_data = Claude Code 传入的 Hook JSON 数据
        # 【输出】响应字典，格式取决于匹配结果：
        #   - 无匹配 → 空字典 {}（允许操作）
        #   - 仅警告 → {"systemMessage": "..."}（显示消息但不阻止）
        #   - 阻断规则 → 根据 Hook 事件类型返回不同格式的阻断响应
        """Evaluate all rules and return combined results.

        Checks all rules and accumulates matches. Blocking rules take priority
        over warning rules. All matching rule messages are combined.

        Args:
            rules: List of Rule objects to evaluate
            input_data: Hook input JSON (tool_name, tool_input, etc.)

        Returns:
            Response dict with systemMessage, hookSpecificOutput, etc.
            Empty dict {} if no rules match.
        """
        hook_event = input_data.get('hook_event_name', '')
        # 【业务含义】获取 Hook 事件名称，决定阻断响应的输出格式
        # 可能的值：PreToolUse、PostToolUse、Stop、UserPromptSubmit
        blocking_rules = []   # 收集匹配的阻断规则
        warning_rules = []    # 收集匹配的警告规则

        for rule in rules:
            if self._rule_matches(rule, input_data):
                # 【业务含义】规则匹配成功，根据 action 分类
                if rule.action == 'block':
                    blocking_rules.append(rule)
                else:
                    warning_rules.append(rule)

        # If any blocking rules matched, block the operation
        if blocking_rules:
            # 【业务含义】阻断规则优先级高于警告规则，只要有任何阻断规则匹配就阻止操作
            messages = [f"**[{r.name}]**\n{r.message}" for r in blocking_rules]
            # 【语法】列表推导式，为每条阻断规则格式化消息
            # Markdown 加粗 **name** 使规则名在输出中更醒目
            combined_message = "\n\n".join(messages)
            # 【语法】str.join() 用双换行连接所有消息

            # Use appropriate blocking format based on event type
            if hook_event == 'Stop':
                # 【业务含义】Stop 事件的阻断格式：使用 decision: "block" 阻止 AI 停止
                return {
                    "decision": "block",
                    "reason": combined_message,
                    "systemMessage": combined_message
                }
            elif hook_event in ['PreToolUse', 'PostToolUse']:
                # 【业务含义】工具调用事件的阻断格式：使用 permissionDecision: "deny" 拒绝工具调用
                return {
                    "hookSpecificOutput": {
                        "hookEventName": hook_event,
                        "permissionDecision": "deny"  # deny = 拒绝执行
                    },
                    "systemMessage": combined_message
                }
            else:
                # For other events, just show message
                return {
                    "systemMessage": combined_message
                }

        # If only warnings, show them but allow operation
        if warning_rules:
            # 【业务含义】仅有警告规则匹配，显示消息但不阻止操作
            messages = [f"**[{r.name}]**\n{r.message}" for r in warning_rules]
            return {
                "systemMessage": "\n\n".join(messages)
            }

        # No matches - allow operation
        return {}
        # 【业务含义】无规则匹配，返回空字典，表示不干预操作

    def _rule_matches(self, rule: Rule, input_data: Dict[str, Any]) -> bool:
        # 【业务功能】检查单条规则是否与输入数据匹配
        # 匹配逻辑：先检查工具匹配器 → 再检查所有条件（AND 逻辑）
        """Check if rule matches input data.

        Args:
            rule: Rule to evaluate
            input_data: Hook input data

        Returns:
            True if rule matches, False otherwise
        """
        # Extract tool information
        tool_name = input_data.get('tool_name', '')
        tool_input = input_data.get('tool_input', {})

        # Check tool matcher if specified
        if rule.tool_matcher:
            # 【业务含义】如果规则指定了工具匹配器，先验证工具名称是否匹配
            if not self._matches_tool(rule.tool_matcher, tool_name):
                return False  # 工具不匹配，规则不适用

        # If no conditions, don't match
        # (Rules must have at least one condition to be valid)
        if not rule.conditions:
            # 【业务含义】没有条件的规则视为无效，不匹配任何内容
            return False

        # All conditions must match
        for condition in rule.conditions:
            if not self._check_condition(condition, tool_name, tool_input, input_data):
                return False  # 任一条件不满足即不匹配（AND 逻辑）

        return True  # 所有条件都满足，规则匹配

    def _matches_tool(self, matcher: str, tool_name: str) -> bool:
        # 【业务功能】检查工具名称是否匹配匹配器模式
        """Check if tool_name matches the matcher pattern.

        Args:
            matcher: Pattern like "Bash", "Edit|Write", "*"
            tool_name: Actual tool name

        Returns:
            True if matches
        """
        if matcher == '*':
            return True  # 通配符匹配所有工具

        # Split on | for OR matching
        patterns = matcher.split('|')
        # 【业务含义】支持管道符分隔的多工具匹配，如 "Edit|Write" 匹配两种工具
        # 【语法】str.split('|') 按管道符分割字符串
        return tool_name in patterns
        # 【语法】in 运算符检查元素是否在列表中

    def _check_condition(self, condition: Condition, tool_name: str,
                        tool_input: Dict[str, Any], input_data: Dict[str, Any] = None) -> bool:
        # 【业务功能】检查单个条件是否满足，是规则匹配的原子判断单元
        # 流程：提取字段值 → 根据操作符进行比较
        """Check if a single condition matches.

        Args:
            condition: Condition to check
            tool_name: Tool being used
            tool_input: Tool input dict
            input_data: Full hook input data (for Stop events, etc.)

        Returns:
            True if condition matches
        """
        # Extract the field value to check
        field_value = self._extract_field(condition.field, tool_name, tool_input, input_data)
        if field_value is None:
            # 【业务含义】字段不存在或无法提取，条件不满足
            return False

        # Apply operator
        operator = condition.operator
        pattern = condition.pattern

        if operator == 'regex_match':
            return self._regex_match(pattern, field_value)
            # 正则匹配
        elif operator == 'contains':
            return pattern in field_value
            # 【语法】in 运算符检查子串是否存在于字符串中
        elif operator == 'equals':
            return pattern == field_value
            # 【语法】== 运算符进行精确相等比较
        elif operator == 'not_contains':
            return pattern not in field_value
            # 【语法】not in 检查子串是否不存在
        elif operator == 'starts_with':
            return field_value.startswith(pattern)
            # 【语法】str.startswith() 检查字符串是否以指定前缀开头
        elif operator == 'ends_with':
            return field_value.endswith(pattern)
            # 【语法】str.endswith() 检查字符串是否以指定后缀结尾
        else:
            # Unknown operator
            return False

    def _extract_field(self, field: str, tool_name: str,
                      tool_input: Dict[str, Any], input_data: Dict[str, Any] = None) -> Optional[str]:
        # 【业务功能】从 Hook 输入数据中提取指定字段的值
        # 不同工具类型和事件类型有不同的字段结构，此方法统一处理
        """Extract field value from tool input or hook input data.

        Args:
            field: Field name like "command", "new_text", "file_path", "reason", "transcript"
            tool_name: Tool being used (may be empty for Stop events)
            tool_input: Tool input dict
            input_data: Full hook input (for accessing transcript_path, reason, etc.)

        Returns:
            Field value as string, or None if not found
        """
        # Direct tool_input fields
        if field in tool_input:
            # 【业务含义】先在工具输入字典中直接查找
            value = tool_input[field]
            if isinstance(value, str):
                return value
            return str(value)
            # 【语法】str() 将任意类型转为字符串

        # For Stop events and other non-tool events, check input_data
        if input_data:
            # Stop event specific fields
            if field == 'reason':
                # 【业务含义】Stop 事件的 reason 字段，包含 AI 停止的原因
                return input_data.get('reason', '')
            elif field == 'transcript':
                # 【业务含义】读取会话记录文件内容
                # transcript_path 指向一个文件路径，需要读取其内容
                transcript_path = input_data.get('transcript_path')
                if transcript_path:
                    try:
                        with open(transcript_path, 'r') as f:
                            return f.read()
                    except FileNotFoundError:
                        # 【语法】文件不存在时的异常处理
                        print(f"Warning: Transcript file not found: {transcript_path}", file=sys.stderr)
                        return ''
                    except PermissionError:
                        # 【语法】权限不足时的异常处理
                        print(f"Warning: Permission denied reading transcript: {transcript_path}", file=sys.stderr)
                        return ''
                    except (IOError, OSError) as e:
                        # 【语法】IOError 和 OSError 涵盖文件读写的各种底层错误
                        print(f"Warning: Error reading transcript {transcript_path}: {e}", file=sys.stderr)
                        return ''
                    except UnicodeDecodeError as e:
                        # 【语法】文件编码无法解码时的异常
                        print(f"Warning: Encoding error in transcript {transcript_path}: {e}", file=sys.stderr)
                        return ''
            elif field == 'user_prompt':
                # For UserPromptSubmit events
                # 【业务含义】UserPromptSubmit 事件的用户输入文本
                return input_data.get('user_prompt', '')

        # Handle special cases by tool type
        if tool_name == 'Bash':
            if field == 'command':
                return tool_input.get('command', '')
                # 【业务含义】Bash 工具的命令字段

        elif tool_name in ['Write', 'Edit']:
            # 【语法】in 检查是否在列表中，等价于 tool_name == 'Write' or tool_name == 'Edit'
            if field == 'content':
                # Write uses 'content', Edit has 'new_string'
                # 【业务含义】Write 工具用 'content' 字段，Edit 工具用 'new_string' 字段
                return tool_input.get('content') or tool_input.get('new_string', '')
                # 【语法】or 短路求值：如果前者为真值则返回前者，否则返回后者
            elif field == 'new_text' or field == 'new_string':
                return tool_input.get('new_string', '')
            elif field == 'old_text' or field == 'old_string':
                # 【业务含义】Edit 工具的旧文本字段
                return tool_input.get('old_string', '')
            elif field == 'file_path':
                return tool_input.get('file_path', '')

        elif tool_name == 'MultiEdit':
            # 【业务含义】MultiEdit 工具支持同时编辑多处，edits 是编辑列表
            if field == 'file_path':
                return tool_input.get('file_path', '')
            elif field in ['new_text', 'content']:
                # Concatenate all edits
                edits = tool_input.get('edits', [])
                # 【语法】生成器表达式 + join() 拼接所有编辑的新文本
                return ' '.join(e.get('new_string', '') for e in edits)

        return None  # 字段未找到


    def _regex_match(self, pattern: str, text: str) -> bool:
        # 【业务功能】使用正则表达式检查文本是否匹配模式
        # 通过缓存的编译函数优化性能
        """Check if pattern matches text using regex.

        Args:
            pattern: Regex pattern
            text: Text to match against

        Returns:
            True if pattern matches
        """
        try:
            # Use cached compiled regex (LRU cache with max 128 patterns)
            regex = compile_regex(pattern)
            # 【语法】调用模块级的缓存编译函数，相同模式只编译一次
            return bool(regex.search(text))
            # 【语法】regex.search() 返回 Match 对象或 None
            # bool() 将 Match 转为 True，None 转为 False

        except re.error as e:
            # 【语法】re.error 是无效正则表达式时抛出的异常
            print(f"Invalid regex pattern '{pattern}': {e}", file=sys.stderr)
            return False


# For testing
if __name__ == '__main__':
    from hookify.core.config_loader import Condition, Rule

    # Test rule evaluation
    rule = Rule(
        name="test-rm",
        enabled=True,
        event="bash",
        conditions=[
            Condition(field="command", operator="regex_match", pattern=r"rm\s+-rf")
        ],
        message="Dangerous rm command!"
    )

    engine = RuleEngine()

    # Test matching input
    test_input = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "rm -rf /tmp/test"
        }
    }

    result = engine.evaluate_rules([rule], test_input)
    print("Match result:", result)

    # Test non-matching input
    test_input2 = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "ls -la"
        }
    }

    result2 = engine.evaluate_rules([rule], test_input2)
    print("Non-match result:", result2)
