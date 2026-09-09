#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${PYTHON_BIN:-${VENV_PYTHON:-$repo_root/.venv/bin/python}}"
test -x "$python_bin" || { echo "trusted PYTHON_BIN/VENV_PYTHON is required" >&2; exit 1; }
deno_bin="$("$python_bin" -c 'from scripts.check_function_runtime_manifest import _deno_binary; print(_deno_binary())')"
deno_env=(env -i "HOME=${HOME:?}" "PATH=${PATH:?}" NO_COLOR=1)
[[ -z "${DENO_DIR:-}" ]] || deno_env+=("DENO_DIR=$DENO_DIR")
[[ -z "${SYSTEMROOT:-}" ]] || deno_env+=("SYSTEMROOT=$SYSTEMROOT")
[[ -z "${TMPDIR:-}" ]] || deno_env+=("TMPDIR=$TMPDIR")
[[ -z "${XDG_CACHE_HOME:-}" ]] || deno_env+=("XDG_CACHE_HOME=$XDG_CACHE_HOME")
"$python_bin" -m py_compile scripts/verify_personal_stock_agent_v1.py
"$python_bin" scripts/sync_market_calendar.py --check
env -u RUN_DB_INTEGRATION_TESTS "$python_bin" -m pytest -q -m "not db_integration"
node --test tests/*.mjs
"${deno_env[@]}" "$deno_bin" cache \
  --config supabase/functions/deno.json \
  --lock supabase/functions/deno.lock \
  --frozen-lockfile \
  supabase/functions/telegram-portfolio/index.ts \
  supabase/functions/market-briefing-gateway/index.ts \
  supabase/functions/owner-dashboard-api/index.ts
"${deno_env[@]}" "$deno_bin" test --cached-only --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/_shared \
  supabase/functions/owner-dashboard-api
"$python_bin" scripts/check_function_runtime_manifest.py
npm test --workspace @stocks-agent/dashboard-contracts -- --run
npm test --workspace @stocks-agent/web -- --run
npm run typecheck --workspace @stocks-agent/dashboard-contracts
npm run typecheck --workspace @stocks-agent/web
npm run lint --workspace @stocks-agent/web
node scripts/check_dependency_licenses.mjs
VITE_SUPABASE_URL=https://test-project.supabase.co \
VITE_DASHBOARD_API_URL=https://test-project.supabase.co/functions/v1/owner-dashboard-api \
VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_test \
  npm run build --workspace @stocks-agent/web
node scripts/check_dashboard_bundle.mjs apps/web/dist
npm run test:e2e --workspace @stocks-agent/web
