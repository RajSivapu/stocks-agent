import { Component, type PropsWithChildren, type ReactNode } from "react";

export class ErrorBoundary extends Component<PropsWithChildren, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(): void {
    // Raw payloads and stack details are deliberately not rendered.
  }

  render(): ReactNode {
    if (this.state.failed) {
      return (
        <main className="auth-layout">
          <section className="auth-card">
            <p className="eyebrow">Protected view paused</p>
            <h1>The dashboard needs a refresh</h1>
            <p>No financial details were retained on this error screen.</p>
            <button className="primary-button" onClick={() => window.location.reload()}>Reload dashboard</button>
          </section>
        </main>
      );
    }
    return this.props.children;
  }
}
