export interface ReceiptStep {
  label: string;
  status: string;
  detail?: string | null;
}

export function ReceiptTimeline({ steps }: { steps: ReceiptStep[] }) {
  return (
    <ol className="receipt-timeline">
      {steps.map((step, index) => (
        <li key={`${step.label}-${index}`}>
          <span className="timeline-marker" aria-hidden="true" />
          <div><strong>{step.label}</strong><span className="badge">{step.status}</span>{step.detail && <p>{step.detail}</p>}</div>
        </li>
      ))}
    </ol>
  );
}
