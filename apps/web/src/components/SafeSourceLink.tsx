import { safeSourceUrl, type SourceLink } from "@stocks-agent/dashboard-contracts";

export function safeHttpsUrl(value: string | null): string | null {
  return safeSourceUrl(value);
}

export function SafeSourceLink({ source }: { source: SourceLink }) {
  const url = safeHttpsUrl(source.url);
  return url
    ? <a href={url} target="_blank" rel="noreferrer noopener">{source.label}</a>
    : <span>{source.label}</span>;
}
