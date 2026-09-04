#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

.venv/bin/python -m py_compile scripts/verify_personal_stock_agent_v1.py
.venv/bin/python -m pytest -q
node --test tests/*.mjs
npx --yes deno@2.9.6 test --config supabase/functions/deno.json \
  supabase/functions/market-briefing-gateway/_shared \
  supabase/functions/owner-dashboard-api
npx --yes deno@2.9.6 check --config supabase/functions/deno.json \
  supabase/functions/telegram-portfolio/index.ts \
  supabase/functions/market-briefing-gateway/index.ts \
  supabase/functions/owner-dashboard-api/index.ts
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
