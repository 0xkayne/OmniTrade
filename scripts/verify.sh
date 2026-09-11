#!/usr/bin/env bash
#
# 提交前的验证门禁。这是 `docs/docs-paradigm.md` §9 和 `CLAUDE.md`「Disk quota」两节
# 的手工步骤的可执行形式 —— 那些步骤以前只以散文存在，每次都要重新拼一遍环境变量。
#
# 用法：
#   scripts/verify.sh            # 全部阶段
#   scripts/verify.sh lint arch  # 只跑指定阶段
#
# 阶段：lint · format · arch · skills · test · docs

set -euo pipefail

# 所有生成物都指向 /share_data/wangziping/：家目录配额已满，字节码缓存或 ruff 缓存
# 写进去会直接 EDQUOT（CLAUDE.md「Disk quota」）。仓库内的 .pytest_cache / .ruff_cache
# 已是符号链接，这里补上剩下的两个。
export PYTHONPYCACHEPREFIX=/share_data/wangziping/pycache
export RUFF_CACHE_DIR=/share_data/wangziping/ruff-cache
export UV_LINK_MODE=copy

cd "$(dirname "$0")/.."

STAGES=(lint format arch skills test docs)
if [ $# -gt 0 ]; then
  STAGES=("$@")
fi

failed=()
for stage in "${STAGES[@]}"; do
  echo
  echo "── $stage ──────────────────────────────────────────────"
  case "$stage" in
    lint) uv run --locked ruff check . ;;
    format) uv run --locked ruff format --check . ;;
    arch) uv run --locked pytest tests/test_architecture.py -q ;;
    skills) uv run --locked python scripts/validate_skills.py ;;
    test) uv run --locked pytest -m "not network" -q ;;
    docs)
      uv run --locked --group docs mkdocs build --strict \
        --site-dir /share_data/wangziping/tmp/omnitrade-mkdocs-site
      ;;
    *)
      echo "未知阶段：$stage（可选：${STAGES[*]}）" >&2
      exit 2
      ;;
  esac || failed+=("$stage")
done

echo
if [ ${#failed[@]} -gt 0 ]; then
  echo "✗ 未通过：${failed[*]}"
  exit 1
fi
echo "✓ 全部通过：${STAGES[*]}"
