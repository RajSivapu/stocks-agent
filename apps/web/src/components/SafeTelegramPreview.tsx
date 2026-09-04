import type { SourceLink } from "@stocks-agent/dashboard-contracts";

import { SafeSourceLink } from "./SafeSourceLink";

export function SafeTelegramPreview({ text, links = [] }: { text: string; links?: SourceLink[] }) {
  return (
    <div className="telegram-preview">
      <pre>{text}</pre>
      {links.length > 0 && (
        <ul className="source-list" aria-label="Sources">
          {links.map((source, index) => <li key={`${source.label}-${index}`}><SafeSourceLink source={source} /></li>)}
        </ul>
      )}
    </div>
  );
}
