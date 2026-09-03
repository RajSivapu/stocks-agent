interface Env {
  HEALTH_URL: string;
  TELEGRAM_BOT_TOKEN: string;
  OPERATIONAL_TELEGRAM_CHAT_ID: string;
}

const ALERT_TEXT =
  "STOCK AGENT UNAVAILABLE: the public health check failed. Check Supabase and the deployment status.";

function validatedHealthUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("health URL is invalid");
  }
  if (
    url.protocol !== "https:" ||
    !/^[a-z0-9][a-z0-9-]{2,62}\.supabase\.co$/.test(url.hostname) ||
    url.pathname !== "/functions/v1/app-api/healthz" ||
    url.search || url.hash || url.username || url.password
  ) throw new Error("health URL is invalid");
  return url.href;
}

function validateTelegram(env: Env): void {
  if (!/^\d{6,12}:[A-Za-z0-9_-]{20,}$/.test(env.TELEGRAM_BOT_TOKEN)) {
    throw new Error("Telegram operational credential is invalid");
  }
  if (!/^-?[1-9][0-9]{0,15}$/.test(env.OPERATIONAL_TELEGRAM_CHAT_ID)) {
    throw new Error("Telegram operational destination is invalid");
  }
}

async function isHealthy(response: Response): Promise<boolean> {
  if (!response.ok || response.body === null) return false;
  const declared = response.headers.get("content-length");
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > 1024)) return false;
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > 1024) return false;
  try {
    const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    return !!value && typeof value === "object" && !Array.isArray(value) &&
      Object.keys(value).length === 2 && value.ok === true &&
      !!value.data && typeof value.data === "object" && !Array.isArray(value.data) &&
      Object.keys(value.data).length === 2 && value.data.status === "ok" &&
      value.data.schema_version === 1;
  } catch {
    return false;
  }
}

async function sendAlert(env: Env, fetcher: typeof fetch): Promise<void> {
  validateTelegram(env);
  const response = await fetcher(
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        chat_id: env.OPERATIONAL_TELEGRAM_CHAT_ID,
        text: ALERT_TEXT,
        disable_web_page_preview: true,
      }),
      signal: AbortSignal.timeout(5_000),
    },
  );
  if (!response.ok) throw new Error("health alert delivery failed");
}

export async function runHealthCheck(
  env: Env,
  fetcher: typeof fetch = fetch,
): Promise<"healthy" | "alerted"> {
  const healthUrl = validatedHealthUrl(env.HEALTH_URL);
  let healthy = false;
  try {
    healthy = await isHealthy(await fetcher(healthUrl, {
      method: "GET",
      signal: AbortSignal.timeout(5_000),
    }));
  } catch {
    healthy = false;
  }
  if (healthy) return "healthy";
  await sendAlert(env, fetcher);
  return "alerted";
}

export default {
  async scheduled(_controller: unknown, env: Env): Promise<void> {
    await runHealthCheck(env);
  },
};
