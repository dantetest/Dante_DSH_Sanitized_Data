"""规则包加载与编译。

与 ``jobs.py`` 对齐的接口::

    load_packs(directory) -> dict[str, list[Rule]]   # pack_id -> 该包规则列表
    compile_rules(packs, fmt, pack_ids=None) -> list[Rule]
    pack_index() -> list[dict]                       # id/name/version/count

YAML 形状（一个文件就是一个规则包）::

    pack: legal-cn
    name: 法律 · 民事诉讼
    version: 1
    include: [common]        # 可选：并入其它包；被包含的规则保留原 pack 名
    rules:
      - id: cn.case_no
        entity: CASE_NO
        label: 案号
        level: L2
        scope: [pdf, docx, xlsx, text, image, tiff]
        priority: 80
        detect:
          - {type: regex, pattern: '...'}
        replace: {strategy: surrogate}

包列表语义：``load_packs`` 返回的每个包列表 = 该包 include 的包规则 + 自身规则
（按 rule id 去重，本包覆盖被包含包）。所以用户只勾选 ``legal-cn`` 时，
``common`` 里的身份证/手机号规则依然生效。

缓存是"目录级"的：任一 yaml 的 mtime/size 变化、目录里增删文件都会重载，
新增规则包不用重启进程。返回给调用方的是 Rule 的深拷贝，避免调用方改
``rule.enabled`` 之类的字段污染缓存。
"""
from __future__ import annotations

import copy
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import yaml

from .udm import Rule

# 规则默认适用所有格式；个别包在 yaml 里收窄 scope。
DEFAULT_SCOPE: tuple[str, ...] = ("pdf", "docx", "xlsx", "text", "image", "tiff", "mesh")

# 合法的替换策略（与 CONTRACT §5 一致）。
KNOWN_STRATEGIES: frozenset[str] = frozenset({"surrogate", "mask", "black", "drop", "keep"})

# 规则包默认目录：yinzhan/rules/
PACK_DIR: Path = Path(__file__).resolve().parent / "rules"

_LOCK = threading.RLock()

# 目录 -> 缓存条目
_CACHE: dict[str, "_DirCache"] = {}

# 最近一次加载收集到的错误（便于 /api/health 或日志展示）
_LAST_ERRORS: list[str] = []


# ────────────────────────────── 内部结构 ──────────────────────────────

@dataclass
class _RawPack:
    """一个 yaml 文件解析出来的原始内容。"""

    key: str                       # 包标识（pack 字段，缺省用文件名）
    name: str = ""
    version: Any = 0
    include: list[str] = field(default_factory=list)
    rules: list[tuple[dict, str, Any]] = field(default_factory=list)  # (raw, pack, version)
    path: str = ""


@dataclass
class _DirCache:
    """目录缓存：文件签名 + 解析结果。"""

    signature: tuple[tuple[str, int, int], ...]
    packs: dict[str, list[Rule]]
    metas: list[dict]
    errors: list[str]


# ────────────────────────────── 读取 ──────────────────────────────

def _yaml_paths(directory: Path) -> list[Path]:
    try:
        return sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    except OSError:
        return []


def _dir_signature(directory: Path) -> tuple[tuple[str, int, int], ...]:
    """目录内 yaml 的 (文件名, mtime_ns, size) 快照，用于判断是否需要重读。"""
    sig: list[tuple[str, int, int]] = []
    for path in _yaml_paths(directory):
        try:
            st = path.stat()
        except OSError:
            continue
        sig.append((path.name, st.st_mtime_ns, st.st_size))
    return tuple(sig)


