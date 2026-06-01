#!/usr/bin/env python3
"""Configuration loader for hookify plugin.

Loads and parses .claude/hookify.*.local.md files.
"""

# 【模块功能概述】
# 本模块是 hookify 插件的配置加载器，负责：
# 1. 从 .claude/ 目录下读取所有 hookify.*.local.md 规则文件
# 2. 解析 Markdown 文件中的 YAML frontmatter（前置元数据）和消息正文
# 3. 将解析结果转换为结构化的 Rule 和 Condition 对象供规则引擎使用
#
# 【规则文件格式】
# 规则文件采用 Markdown + YAML frontmatter 格式：
# ---
# name: rule-name        # 规则名称
# enabled: true          # 是否启用
# event: bash            # 事件类型：bash/file/stop/all
# pattern: "rm -rf"      # 简单正则模式（旧式语法）
# conditions:            # 复杂条件列表（新式语法）
#   - field: command
#     operator: regex_match
#     pattern: "rm -rf"
# action: warn           # 动作：warn（警告）或 block（阻止）
# ---
# 这是规则触发时显示的消息正文

import os       # 【语法】提供操作系统接口，如路径拼接 os.path.join()
import sys      # 【语法】提供系统相关功能，如 sys.stderr 标准错误输出
import glob     # 【语法】提供文件路径模式匹配（通配符），如 glob.glob("*.md")
import re       # 【语法】提供正则表达式支持
from typing import List, Optional, Dict, Any  # 【语法】从 typing 模块导入类型注解工具
# List[str] = 字符串列表类型；Optional[str] = 可选字符串（等价于 str | None）
# Dict[str, Any] = 键为 str、值为任意类型的字典；Any = 任意类型
from dataclasses import dataclass, field  # 【语法】导入数据类装饰器和字段工厂
# @dataclass 自动生成 __init__、__repr__ 等方法
# field(default_factory=list) 用于可变默认值（避免 Python 经典的可变默认参数陷阱）


@dataclass
class Condition:
    """A single condition for matching."""
    # 【业务功能】定义规则中的单个匹配条件，是规则引擎的最小判断单元
    # 【语法】@dataclass 装饰器自动生成 __init__(self, field, operator, pattern) 等方法
    # 【语法】类属性声明即定义了实例属性，类型注解同时充当文档
    field: str  # "command", "new_text", "old_text", "file_path", etc.
    # 【业务含义】要检查的字段名，对应 Hook 输入数据中的字段
    # 常见值：command（Bash命令）、new_string（编辑新文本）、file_path（文件路径）
    operator: str  # "regex_match", "contains", "equals", etc.
    # 【业务含义】匹配操作符，决定如何将 pattern 与字段值进行比较
    # regex_match = 正则匹配；contains = 包含子串；equals = 精确相等
    pattern: str  # Pattern to match
    # 【业务含义】匹配模式，具体含义取决于 operator（如正则表达式字符串、子串、精确值等）

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Condition':
        # 【语法】@classmethod 装饰器声明类方法，第一个参数是类本身（cls）而非实例（self）
        # 【语法】返回类型 'Condition' 用字符串包裹是因为前向引用（类定义内部引用自身）
        # 【业务功能】工厂方法，从字典创建 Condition 对象，提供默认值
        """Create Condition from dict."""
        return cls(
            field=data.get('field', ''),        # 【语法】dict.get(key, default) 安全取值
            operator=data.get('operator', 'regex_match'),  # 默认操作符为正则匹配
            pattern=data.get('pattern', '')      # 默认模式为空字符串
        )


