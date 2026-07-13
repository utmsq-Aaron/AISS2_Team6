import { AlertTriangle } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

// NOTE: React error boundaries only catch errors thrown during *render*,
// lifecycle methods, and constructors of the components below them. They do
// NOT catch errors from event handlers, async code (promises/setTimeout), or
// server-side rendering — those keep surfacing through the existing ErrorBox
// states on each page. This boundary exists to stop a render-time throw from
// unmounting the whole shell into a blank white screen.

type Scope = "app" | "page";

interface Props {
  children: ReactNode;
  scope?: Scope;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[fitdash] render error:", error, info.componentStack);
  }

  private handleReset = () => this.setState({ error: null });

  private handleReload = () => window.location.reload();

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const isApp = this.props.scope === "app";
    const wrapperClass = isApp
      ? "flex h-screen items-center justify-center bg-bg-app px-6"
      : "flex min-h-[60vh] items-center justify-center px-6";

    return (
      <div className={wrapperClass}>
        <div className="fd-card max-w-md p-6 text-center">
          <AlertTriangle className="mx-auto mb-3 h-8 w-8 text-metric-red" />
          <h2 className="text-lg font-semibold text-text-primary">
            Something went wrong
          </h2>
          {!isApp && (
            <p className="mt-1 text-sm text-text-muted">
              This page crashed — the rest of the app is still running.
            </p>
          )}
          {error.message && (
            <p className="mt-2 break-words text-sm text-text-muted">
              {error.message}
            </p>
          )}
          <div className="mt-5 flex items-center justify-center gap-3">
            <button className="fd-btn-primary" onClick={this.handleReset}>
              Try again
            </button>
            <button className="fd-btn-secondary" onClick={this.handleReload}>
              Reload app
            </button>
          </div>
        </div>
      </div>
    );
  }
}

// Per-route boundary: keying on the pathname resets the boundary automatically
// on navigation, so a crash on one page doesn't leave a stuck error card when
// the user routes away. Only this wrapper uses router hooks — the class above
// stays hook-free so it can sit at the very top of the tree in main.tsx.
export function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const location = useLocation();
  return (
    <ErrorBoundary scope="page" key={location.pathname}>
      {children}
    </ErrorBoundary>
  );
}
