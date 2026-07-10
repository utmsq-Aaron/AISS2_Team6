import { useQuery } from "@tanstack/react-query";

import { getServerHealth } from "../lib/api";
import { C_GREEN, C_RED } from "../theme/tokens";

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

// Live sidebar status — mirrors app.py _status_dots (🔑 service · 🖥️ server),
// auto-refreshing every 5 s.
export function StatusDots() {
  const { data } = useQuery({
    queryKey: ["health", "servers"],
    queryFn: getServerHealth,
    refetchInterval: 5000,
  });

  return (
    <div className="space-y-1.5">
      <div
        className="mb-1.5 flex items-center gap-2.5 text-[11px] text-text-muted"
        aria-hidden="true"
      >
        <span>🔑 Service</span>
        <span>🖥️ Server</span>
      </div>
      {(data?.servers ?? []).map((s) => (
        <div key={s.key} className="flex items-center gap-1.5">
          <span aria-hidden="true">🔑</span>
          <Dot
            ok={s.service_ok}
            label={`${s.label} service ${s.service_ok ? "connected" : "not connected"}`}
          />
          <span className="ml-1" aria-hidden="true">
            🖥️
          </span>
          <Dot
            ok={s.server_up}
            label={`${s.label} server ${s.server_up ? "running" : "down"}`}
          />
          <span className="ml-0.5 text-[13px] text-text-primary/80">{s.label}</span>
        </div>
      ))}
    </div>
  );
}