@dataclass
class Rule:
    """A hookify rule."""
    # 【业务功能】定义完整的 hookify 规则，包含规则元数据、匹配条件和动作
    name: str
    # 【业务含义】规则名称，用于标识和在消息中显示
    enabled: bool
    # 【业务含义】是否启用，False 的规则会被加载时跳过
    event: str  # "bash", "file", "stop", "all", etc.
    # 【业务含义】事件类型过滤器，决定规则在哪些 Hook 事件中生效
    # bash = Bash 工具调用时；file = 文件编辑时；stop = AI 停止时；all = 所有事件
    pattern: Optional[str] = None  # Simple pattern (legacy)
    # 【业务含义】旧式简单正则模式（向后兼容），新规则应使用 conditions
    # 【语法】Optional[str] = None 表示可选字段，默认值为 None
    conditions: List[Condition] = field(default_factory=list)
    # 【业务含义】条件列表，所有条件必须同时满足（AND 逻辑）规则才匹配
    # 【语法】field(default_factory=list) 使用工厂函数创建默认空列表
    # 不能写成 conditions: List[Condition] = []，因为可变默认值在 Python 中是共享的
    action: str = "warn"  # "warn" or "block" (future)
    # 【业务含义】触发动作：warn = 仅显示警告（不阻止）；block = 阻止操作
    tool_matcher: Optional[str] = None  # Override tool matching
    # 【业务含义】工具匹配器，覆盖默认的事件-工具映射
    # 例如 "Bash" 只匹配 Bash 工具，"Edit|Write" 匹配多种工具，"*" 匹配所有
    message: str = ""  # Message body from markdown
    # 【业务含义】规则触发时显示的消息正文（从 Markdown 的 frontmatter 之后提取）

    @classmethod
    def from_dict(cls, frontmatter: Dict[str, Any], message: str) -> 'Rule':
        # 【业务功能】从 frontmatter 字典和消息正文创建 Rule 对象
        # 支持新旧两种规则语法：旧式 pattern 字段 和 新式 conditions 列表
        """Create Rule from frontmatter dict and message body."""
        # Handle both simple pattern and complex conditions
        conditions = []

        # New style: explicit conditions list
        if 'conditions' in frontmatter:
            # 【业务含义】新式语法：使用显式的 conditions 列表定义多个匹配条件
            cond_list = frontmatter['conditions']
            if isinstance(cond_list, list):
                # 【语法】isinstance() 检查对象是否为指定类型
                # 【语法】列表推导式 [expr for item in iterable]，简洁地创建列表
                conditions = [Condition.from_dict(c) for c in cond_list]

        # Legacy style: simple pattern field
        simple_pattern = frontmatter.get('pattern')
        if simple_pattern and not conditions:
            # 【业务含义】旧式语法：只有简单的 pattern 字段，自动转换为 Condition
            # 仅在没有新式 conditions 时才使用（新式优先）
            # Convert simple pattern to condition
            # Infer field from event
            event = frontmatter.get('event', 'all')
            if event == 'bash':
                field = 'command'    # bash 事件对应 command 字段
            elif event == 'file':
                field = 'new_text'   # file 事件对应 new_text 字段
            else:
                field = 'content'    # 其他事件对应 content 字段

            conditions = [Condition(
                field=field,
                operator='regex_match',  # 旧式语法固定使用正则匹配
                pattern=simple_pattern
            )]

        return cls(
            name=frontmatter.get('name', 'unnamed'),
            enabled=frontmatter.get('enabled', True),
            event=frontmatter.get('event', 'all'),
            pattern=simple_pattern,
            conditions=conditions,
            action=frontmatter.get('action', 'warn'),
            tool_matcher=frontmatter.get('tool_matcher'),
            message=message.strip()  # 【语法】str.strip() 去除首尾空白字符
        )


