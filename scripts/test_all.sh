#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo_root"

.venv/bin/python -m pytest tests/ -q
node --test tests/test_telegram_parser.mjs tests/test_telegram_webhook_utils.mjs tests/test_telegram_pairing.mjs
npx --yes deno@2.9.6 test supabase/functions/market-briefing-gateway/_shared
npx --yes deno@2.9.6 test --allow-env --allow-net supabase/functions/_shared
npx --yes deno@2.9.6 test supabase/functions/app-api
npx --yes deno@2.9.6 test supabase/functions/telegram-portfolio
npx --yes deno@2.9.6 check supabase/functions/market-briefing-gateway/index.ts
npx --yes deno@2.9.6 check supabase/functions/telegram-portfolio/index.ts
npx --yes deno@2.9.6 check supabase/functions/app-api/index.ts
npx --yes deno@2.9.6 check supabase/functions/_shared/bounded-json.ts \
  supabase/functions/_shared/cors.ts \
  supabase/functions/_shared/errors.ts \
  supabase/functions/_shared/jwt.ts \
  supabase/functions/_shared/postgres.ts

if [[ -f packages/contracts/src/index.ts ]]; then
  npx --yes deno@2.9.6 test packages/contracts/src
  npx --yes deno@2.9.6 check packages/contracts/src/index.ts
fi

if [[ -f apps/web/package.json ]]; then
  npm --workspace apps/web run test
  npm --workspace apps/web run typecheck
  npm --workspace apps/web run build
fi
