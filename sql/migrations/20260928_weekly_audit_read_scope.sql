-- Extend the existing owner-only SELECT role for the bounded weekly audit.
-- The runtime login inherits only stock_agent_dashboard and cannot execute writes.

REVOKE ALL PRIVILEGES ON TABLE
  public.suggestion_grades,
  public.lessons,
  public.daily_snapshots
FROM stock_agent_dashboard;

GRANT SELECT (id, suggestion_id, graded_at, result, price_then, price_later,
              horizon_days, note, benchmark_ticker, stock_return_pct,
              benchmark_return_pct, excess_return_pct, mfe_pct, mae_pct,
              entry_hit_at, stop_hit_at, target_hit_at, invalidation_hit_at,
              coverage_status, horizon_sessions, policy_version, final_action,
              direction_success)
  ON public.suggestion_grades TO stock_agent_dashboard;
GRANT SELECT (id, entry_date, category, content, created_at)
  ON public.lessons TO stock_agent_dashboard;
GRANT SELECT (id, snap_date, ticker, close, day_move_pct, rsi14, sma50, sma200, macd_hist)
  ON public.daily_snapshots TO stock_agent_dashboard;

ALTER TABLE public.suggestion_grades ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lessons ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.daily_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS owner_dashboard_select_grades ON public.suggestion_grades;
CREATE POLICY owner_dashboard_select_grades ON public.suggestion_grades
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_lessons ON public.lessons;
CREATE POLICY owner_dashboard_select_lessons ON public.lessons
  FOR SELECT TO stock_agent_dashboard USING (true);
DROP POLICY IF EXISTS owner_dashboard_select_snapshots ON public.daily_snapshots;
CREATE POLICY owner_dashboard_select_snapshots ON public.daily_snapshots
  FOR SELECT TO stock_agent_dashboard USING (true);