def extract_frontmatter(content: str) -> tuple[Dict[str, Any], str]:
    # 【业务功能】从 Markdown 内容中提取 YAML frontmatter 和消息正文
    # 【输入】Markdown 文件完整内容字符串
    # 【输出】(frontmatter_dict, message_body) 元组
    # 【语法】tuple[Dict[str, Any], str] 是 Python 3.9+ 的内置泛型类型注解
    """Extract YAML frontmatter and message body from markdown.

    Returns (frontmatter_dict, message_body).

    Supports multi-line dictionary items in lists by preserving indentation.
    """
    if not content.startswith('---'):
        # 【业务含义】没有 frontmatter 标记，整个内容作为消息正文
        return {}, content

    # Split on --- markers
    parts = content.split('---', 2)
    # 【语法】str.split(sep, maxsplit) 最多分割 maxsplit 次
    # 分割结果：['', 'frontmatter内容', '正文内容']（因为以 --- 开头，第一个元素为空串）
    if len(parts) < 3:
        # 【业务含义】--- 标记不足两个，格式不合法
        return {}, content

    frontmatter_text = parts[1]  # --- 和 --- 之间的文本
    message = parts[2].strip()   # 第二个 --- 之后的文本（去除首尾空白）

    # Simple YAML parser that handles indented list items
    # 【业务说明】由于规则文件的 YAML 结构较简单，这里实现了轻量级 YAML 解析器
    # 而非引入 pyyaml 等第三方依赖，保持插件的零依赖特性
    frontmatter = {}
    lines = frontmatter_text.split('\n')

    # 状态机变量
    current_key = None      # 当前正在解析的顶层键名
    current_list = []       # 当前正在构建的列表值
    current_dict = {}       # 当前正在构建的字典项（列表中的字典元素）
    in_list = False         # 是否正在解析列表值
    in_dict_item = False    # 是否正在解析列表中的字典项

    for line in lines:
        # Skip empty lines and comments
        stripped = line.strip()
        # 【语法】str.strip() 去除首尾空白；空行和注释行跳过
        if not stripped or stripped.startswith('#'):
            continue

        # Check indentation level
        indent = len(line) - len(line.lstrip())
        # 【语法】通过原始行和去除前导空白后的长度差，计算缩进级别

        # Top-level key (no indentation or minimal)
        if indent == 0 and ':' in line and not line.strip().startswith('-'):
            # 【业务含义】顶层键值对，如 name: test-rule 或 conditions:
            # Save previous list/dict if any
            if in_list and current_key:
                # 【业务含义】遇到新键时，保存之前正在构建的列表
                if in_dict_item and current_dict:
                    current_list.append(current_dict)
                    current_dict = {}
                frontmatter[current_key] = current_list
                in_list = False
                in_dict_item = False
                current_list = []

            key, value = line.split(':', 1)
            # 【语法】split(':', 1) 只分割第一个冒号，避免值中包含冒号时出错
            key = key.strip()
            value = value.strip()

            if not value:
                # Empty value - list or nested structure follows
                # 【业务含义】值为空表示后续是列表或嵌套结构，如 "conditions:"
                current_key = key
                in_list = True
                current_list = []
            else:
                # Simple key-value pair
                value = value.strip('"').strip("'")
                # 【语法】去除引号包裹（支持单引号和双引号）
                if value.lower() == 'true':
                    value = True    # 【语法】将字符串 "true"/"True" 转为布尔值 True
                elif value.lower() == 'false':
                    value = False   # 【语法】将字符串 "false"/"False" 转为布尔值 False
                frontmatter[key] = value

        # List item (starts with -)
        elif stripped.startswith('-') and in_list:
            # 【业务含义】列表项，如 "- field: command" 或 "- regex_match"
            # Save previous dict item if any
            if in_dict_item and current_dict:
                current_list.append(current_dict)
                current_dict = {}

            item_text = stripped[1:].strip()
            # 【语法】stripped[1:] 去除开头的 "-" 符号

            # Check if this is an inline dict (key: value on same line)
            if ':' in item_text and ',' in item_text:
                # Inline comma-separated dict: "- field: command, operator: regex_match"
                # 【业务含义】单行内联字典，逗号分隔多个键值对
                item_dict = {}
                for part in item_text.split(','):
                    if ':' in part:
                        k, v = part.split(':', 1)
                        item_dict[k.strip()] = v.strip().strip('"').strip("'")
                current_list.append(item_dict)
                in_dict_item = False
            elif ':' in item_text:
                # Start of multi-line dict item: "- field: command"
                # 【业务含义】多行字典项的第一行
                in_dict_item = True
                k, v = item_text.split(':', 1)
                current_dict = {k.strip(): v.strip().strip('"').strip("'")}
            else:
                # Simple list item
                # 【业务含义】简单列表项（纯字符串）
                current_list.append(item_text.strip('"').strip("'"))
                in_dict_item = False

        # Continuation of dict item (indented under list item)
        elif indent > 2 and in_dict_item and ':' in line:
            # 【业务含义】字典项的后续字段（缩进在列表项下方）
            # 如 "- field: command" 下一行的 "  operator: regex_match"
            k, v = stripped.split(':', 1)
            current_dict[k.strip()] = v.strip().strip('"').strip("'")

    # Save final list/dict if any
    if in_list and current_key:
        # 【业务含义】文件末尾，保存最后一个正在构建的列表
        if in_dict_item and current_dict:
            current_list.append(current_dict)
        frontmatter[current_key] = current_list

    return frontmatter, message


