"""Architecture invariants, enforced by machine.

The checkable half of ``docs/developer-guide/standards/code-standards.md``: the prose there
states the principle, these tests cover every module -- including ones that do not exist yet.
That is the whole point of moving a rule out of a document; a rule in prose covers the cases
its author listed, a rule in a test covers every case.

The allowed dependency edges are the table in
``docs/developer-guide/standards/directory-structure.md`` §3. The other checks were shell
snippets inside that doc's §10 and in ``naming-conventions.md`` §3, which only ran when
someone remembered to paste them.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"

# ── §3 允许的跨层依赖 ────────────────────────────────────────────────────────

LAYER_DEPS: dict[str, set[str]] = {
    "cli": {"strategy", "coordinator", "arbitrage", "market", "persistence", "exchange", "observability"},
    "strategy": {"coordinator", "market", "persistence", "exchange", "observability"},
    "coordinator": {"market", "persistence", "exchange", "observability"},
    "market": {"persistence"},
    # Arbitrage calculations consume market objects through structural typing;
    # adapters and coordinators provide them at runtime, so this domain layer
    # has no import-time dependency on another layer.
    # The execution boundary uses the shared exchange OrderRequest contract;
    # venue I/O still remains behind injected adapter objects.
    "arbitrage": {"exchange", "market"},
    "exchange": {"market", "persistence"},
    "persistence": set(),
    "observability": set(),
}

# strategy/ 内部的允许边。`price_watch -> trade_log` 是 directory-structure.md §5.5 记录的
# 唯一功能域间依赖：Telegram `/log` 指令会构造 TradeRecord 写 trades 表。
# `strategy -> algos` 与 `algos -> strategy` 成环：registry 在 get_strategy() 里惰性 import
# algos 以触发注册，而 algos 又 import registry 取 register_strategy。见下面的环检查。
INTRA_STRATEGY: set[tuple[str, str]] = {
    ("strategy", "strategy.algos"),
    ("strategy.algos", "strategy"),
    ("strategy.algos", "strategy.signals"),
    ("strategy.backtest", "strategy"),
    ("strategy.funding_arb", "strategy"),
    ("strategy.price_watch", "strategy"),
    ("strategy.price_watch", "strategy.signals"),
    ("strategy.price_watch", "strategy.trade_log"),
    ("strategy.trade_log", "strategy"),
}

# 模块级构造即副作用：import 这个模块就等于连了交易所或开了数据库。
IO_CONSTRUCTORS = {
    "CCXTExchange",
    "ExchangeFactory",
    "InstrumentRegistry",
    "OrderbookCache",
    "PersistenceStore",
    "PriceWatcher",
}


def _source_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _node_of(path: Path) -> str:
    """``src/cli/main.py`` -> ``cli``; ``src/strategy/price_watch/watcher.py`` -> ``strategy.price_watch``."""
    parts = path.relative_to(SRC).parts
    if len(parts) == 1:  # src/__init__.py
        return "root"
    if parts[0] == "strategy" and len(parts) > 2:
        return f"strategy.{parts[1]}"
    return parts[0]


def _module_to_node(module: str) -> str | None:
    """``src.strategy.price_watch.watcher`` -> ``strategy.price_watch`` (None if not under src)."""
    parts = module.split(".")
    if not parts or parts[0] != "src":
        return None
    if len(parts) < 2:
        return "root"
    if parts[1] == "strategy" and len(parts) > 2:
        candidate = SRC / "strategy" / parts[2]
        return f"strategy.{parts[2]}" if candidate.is_dir() else "strategy"
    return parts[1]


def _layer(node: str) -> str:
    return node.split(".")[0]


def _import_nodes(tree: ast.Module) -> Iterator[tuple[ast.Import | ast.ImportFrom, bool]]:
    """Yield ``(node, is_lazy)`` for every import, lazy meaning inside a function/method."""

    def recurse(stmts: list[ast.stmt], lazy: bool) -> Iterator[tuple[ast.AST, bool]]:
        for stmt in stmts:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                yield stmt, lazy
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield from recurse(stmt.body, True)
            elif isinstance(stmt, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                yield from recurse(stmt.body, lazy)
                yield from recurse(getattr(stmt, "orelse", []), lazy)
                yield from recurse(getattr(stmt, "finalbody", []), lazy)
                for handler in getattr(stmt, "handlers", []):
                    yield from recurse(handler.body, lazy)

    yield from recurse(tree.body, False)


def _import_edges() -> dict[tuple[str, str], tuple[str, bool]]:
    """``(from_node, to_node) -> ("file:line", is_lazy)`` for every cross-package import."""
    edges: dict[tuple[str, str], tuple[str, bool]] = {}
    for f in _source_files():
        src = _node_of(f)
        for node, lazy in _import_nodes(ast.parse(f.read_text())):
            modules = (
                [node.module]
                if isinstance(node, ast.ImportFrom) and node.module
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for mod in modules:
                dst = _module_to_node(mod)
                if dst and dst != src:
                    edges.setdefault((src, dst), (f"{f.relative_to(REPO)}:{node.lineno}", lazy))
    return edges


# ── 依赖方向 ────────────────────────────────────────────────────────────────


def test_only_allowed_dependency_edges_exist():
    """§3 的允许表是封闭的；任何新边都必须先改规范再改代码。"""
    violations = []
    for (src, dst), (where, _) in sorted(_import_edges().items()):
        if _layer(src) == _layer(dst):
            ok = (src, dst) in INTRA_STRATEGY
        else:
            ok = _layer(dst) in LAYER_DEPS.get(_layer(src), set())
        if not ok:
            violations.append(f"  {src} -> {dst}   ({where})")
    assert not violations, (
        "存在未登记的跨层依赖。要么改代码，要么先在 "
        "docs/developer-guide/standards/directory-structure.md §3 登记：\n" + "\n".join(violations)
    )


def test_import_graph_is_acyclic():
    """环只允许存在于「至少一个方向是惰性导入」的地方。

    惰性（函数级）导入是打破 import 环的标准手段。真正的模块级互相导入会在
    import 阶段就炸，这台机器拦不住，但把「靠惰性导入才成立的环」显式记录下来，
    可以让下一个人在加边之前看到它。
    """
    edges = _import_edges()
    bad = []
    for (a, b), (where, lazy_ab) in sorted(edges.items()):
        if (b, a) not in edges:
            continue
        _, lazy_ba = edges[(b, a)]
        if not (lazy_ab or lazy_ba):
            bad.append(f"  {a} <-> {b}   (两侧都是模块级导入, {where})")
    assert not bad, "模块级循环导入：\n" + "\n".join(bad)


def test_persistence_is_a_leaf():
    """persistence 只读写列，不认识任何业务层类型（§5.3）。"""
    bad = []
    for f in sorted((SRC / "persistence").rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            imported = (
                [node.module]
                if isinstance(node, ast.ImportFrom) and node.module
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            bad.extend(
                f"  {f.relative_to(REPO)}:{node.lineno}  {mod}"
                for mod in imported
                if mod.startswith("src.") and not mod.startswith("src.persistence")
            )
    assert not bad, "persistence 不得导入任何业务层：\n" + "\n".join(bad)


# ── 副作用发生在显式调用点 ───────────────────────────────────────────────────


def test_no_io_construction_at_import_time():
    """模块顶层不得构造 I/O 对象 —— 否则 import 就等于连交易所 / 开数据库。"""
    bad = []
    for f in _source_files():
        for node in ast.parse(f.read_text()).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # 函数/类体内是惰性的，正是应该待的地方
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id in IO_CONSTRUCTORS
                ):
                    bad.append(f"  {f.relative_to(REPO)}:{inner.lineno}  {inner.func.id}(...)")
    assert not bad, "import 阶段不得产生副作用：\n" + "\n".join(bad)


def test_config_is_read_only_at_the_cli_boundary():
    """配置只在边界层读取一次并完成归一化（§4）。"""
    bad = []
    for f in _source_files():
        if f.relative_to(SRC).parts[0] == "cli":
            continue
        tree = ast.parse(f.read_text())
        docstrings = {
            ast.get_docstring(n, clean=False)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "config/" in node.value
                and node.value not in docstrings
            ):
                bad.append(f"  {f.relative_to(REPO)}:{node.lineno}  {node.value!r}")
    assert not bad, "config/ 只应由 src/cli/ 读取：\n" + "\n".join(bad)


# ── 命名 ────────────────────────────────────────────────────────────────────


def test_public_symbol_names_are_unique():
    """naming-conventions §3：公开类、类型别名和模块级函数全项目唯一。"""
    seen: dict[str, set[str]] = {}
    for f in _source_files():
        for node in ast.parse(f.read_text()).body:
            names: list[str] = []
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(
                "_"
            ):
                names.append(node.name)
            elif isinstance(node, ast.Assign):
                names.extend(t.id for t in node.targets if isinstance(t, ast.Name) and t.id[:1].isupper())
            for name in names:
                seen.setdefault(name, set()).add(str(f.relative_to(REPO)))

    clashes = {n: sorted(w) for n, w in seen.items() if len(w) > 1}
    assert not clashes, "同名公开符号出现在多个模块：\n" + "\n".join(
        f"  {n}: {', '.join(w)}" for n, w in sorted(clashes.items())
    )


# ── 凭据 ────────────────────────────────────────────────────────────────────


def _strings(obj) -> list[str]:
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return [obj] if isinstance(obj, str) else []


def test_secrets_never_appear_in_docs_or_tests():
    """公共与各网络的 secrets 值不得进入文档、测试或配置模板（§4）。

    抄进测试文件的凭据会随仓库一起提交 —— 这正是本测试存在的理由：它已经抓出过一次。
    """
    config_dir = REPO / "config"
    secrets_paths = [
        path for path in sorted(config_dir.glob("secrets*.yaml")) if not path.name.endswith(".example.yaml")
    ]
    if not secrets_paths:
        pytest.skip("没有本地 secrets 文件（CI / 未配置）")

    # 模板里的占位值不是秘密；未填写的字段会与它们逐字相同。
    example_paths = sorted(config_dir.glob("secrets*.example.yaml"))
    placeholders = {
        value
        for path in example_paths
        for value in _strings(yaml.safe_load(path.read_text()))
        if value.startswith("your_")
    }
    placeholders.add("0x" + "0" * 40)
    # Migrated local files can still contain the former credential placeholders.
    placeholders.update({"your_binance_api_key", "your_binance_secret", "your_wallet_address", "your_private_key"})

    # 短值（如纯数字的 chat_id）在正常文本里会误报，只查够长的。
    values = {
        value
        for path in secrets_paths
        for value in _strings(yaml.safe_load(path.read_text()))
        if len(value) >= 12 and value not in placeholders
    }
    if not values:
        pytest.skip("secrets 文件里没有已填写且够长的值可供检查")

    leaks = []
    targets = example_paths + [
        path for directory in ("docs", "tests") for path in sorted((REPO / directory).rglob("*"))
    ]
    for f in targets:
        if not f.is_file() or "__pycache__" in f.parts:
            continue
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue  # 二进制资源
        leaks.extend(f"  {f.relative_to(REPO)}" for v in values if v in text)
    assert not leaks, "凭据出现在文档或测试里：\n" + "\n".join(sorted(set(leaks)))