def _read_yaml(path: Path, errors: list[str]) -> dict | None:
    """安全读取单个 yaml，失败记录错误返回 None。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        errors.append(f"{path.name}: 读取失败 {exc}")
        return None
    try:
        data = yaml.safe_load(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        try:
            data = yaml.safe_load(raw.decode("gbk"))
        except Exception as exc:  # noqa: BLE001 - 解析错误统一降级
            errors.append(f"{path.name}: 编码/解析失败 {exc}")
            return None
    except yaml.YAMLError as exc:
        errors.append(f"{path.name}: YAML 解析失败 {exc}")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path.name}: 顶层必须是映射")
        return None
    return data


def _producer(data: dict, path: Path) -> _RawPack:
    """把 yaml dict 整理成 _RawPack。"""
    key = str(data.get("pack") or path.stem).strip()
    include = data.get("include") or []
    if isinstance(include, str):
        include = [include]
    if not isinstance(include, list):
        include = []
    version = data.get("version", 0)
    pack = _RawPack(
        key=key,
        name=str(data.get("name") or key),
        version=version,
        include=[str(x).strip() for x in include if str(x).strip()],
        path=str(path),
    )
    rules = data.get("rules") or []
    if not isinstance(rules, list):
        return pack
    for raw in rules:
        if isinstance(raw, dict):
            pack.rules.append((raw, key, version))
    return pack


def _normalize_rule(raw: dict, pack: str, version: Any, source: str,
                    errors: list[str]) -> Rule | None:
    """把一条 yaml 规则规范成 udm.Rule；不合法返回 None。"""
    rid = str(raw.get("id") or "").strip()
    entity = str(raw.get("entity") or "").strip().upper()
    if not rid or not entity:
        errors.append(f"{source}: 规则缺少 id/entity，已跳过 {raw!r:.80}")
        return None

    label = str(raw.get("label") or entity)
    level = str(raw.get("level") or "L2").upper()
    if level not in ("L0", "L1", "L2", "L3"):
        errors.append(f"{source}/{rid}: 非法 level {level!r}，按 L2 处理")
        level = "L2"

    scope_raw = raw.get("scope")
    if scope_raw is None:
        scope = list(DEFAULT_SCOPE)
    elif isinstance(scope_raw, str):
        scope = [scope_raw]
    else:
        scope = [str(s).strip().lower() for s in scope_raw if str(s).strip()]
    if not scope:
        scope = list(DEFAULT_SCOPE)

    detect = raw.get("detect")
    if isinstance(detect, dict):
        detect = [detect]
    if not isinstance(detect, list) or not detect:
        errors.append(f"{source}/{rid}: detect 为空，已跳过")
        return None
    detect_list: list[dict] = []
    for item in detect:
        if not isinstance(item, dict):
            errors.append(f"{source}/{rid}: detect 项必须是映射，已跳过该项")
            continue
        spec = dict(item)
        if not str(spec.get("type") or "").strip():
            errors.append(f"{source}/{rid}: detect 缺少 type，已跳过该项")
            continue
        detect_list.append(spec)
    if not detect_list:
        return None

    replace = raw.get("replace")
    if not isinstance(replace, dict):
        replace = {"strategy": "black"}
    strategy = str(replace.get("strategy") or "black").strip().lower()
    if strategy not in KNOWN_STRATEGIES:
        errors.append(f"{source}/{rid}: 未知替换策略 {strategy!r}，按 black 处理")
        strategy = "black"
    replace = dict(replace)
    replace["strategy"] = strategy

    try:
        priority = int(raw.get("priority", 0))
    except (TypeError, ValueError):
        errors.append(f"{source}/{rid}: priority 非整数，按 0 处理")
        priority = 0

    return Rule(
        id=rid,
        entity=entity,
        label=label,
        level=level,
        scope=scope,
        detect=detect_list,
        replace=replace,
        pack=pack,
        priority=priority,
        enabled=bool(raw.get("enabled", True)),
    )


def _load_directory(directory: Path) -> _DirCache:
    """真正读取目录（无缓存）。"""
    errors: list[str] = []
    raw_packs: dict[str, _RawPack] = {}

    for path in _yaml_paths(directory):
        data = _read_yaml(path, errors)
        if data is None:
            continue
        pack = _producer(data, path)
        if pack.key in raw_packs:
            errors.append(f"{path.name}: 包名 {pack.key!r} 重复，后出现的覆盖前者")
        raw_packs[pack.key] = pack

    # 逐包解析 include（递归，带环检测）
    memo: dict[str, list[Rule]] = {}
    visiting: set[str] = set()

    def resolve(key: str) -> list[Rule]:
        if key in memo:
            return memo[key]
        pack = raw_packs.get(key)
        if pack is None:
            errors.append(f"规则包 {key!r}: include 引用了不存在的包")
            memo[key] = []
            return []
        if key in visiting:
            errors.append(f"规则包 {key!r}: include 存在循环，已截断")
            return []
        visiting.add(key)
        merged: dict[str, Rule] = {}
        for inc in pack.include:
            for rule in resolve(inc):
                merged[rule.id] = rule            # 先放被包含的包
        for raw, pname, version in pack.rules:
            rule = _normalize_rule(raw, pname, version, pack.key, errors)
            if rule is not None:
                merged[rule.id] = rule            # 本包覆盖被包含包
        visiting.discard(key)
        result = list(merged.values())
        memo[key] = result
        return result

    packs: dict[str, list[Rule]] = {key: resolve(key) for key in sorted(raw_packs)}

    # 包间同名规则（非 include 关系）告警
    owner: dict[str, str] = {}
    for key, rules in packs.items():
        for rule in rules:
            if rule.pack != key:
                continue                          # include 进来的规则不参与检查
            prev = owner.get(rule.id)
            if prev is not None and prev != key:
                errors.append(f"规则 {rule.id!r} 同时存在于 {prev!r} 与 {key!r}")
            owner[rule.id] = key

    metas: list[dict] = []
    for key in sorted(raw_packs):
        pack = raw_packs[key]
        own = sum(1 for r in packs.get(key, []) if r.pack == key)
        metas.append({
            "id": key,
            "name": pack.name,
            "version": pack.version,
            "file": Path(pack.path).name,
            "include": list(pack.include),
            "count": own,                               # 本包自有规则数
            "count_total": len(packs.get(key, [])),     # 含 include 后总数
        })

    return _DirCache(
        signature=_dir_signature(directory),
        packs=packs,
        metas=metas,
        errors=errors,
    )


# ────────────────────────────── 对外 API ──────────────────────────────

def load_packs(directory: str | os.PathLike[str] | None = None) -> dict[str, list[Rule]]:
    """加载目录下所有规则包，返回 ``{pack_id: [Rule, ...]}``。

    :param directory: 规则目录，缺省为 ``yinzhan/rules``。
    :return: 包 -> 规则列表（已解析 include；规则对象是深拷贝）。
    """
    directory = Path(directory) if directory else PACK_DIR
    key = str(directory.resolve())

    with _LOCK:
        cached = _CACHE.get(key)
        signature = _dir_signature(directory)
        if cached is not None and cached.signature == signature:
            _LAST_ERRORS.clear()
            _LAST_ERRORS.extend(cached.errors)
            return copy.deepcopy(cached.packs)

        fresh = _load_directory(directory)
        _CACHE[key] = fresh
        _LAST_ERRORS.clear()
        _LAST_ERRORS.extend(fresh.errors)
        return copy.deepcopy(fresh.packs)


def _iter_rule_values(packs: Any) -> Iterator[Rule]:
    """从各种可能的容器形状里逐个产出 Rule（兼容早期扁平 dict 写法）。"""
    if packs is None:
        for rules in load_packs().values():
            yield from rules
        return
    if isinstance(packs, Rule):
        yield packs
        return
    if isinstance(packs, Mapping):
        for value in packs.values():
            if isinstance(value, Rule):
                yield value
            elif isinstance(value, Mapping):
                yield from _iter_rule_values(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, Rule):
                        yield item
        return
    if isinstance(packs, (list, tuple, set)):
        for item in packs:
            if isinstance(item, Rule):
                yield item
            elif isinstance(item, Mapping):
                yield from _iter_rule_values(item)


def compile_rules(packs: Any = None, fmt: str = "text",
                  pack_ids: Iterable[str] | None = None) -> list[Rule]:
    """按格式过滤、按 priority 降序返回规则列表。

    :param packs: :func:`load_packs` 的返回值（也接受 ``list[Rule]``）。
    :param fmt: 文档格式，如 ``pdf`` / ``docx`` / ``text``。
    :param pack_ids: 只启用这些规则包；``None`` 表示全部。
    :return: 排好序的规则列表（priority 高者在前，同优先级按 id 稳定排序）。
    """
    target = (fmt or "text").strip().lower()
    allowed = {str(p).strip() for p in pack_ids} if pack_ids else None

    def keep(rule: Rule) -> bool:
        if not rule.enabled:
            return False
        scope = {str(s).strip().lower() for s in rule.scope}
        return target in scope or "all" in scope or "*" in scope

    found: dict[str, Rule] = {}
    if isinstance(packs, Mapping) and any(isinstance(v, (list, tuple)) for v in packs.values()):
        # 主形态：{pack_id: [Rule...]}。选中某个包 = 启用它 include 进来的全部规则，
        # 所以这里不再按 rule.pack 过滤（common 的规则会出现在每个行业包列表里）。
        keys = [k for k in packs if allowed is None or str(k) in allowed]
        for key in keys:
            for rule in folds(packs[key], Rule):
                if keep(rule):
                    found.setdefault(rule.id, rule)
    else:
        for rule in _iter_rule_values(packs):
            if not keep(rule):
                continue
            if allowed is not None and rule.pack not in allowed:
                continue
            found.setdefault(rule.id, rule)
    return sorted(found.values(), key=lambda r: (-r.priority, r.id))


def folds(value: Any, cls: type) -> list:
    """把单个值/列表都视为 list（内部小工具）。"""
    if value is None:
        return []
    if isinstance(value, cls):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if isinstance(item, cls)]
    return []


def pack_index(directory: str | os.PathLike[str] | None = None) -> list[dict]:
    """``GET /api/packs`` 用的包索引：``[{id, name, version, count}]``。"""
    directory = Path(directory) if directory else PACK_DIR
    load_packs(directory)                       # 确保缓存已建立
    with _LOCK:
        cached = _CACHE.get(str(directory.resolve()))
        if cached is None:
            return []
        return [
            {"id": meta["id"], "name": meta["name"],
             "version": meta["version"], "count": meta["count_total"]}
            for meta in copy.deepcopy(cached.metas)
        ]


def packs_info(directory: str | os.PathLike[str] | None = None) -> list[dict]:
    """比 :func:`pack_index` 更详细的包元信息（含 include/自有规则数）。"""
    directory = Path(directory) if directory else PACK_DIR
    load_packs(directory)
    with _LOCK:
        cached = _CACHE.get(str(directory.resolve()))
        return copy.deepcopy(cached.metas) if cached else []


def load_errors() -> list[str]:
    """最近一次 :func:`load_packs` 收集到的错误/告警。"""
    with _LOCK:
        return list(_LAST_ERRORS)


def invalidate_cache(directory: str | os.PathLike[str] | None = None) -> None:
    """手动丢弃缓存（测试或热重载用；正常情况下靠 mtime 自动失效）。"""
    with _LOCK:
        if directory is None:
            _CACHE.clear()
        else:
            _CACHE.pop(str(Path(directory).resolve()), None)


def get_rule(packs: Any, rule_id: str) -> Rule | None:
    """按 id 取规则（小工具，管线/测试用）。"""
    for rule in _iter_rule_values(packs):
        if rule.id == rule_id:
            return rule
    return None


__all__ = [
    "DEFAULT_SCOPE", "KNOWN_STRATEGIES", "PACK_DIR",
    "load_packs", "compile_rules", "pack_index", "packs_info",
    "load_errors", "invalidate_cache", "get_rule",
]
