import type { SourceLink } from "@stocks-agent/dashboard-contracts";

export function safeHttpsUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" ? parsed.href : null;
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
