import { useState } from "react";

import type { ChatTrace } from "../../lib/api";

// Mirror of ui/chat.py `_render_trace`: a collapsible "🔍 Agent trace" panel
// (default closed) showing the agent pipeline, the FetchingAgent plan + planned
// MCP calls, a tool-execution table, and the timing breakdown.

const TIMING_LABELS: Array<[string, string]> = [
  ["fetch_ms", "FetchingAgent"],
  ["analysis_ms", "Viz+Flyover (∥)"],
  ["chat_ms", "ChatAgent"],
  ["plan_ms", "Plan"],
  ["exec_ms", "Exec"],
  ["synth_ms", "Synth"],
];

export function AgentTrace({ trace }: { trace: ChatTrace }) {
  const [open, setOpen] = useState(false);
  if (!trace) return null;

  const plan = trace.plan ?? {};
  const toolCalls = trace.tool_calls ?? [];
  const timing = trace.timing ?? {};
  const agents = trace.agents ?? [];
  const error = trace.error;

  const totalMs = Object.values(timing).reduce((a, b) => a + (b || 0), 0);
  const label = `🔍 Agent trace  ·  ${toolCalls.length} tool call(s)  ·  ${totalMs} ms`;

  const reasoning = plan.reasoning ?? "";
  const steps = plan.steps ?? [];

  const sortedCalls = [...toolCalls].sort(
    (a, b) => (b.duration_ms ?? 0) - (a.duration_ms ?? 0),
  );

  return (
    <div className="mt-2 rounded-card border border-border bg-bg-surface/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-2 text-left text-xs text-text-muted hover:text-text-primary"
      >
        <span>{label}</span>
        <span>{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-border px-4 py-3 text-xs">
          {error && (
            <div className="rounded-lg border border-metric-red/40 bg-metric-red/10 px-3 py-2 text-metric-red">
              Orchestrator error: {error}
            </div>
          )}

          {/* Agent pipeline overview */}
          {agents.length > 0 && (
            <div>
              <div className="mb-1 font-semibold text-text-primary">
                Agent pipeline:
              </div>
              {agents.map((ag, i) => (
                <div key={i} className="text-text-muted">
                  Phase {ag.phase} —{" "}
                  <span className="font-semibold text-text-primary">{ag.agent}</span>{" "}
                  — {ag.duration_ms} ms
                  {ag.data_summary && (
                    <div className="pl-3 text-text-muted">↳ {ag.data_summary}</div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Orchestrator plan */}
          {reasoning && (
            <div className="text-text-primary">
              <span className="font-semibold">Plan:</span> {reasoning}
            </div>
          )}
          {steps.length > 0 ? (
            <div>
              <div className="mb-1 font-semibold text-text-primary">
                {steps.length} planned MCP call(s):
              </div>
              <div className="space-y-1">
                {steps.map((s, i) => (
                  <pre
                    key={i}
                    className="overflow-x-auto rounded bg-bg-surface px-2 py-1 font-mono text-[11px] text-text-primary"
                  >
                    {`${s.tool}(${JSON.stringify(s.args ?? {})})  # ${s.label ?? ""}`}
                  </pre>
                ))}
              </div>
            </div>
          ) : (
            !error && (
              <div className="text-text-muted">
                No MCP tool calls needed for this question.
              </div>
            )
          )}

          {/* Tool execution results table */}
          {toolCalls.length > 0 && (
            <div>
              <div className="mb-1 font-semibold text-text-primary">
                MCP execution results:
              </div>
              <table className="w-full table-fixed text-[11px]">
                <thead>
                  <tr className="text-text-muted">
                    <th className="w-[42%] py-1 text-left font-normal">Tool</th>
                    <th className="w-[30%] py-1 text-left font-normal">Label</th>
                    <th className="w-[16%] py-1 text-left font-normal">Duration</th>
                    <th className="w-[12%] py-1 text-left font-normal">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedCalls.map((c, i) => (
                    <tr key={i} className="border-t border-border/50">
                      <td className="py-1 pr-2">
                        <code className="font-mono text-text-primary">{c.tool}</code>
                      </td>
                      <td className="py-1 pr-2 text-text-muted">{c.label ?? "—"}</td>
                      <td className="py-1 pr-2 text-text-muted">
                        {c.duration_ms ?? 0} ms
                      </td>
                      <td className="py-1">{c.error ? "❌" : "✅"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Timing breakdown */}
          {Object.keys(timing).length > 0 && (
            <div className="text-text-muted">
              {TIMING_LABELS.filter(([k]) => k in timing)
                .map(([k, lbl]) => `${lbl} ${timing[k]} ms`)
                .concat([`Total ${totalMs} ms`])
                .join("  ·  ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
