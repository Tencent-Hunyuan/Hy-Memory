#!/usr/bin/env bash
# 天枢 fork 上游监控：拉取 upstream，对比 base tag，报告版本/schema 变化。
# 用法：bash scripts/tianshu/upstream-monitor.sh [base_tag]
#   base_tag 默认 v1.0.6（本 fork 的 base）
set -euo pipefail

BASE_TAG="${1:-v1.0.6}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "== 拉取 upstream =="
git fetch upstream --tags 2>/dev/null || git fetch upstream

UPSTREAM="upstream/main"
echo
echo "== upstream 自 ${BASE_TAG} 以来的提交 =="
COUNT=$(git rev-list --count "${BASE_TAG}..${UPSTREAM}" 2>/dev/null || echo "?")
echo "新增提交数：${COUNT}"
echo
git log --oneline --no-decorate "${BASE_TAG}..${UPSTREAM}" 2>/dev/null | head -30

echo
echo "== 版本与 schema 变化检测 =="
check_field() {
  local label="$1" path="$2" pattern="$3"
  local base now
  base=$(git show "${BASE_TAG}:${path}" 2>/dev/null | grep -Eo "${pattern}" | head -1 || echo "(缺失)")
  now=$(grep -Eo "${pattern}" "${ROOT}/${path}" 2>/dev/null | head -1 || echo "(缺失)")
  echo "  ${label}: base=${base}  upstream-head=$(git show "${UPSTREAM}:${path}" 2>/dev/null | grep -Eo "${pattern}" | head -1 || echo '(缺失)')  fork=${now}"
}

check_field "root version"        "package.json"                          '"version":[[:space:]]*"[^"]+"'
check_field "build_number"        "package.json"                          '"build_number":[[:space:]]*[0-9]+'
check_field "schema_version"      "apps/core/src/one_memory/database.py"  'schema_version[[:space:]]*=[[:space:]]*[0-9]+'
check_field "hy-memory pin"       "apps/core/pyproject.toml"              'hy-memory==[0-9.]+'

echo
echo "== 建议 =="
SCHEMA_BASE=$(git show "${BASE_TAG}:apps/core/src/one_memory/database.py" 2>/dev/null | grep -Eo 'schema_version\s*=\s*[0-9]+' | grep -Eo '[0-9]+' | head -1)
SCHEMA_HEAD=$(git show "${UPSTREAM}:apps/core/src/one_memory/database.py" 2>/dev/null | grep -Eo 'schema_version\s*=\s*[0-9]+' | grep -Eo '[0-9]+' | head -1)
if [[ -n "${SCHEMA_BASE}" && -n "${SCHEMA_HEAD}" && "${SCHEMA_BASE}" != "${SCHEMA_HEAD}" ]]; then
  echo "  ⚠ schema_version ${SCHEMA_BASE} → ${SCHEMA_HEAD}：跟随上游会触发单向迁移门，先在隔离 data dir 验证"
else
  echo "  schema 无变化，可安全 rebase"
fi
echo "  跟随：git rebase ${UPSTREAM}（冲突优先保留 fork 的 tianshu 改动）"
echo "  仅取部分：git cherry-pick <commit>"
