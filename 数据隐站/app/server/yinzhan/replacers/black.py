"""涂黑替换器（不可逆）。

出口只有两种语义：给 AI 读的是"换名版"（surrogate），给人看的是"涂黑版"
（black / mask）。本模块负责后者里最彻底的一种：不产生任何可还原的假值，
只告诉导出器"这块要真实移除内容"。

约定（CONTRACT §5）：``Decision.black=True`` 表示涂黑；导出器必须做**破坏性
重写**，不能只画黑框。
"""
from __future__ import annotations

from ..registry import register_replacer
from ..udm import Decision, ReplaceContext, Rule


def build(rule: Rule, value: str, ctx: ReplaceContext) -> Decision:
    """返回"涂黑"决策：无替换值、``black=True``。

    :param rule: 命中的规则（本策略不依赖它）。
    :param value: 原值（本策略不保留，任何形式都不写进结果）。
    :param ctx: 替换上下文（本策略不使用 session）。
    """
    return Decision(strategy="black", replacement=None, black=True)


def build_drop(rule: Rule, value: str, ctx: ReplaceContext) -> Decision:
    """删除策略：整段替换为空串（导出器按空替换处理）。"""
    return Decision(strategy="drop", replacement="", black=False)


def build_keep(rule: Rule, value: str, ctx: ReplaceContext) -> Decision:
    """保留策略：原样写回（用户显式拒绝该实体时使用）。"""
    return Decision(strategy="keep", replacement=value, black=False)


register_replacer("black", build)
# CONTRACT §5 的内置策略里 drop/keep 不需要单独的替换器文件，挂在这里统一注册，
# 避免任何调用方 get_replacer("drop") 拿到 None。
register_replacer("drop", build_drop)
register_replacer("keep", build_keep)

__all__ = ["build", "build_drop", "build_keep"]
