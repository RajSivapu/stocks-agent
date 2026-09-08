import { renderReportDelivery } from "../../supabase/functions/market-briefing-gateway/_shared/reports.ts";

const request = JSON.parse(await new Response(Deno.stdin.readable).text());
const delivery = renderReportDelivery(
  request.input,
  [],
  {
    dashboardBaseUrl: "https://stocks.example.test",
    allowedDashboardOrigins: ["https://stocks.example.test"],
  },
  request.packet,
);
if (!delivery.payload) throw new Error(`report suppressed without payload: ${delivery.reason}`);
console.log(JSON.stringify(delivery.payload));
