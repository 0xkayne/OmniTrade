#!/usr/bin/env bash
#
# 重新生成 docs/assets/ 下的架构图。
#
# 这是 `docs/docs-paradigm.md` §2 要求的「可执行的重新生成入口」，
# 也是 `harness/meta-skills/README.md`「共享的验证共识」那条要求的落地：
# 能自动生成的产物必须有一个可执行的重新生成入口，否则若干次变更后会静默失真。
#
# 图分两层，必须成对存在：
#   *.dot  —— 源码，手写、可 diff，入库
#   *.svg  —— 生成的位图，由 dot 渲染，入库但**不得手工编辑**
#
# 用法：
#   scripts/render_diagrams.sh              # 重新生成全部，并报告有哪些过期
#   scripts/render_diagrams.sh --check      # 只检查 .svg 是否与 .dot 一致（提交前用）
#   scripts/render_diagrams.sh base-market-layer   # 只重新生成指定的几张
#
# 为什么 .svg 也入库：MkDocs 站点与 GitHub 都要直接显示它，不依赖渲染环境。
# 为什么 .dot 也入库：否则图就成了「没有生成脚本的静态图片」——
#   那正是本项目删掉上一批架构图的原因（见 `git log 3eae116`）。

set -euo pipefail

cd "$(dirname "$0")/.."

ASSETS=docs/assets
DOT_BIN="${DOT_BIN:-dot}"
CHECK_ONLY=0

if [ "${1:-}" = "--check" ]; then
  CHECK_ONLY=1
  shift
fi

if ! command -v "$DOT_BIN" >/dev/null 2>&1; then
  echo "找不到 graphviz 的 $DOT_BIN。装 graphviz 后重试，或用 DOT_BIN 指定路径。" >&2
  echo "  Debian/Ubuntu: apt-get install graphviz" >&2
  exit 2
fi

if [ $# -gt 0 ]; then
  mapfile -t SOURCES < <(for n in "$@"; do echo "$ASSETS/$n.dot"; done)
else
  mapfile -t SOURCES < <(find "$ASSETS" -name '*.dot' | sort)
fi

if [ ${#SOURCES[@]} -eq 0 ]; then
  echo "没有找到任何 .dot 文件（$ASSETS/）。" >&2
  exit 1
fi

stale=()
for src in "${SOURCES[@]}"; do
  if [ ! -f "$src" ]; then
    echo "✗ 源文件不存在：$src" >&2
    exit 1
  fi
  out="${src%.dot}.svg"
  name="$(basename "${src%.dot}")"

  if [ "$CHECK_ONLY" -eq 1 ]; then
    if [ ! -f "$out" ]; then
      echo "✗ $name —— 缺少 .svg"
      stale+=("$name")
      continue
    fi
    # 渲染到临时文件再比对，避免只凭时间戳判断（checkout 会打乱 mtime）
    tmp="$(mktemp)"
    "$DOT_BIN" -Tsvg "$src" -o "$tmp"
    if cmp -s "$tmp" "$out"; then
      echo "✓ $name"
    else
      echo "✗ $name —— .svg 与 .dot 不一致（重新生成：scripts/render_diagrams.sh $name）"
      stale+=("$name")
    fi
    rm -f "$tmp"
  else
    "$DOT_BIN" -Tsvg "$src" -o "$out"
    echo "✓ $name -> $(basename "$out") ($(stat -c%s "$out") bytes)"
  fi
done

echo
if [ "$CHECK_ONLY" -eq 1 ]; then
  if [ ${#stale[@]} -gt 0 ]; then
    echo "✗ ${#stale[@]} 张图与源码不一致：${stale[*]}"
    exit 1
  fi
  echo "✓ 全部一致：${#SOURCES[@]} 张图"
else
  echo "✓ 已重新生成：${#SOURCES[@]} 张图"
fi
