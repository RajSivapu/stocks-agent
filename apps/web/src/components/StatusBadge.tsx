import { titleCase } from "../lib/format";

export function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase().replaceAll(" ", "_");
  return (
    <span className={`status-badge status-${normalized}`} data-status={normalized}>
      {titleCase(status)}
    </span>
  );
}
