-- Keep the owner dashboard runtime's effective EXECUTE authority exact.
-- These trigger helpers were still inherited through PostgreSQL's PUBLIC grant.

REVOKE EXECUTE ON FUNCTION public.initialize_market_intelligence_window()
  FROM PUBLIC, stock_agent_dashboard;
REVOKE EXECUTE ON FUNCTION public.reuse_market_source_item_if_immutable()
  FROM PUBLIC, stock_agent_dashboard;

GRANT EXECUTE ON FUNCTION public.read_overdue_scheduled_market_phases(TIMESTAMPTZ)
  TO stock_agent_dashboard;
GRANT EXECUTE ON FUNCTION public.read_owner_intelligence_v2(INT)
  TO stock_agent_dashboard;
