import type { SourceLink } from "@stocks-agent/dashboard-contracts";

const APPROVED_SOURCE_HOSTS = new Set([
  "api.gdeltproject.org",
  "www.alphavantage.co",
  "finnhub.io",
  "query1.finance.yahoo.com",
  "www.sec.gov",
  "data.sec.gov",
  "www.federalregister.gov",
  "www.whitehouse.gov",
  "www.energy.gov",
  "www.defense.gov",
  "api.eia.gov",
  "www.eia.gov",
  "api.stlouisfed.org",
  "fred.stlouisfed.org",
  "api.bls.gov",
  "www.bls.gov",
  "apps.bea.gov",
  "www.bea.gov",
]);

export function safeHttpsUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    if (
      parsed.protocol !== "https:"
      || parsed.username
      || parsed.password
      || (parsed.port && parsed.port !== "443")
      || !APPROVED_SOURCE_HOSTS.has(parsed.hostname)
    ) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

export function SafeSourceLink({ source }: { source: SourceLink }) {
  const url = safeHttpsUrl(source.url);
  return url
    ? <a href={url} target="_blank" rel="noreferrer noopener">{source.label}</a>
    : <span>{source.label}</span>;
}
