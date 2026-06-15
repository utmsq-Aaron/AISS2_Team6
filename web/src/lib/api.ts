// Thin client over the FastAPI seam (proxied at /api by Vite in dev, by the Node
// BFF in prod). callTool() is the generic data path; streamChat() consumes SSE.

export interface ToolResult<T = unknown> {
  name: string;
  ok: boolean;
  data: T;
  text?: boolean;
}

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

/** Call an MCP tool by namespaced name `server__tool`. Returns parsed JSON data. */
export async function callTool<T = unknown>(
  name: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  const r = await http<ToolResult<T>>("/tools/call", {
    method: "POST",
    body: JSON.stringify({ name, args }),
  });
  return r.data;
}

export interface ServerStatus {
  key: string;
  label: string;
  server_up: boolean;
  service_ok: boolean;
}
export interface HealthResponse {
  garmin_mock: boolean;
  servers: ServerStatus[];
}

export const getServerHealth = () => http<HealthResponse>("/health/servers");
export const getConfigIssues = () => http<{ issues: string[] }>("/health/config");
export const getSettings = () => http<SettingsResponse>("/settings");
export const putEnv = (values: Record<string, string>) =>
  http<{ written: string[] }>("/settings/env", { method: "PUT", body: JSON.stringify({ values }) });

export interface SettingsResponse {
  integrations: Record<string, boolean>;
  env: Record<string, { set: boolean; value: string; secret: boolean }>;
}

// ── Chat SSE ──────────────────────────────────────────────────────────────────

export type ChatMessage = { role: "user" | "assistant"; content: string };

export interface ChatTrace {
  run_id?: string;
  question?: string;
  answer?: string;
  plan?: { reasoning?: string; steps?: Array<{ tool: string; args?: unknown; label?: string }> };
  tool_calls?: Array<{ tool: string; label?: string; duration_ms?: number; error?: string | null }>;
  timing?: Record<string, number>;
  agents?: Array<{ agent: string; phase: number; duration_ms: number; data_summary?: string }>;
  route_data?: { tool: string; data: Record<string, unknown> } | null;
  chart_hints?: string[];
  actions?: Array<Record<string, unknown>>;
  error?: string | null;
}

export interface ChatHandlers {
  onStatus?: (message: string) => void;
  onToken?: (delta: string) => void;
  onReset?: () => void;
  onTrace?: (trace: ChatTrace) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

/** POST a chat turn and consume the SSE stream. Returns an abort function. */
export function streamChat(
  message: string,
  history: ChatMessage[],
  handlers: ChatHandlers,
): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, history }),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`chat ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      // Parse the SSE framing: blocks separated by a blank line, each with an
      // `event:` line and one or more `data:` lines.
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
          let data: any = {};
          try {
            data = raw ? JSON.parse(raw) : {};
          } catch {
            data = { raw };
          }
          switch (event) {
            case "status": handlers.onStatus?.(data.message); break;
            case "token": handlers.onToken?.(data.delta); break;
            case "reset": handlers.onReset?.(); break;
            case "trace": handlers.onTrace?.(data as ChatTrace); break;
            case "error": handlers.onError?.(data.message); break;
            case "done": handlers.onDone?.(); break;
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

/** Generate LLM charts for a finished chat turn → array of Plotly figure specs. */
export async function generateCharts(trace: ChatTrace): Promise<any[]> {
  const r = await http<{ figures: any[] }>("/charts", {
    method: "POST",
    body: JSON.stringify({ trace }),
  });
  return r.figures || [];
}
