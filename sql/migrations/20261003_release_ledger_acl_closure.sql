-- Supabase's public default privileges can grant service_role direct ledger
-- writes. Application credentials must never manufacture deployment receipts.
REVOKE ALL ON TABLE public.stock_agent_release_migration_ledger FROM service_role;
