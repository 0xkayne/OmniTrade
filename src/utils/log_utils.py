"""
日志工具函数 - 提供统一的阶段分隔符和格式化输出

将日志输出划分为以下阶段:
1. 启动阶段 (Startup) - 程序入口, 进程锁获取
2. 初始化阶段 (Initialization) - 配置加载, 交易所连接
3. 交易阶段 (Trading) - 资金检查, 刷量循环
4. 监控阶段 (Monitoring) - 周期统计报告
5. 关闭阶段 (Shutdown) - 平仓, 连接关闭
"""

from enum import Enum
from typing import Optional


class LogStage(Enum):
    """日志阶段枚举"""
    STARTUP = "🚀 启动"
    INITIALIZATION = "⚙️  初始化"
    TRADING = "💹 交易"
    MONITORING = "📊 监控"
    SHUTDOWN = "🛑 关闭"


# 分隔符宽度
SEPARATOR_WIDTH = 60


def print_stage(stage: LogStage, subtitle: Optional[str] = None):
    """
    打印阶段分隔符 (主阶段)
    
    Args:
        stage: 阶段枚举值
        subtitle: 可选的副标题
    """
    title = stage.value
    
    print()
    print("╔" + "═" * SEPARATOR_WIDTH + "╗")
    
    # 中心对齐标题
    padding = (SEPARATOR_WIDTH - len(title) - 2) // 2
    # 中文字符占用2个显示宽度，需要调整
    display_len = _display_width(title)
    padding = (SEPARATOR_WIDTH - display_len - 2) // 2
    print("║" + " " * padding + " " + title + " " * (SEPARATOR_WIDTH - padding - display_len - 1) + "║")
    
    if subtitle:
        sub_display_len = _display_width(subtitle)
        sub_padding = (SEPARATOR_WIDTH - sub_display_len) // 2
        print("║" + " " * sub_padding + subtitle + " " * (SEPARATOR_WIDTH - sub_padding - sub_display_len) + "║")
    
    print("╚" + "═" * SEPARATOR_WIDTH + "╝")


def print_substage(substage_name: str):
    """
    打印子阶段分隔符
    
    Args:
        substage_name: 子阶段名称
    """
    display_len = _display_width(substage_name)
    remaining = SEPARATOR_WIDTH - display_len - 4  # 4 = 前后各2个字符
    print()
    print("── " + substage_name + " " + "─" * max(0, remaining))


def print_separator():
    """打印简单分隔线"""
    print("─" * SEPARATOR_WIDTH)


def print_section_end():
    """打印阶段结束分隔符"""
    print()
    print("─" * SEPARATOR_WIDTH)
    print()


def _display_width(s: str) -> int:
    """
    计算字符串的显示宽度（中文字符算2，其他算1）
    
    Args:
        s: 输入字符串
        
    Returns:
        显示宽度
    """
    width = 0
    for char in s:
        # 简化判断：CJK字符范围
        if '\u4e00' <= char <= '\u9fff' or '\u3000' <= char <= '\u303f':
            width += 2
        elif char in '🚀⚙️💹📊🛑✅❌⚠️💰🔧📋🔄🔍💫🎯📉💤🔒🔓':
            width += 2  # emoji 通常占2个字符宽度
        else:
            width += 1
    return width


def format_key_value(key: str, value, width: int = 20) -> str:
    """
    格式化键值对输出
    
    Args:
        key: 键名
        value: 值
        width: 键名显示宽度
        
    Returns:
        格式化后的字符串
    """
    key_display_width = _display_width(key)
    padding = " " * max(0, width - key_display_width)
    return f"  {key}{padding}: {value}"
