BEGIN;

-- Migration statement hashes are computed by the protected Python reader.
-- The evidence login no longer needs to reach extension functions or views.
REVOKE USAGE ON SCHEMA extensions
  FROM stock_agent_release_reader, stock_agent_release_reader_runtime;

COMMIT;