def load_rules(event: Optional[str] = None) -> List[Rule]:
    # 【业务功能】从 .claude/ 目录加载所有匹配事件类型的 hookify 规则
    # 这是规则加载的主入口函数，被各 Hook 入口脚本调用
    """Load all hookify rules from .claude directory.

    Args:
        event: Optional event filter ("bash", "file", "stop", etc.)

    Returns:
        List of enabled Rule objects matching the event.
    """
    rules = []

    # Find all hookify.*.local.md files
    # 【业务含义】规则文件命名格式为 hookify.{name}.local.md
    # 如 hookify.dangerous-rm.local.md、hookify.sensitive-files.local.md
    pattern = os.path.join('.claude', 'hookify.*.local.md')
    files = glob.glob(pattern)
    # 【语法】glob.glob() 使用通配符匹配文件路径，返回匹配的文件列表

    for file_path in files:
        try:
            rule = load_rule_file(file_path)
            if not rule:
                continue

            # Filter by event if specified
            if event:
                # 【业务含义】按事件类型过滤：event='all' 的规则对所有事件生效
                if rule.event != 'all' and rule.event != event:
                    continue

            # Only include enabled rules
            if rule.enabled:
                rules.append(rule)

        except (IOError, OSError, PermissionError) as e:
            # File I/O errors - log and continue
            # 【语法】except (A, B, C) as e 同时捕获多种异常类型
            print(f"Warning: Failed to read {file_path}: {e}", file=sys.stderr)
            continue
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            # Parsing errors - log and continue
            print(f"Warning: Failed to parse {file_path}: {e}", file=sys.stderr)
            continue
        except Exception as e:
            # Unexpected errors - log with type details
            # 【语法】type(e).__name__ 获取异常的类名字符串，用于日志记录
            print(f"Warning: Unexpected error loading {file_path} ({type(e).__name__}): {e}", file=sys.stderr)
            continue

    return rules


def load_rule_file(file_path: str) -> Optional[Rule]:
    # 【业务功能】加载单个规则文件，解析其内容并返回 Rule 对象
    # 设计为容错模式：解析失败返回 None 而非抛出异常，避免单个坏文件影响整个插件
    """Load a single rule file.

    Returns:
        Rule object or None if file is invalid.
    """
    try:
        with open(file_path, 'r') as f:
            # 【语法】with 语句是上下文管理器，确保文件在使用后自动关闭
            content = f.read()

        frontmatter, message = extract_frontmatter(content)

        if not frontmatter:
            # 【业务含义】缺少 frontmatter 的文件视为无效规则
            print(f"Warning: {file_path} missing YAML frontmatter (must start with ---)", file=sys.stderr)
            return None

        rule = Rule.from_dict(frontmatter, message)
        return rule

    except (IOError, OSError, PermissionError) as e:
        print(f"Error: Cannot read {file_path}: {e}", file=sys.stderr)
        return None
    except (ValueError, KeyError, AttributeError, TypeError) as e:
        print(f"Error: Malformed rule file {file_path}: {e}", file=sys.stderr)
        return None
    except UnicodeDecodeError as e:
        # 【语法】UnicodeDecodeError 在文件编码不是 UTF-8 时触发
        print(f"Error: Invalid encoding in {file_path}: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: Unexpected error parsing {file_path} ({type(e).__name__}): {e}", file=sys.stderr)
        return None


# For testing
if __name__ == '__main__':
    import sys

    # Test frontmatter parsing
    test_content = """---
name: test-rule
enabled: true
event: bash
pattern: "rm -rf"
---

⚠️ Dangerous command detected!
"""

    fm, msg = extract_frontmatter(test_content)
    print("Frontmatter:", fm)
    print("Message:", msg)

    rule = Rule.from_dict(fm, msg)
    print("Rule:", rule)
