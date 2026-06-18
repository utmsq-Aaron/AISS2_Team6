import { useQuery } from "@tanstack/react-query";

import { getServerHealth } from "../lib/api";

const GREEN = "#10b981";
const RED = "#ef4444";

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className="inline-block h-2 w-2 flex-shrink-0 rounded-full"
      style={{ background: ok ? GREEN : RED }}
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
      <div className="mb-1.5 flex items-center gap-2.5 text-[11px] text-text-muted">
        <span>🔑 Service</span>
        <span>🖥️ Server</span>
      </div>
      {(data?.servers ?? []).map((s) => (
        <div key={s.key} className="flex items-center gap-1.5">
          <span title="Service connected">🔑</span>
          <Dot ok={s.service_ok} />
          <span className="ml-1" title="MCP server running">🖥️</span>
          <Dot ok={s.server_up} />
          <span className="ml-0.5 text-[13px] text-text-primary/80">
            {s.label}
            {s.key === "garmin" && data?.garmin_mock && (
              <span className="ml-1 text-[11px] font-semibold text-metric-amber">(Mock)</span>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
