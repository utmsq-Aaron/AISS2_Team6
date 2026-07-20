import { useQuery } from "@tanstack/react-query";

import { getServerHealth } from "../lib/api";
import { C_GREEN, C_RED } from "../theme/tokens";
import { InfoHint } from "./InfoHint";

function Dot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      role="img"
      aria-label={label}
      className="inline-block h-2 w-2 flex-shrink-0 rounded-full"
      style={{ background: ok ? C_GREEN : C_RED }}
    />
  );
}

// Service status — lives in Settings (moved off the sidebar). Two checks per
// integration: Auth = the account/credentials are connected; Server = the local
// MCP service is up. Refreshes on its own while the Settings page is open.
export function ServiceStatus() {
  const { data, isLoading } = useQuery({
    queryKey: ["health", "servers"],
    queryFn: getServerHealth,
    refetchInterval: 8000,
  });
  const servers = data?.servers ?? [];

  return (
    <div>
      <div className="mb-2 flex items-center gap-1.5">
        <h3 className="text-lg font-semibold text-text-primary">Service status</h3>
        <InfoHint text="Auth = your account/credentials are connected. Server = the local service is running. Green means OK." />
      </div>
      {isLoading ? (
        <p className="text-sm text-text-muted">Checking…</p>
      ) : servers.length === 0 ? (
        <p className="text-sm text-text-muted">No services reachable right now.</p>
      ) : (
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          {servers.map((s) => (
            <div
              key={s.key}
              className="flex items-center justify-between rounded-lg border border-border bg-bg-surface px-3 py-1.5"
            >
              <span className="text-sm text-text-primary">{s.label}</span>
              <span className="flex items-center gap-3 text-[11px] text-text-muted">
                <span className="flex items-center gap-1">
                  <Dot ok={s.service_ok} label={`${s.label} auth ${s.service_ok ? "connected" : "not connected"}`} />
                  Auth
                </span>
                <span className="flex items-center gap-1">
                  <Dot ok={s.server_up} label={`${s.label} server ${s.server_up ? "up" : "down"}`} />
                  Server
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
