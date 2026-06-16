// Client for the Garmin → Strava sync endpoints (api/routers/sync.py).
// Self-contained: a local http() helper (mirrors lib/api.ts's module-private one)
// plus typed fetch/route calls and an SSE consumer for the export stream.

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ── /sync/fetch ─────────────────────────────────────────────────────────────

export interface SyncActivity {
  id: number;
  name: string;
  type: string;
  date: string;
  distance_km: number;
  duration_s: number;
  avg_hr: number | null;
  elevation_m: number | null;
  calories: number | null;
  start_lat: number | null;
  start_lon: number | null;
  has_polyline: boolean;
  in_strava: boolean | null;
}

export interface SyncFetchResponse {
  activities: SyncActivity[];
  has_matches: boolean;
  start: string;
  end: string;
}

export function syncFetch(start: string, end: string): Promise<SyncFetchResponse> {
  return http<SyncFetchResponse>("/sync/fetch", {
    method: "POST",
    body: JSON.stringify({ start, end }),
  });
}

// ── /sync/route ─────────────────────────────────────────────────────────────

export interface SyncRouteResponse {
  coords: [number, number][];
}

export function syncRoute(activityId: number): Promise<SyncRouteResponse> {
  return http<SyncRouteResponse>(`/sync/route?activity_id=${activityId}`);
}

// ── /sync/export (SSE) ────────────────────────────────────────────────────────

export interface ExportActivityRef {
  id: number;
  name?: string;
  date?: string;
}

export type ExportStatus = "ok" | "duplicate" | "skipped" | "error";

export interface ExportProgress {
  index: number;
  total: number;
  name: string;
}

export interface ExportResult {
  index: number;
  total: number;
  name: string;
  status: ExportStatus;
  message?: string;
  url?: string;
}

export interface ExportSummary {
  ok: number;
  duplicate: number;
  skipped: number;
  error: number;
}

export interface ExportHandlers {
  onProgress?: (p: ExportProgress) => void;
  onResult?: (r: ExportResult) => void;
  onSummary?: (s: ExportSummary) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

/** POST the export request and consume the SSE stream. Returns an abort function. */
export function streamExport(
  activities: ExportActivityRef[],
  handlers: ExportHandlers,
): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch("/api/sync/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ activities }),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`export ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      // SSE framing: blocks separated by a blank line, each with an `event:`
      // line and one or more `data:` lines (same loop as streamChat).
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          if (!block.trim()) continue;
          let event = "message";
          const dataLines: string[] = [];
          for (const line of block.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          const raw = dataLines.join("\n");
          let data: unknown = {};
          try {
            data = raw ? JSON.parse(raw) : {};
          } catch {
            data = { raw };
          }
          switch (event) {
            case "progress":
              handlers.onProgress?.(data as ExportProgress);
              break;
            case "result":
              handlers.onResult?.(data as ExportResult);
              break;
            case "summary":
              handlers.onSummary?.(data as ExportSummary);
              break;
            case "error":
              handlers.onError?.((data as { message?: string }).message ?? "Export error");
              break;
            case "done":
              handlers.onDone?.();
              break;
          }
        }
      }
    } catch (err) {
      if (!controller.signal.aborted) {
        handlers.onError?.(err instanceof Error ? err.message : String(err));
        handlers.onDone?.();
      }
    }
  })();

  return () => controller.abort();
}
