import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { AgentTrace } from "../components/chat/AgentTrace";
import { Markdown } from "../components/chat/Markdown";
import { RouteResult } from "../components/chat/RouteResult";
import type { RouteData } from "../components/chat/RouteResult";
import { PageHeader } from "../components/PageHeader";
import { PlotlyFigure } from "../components/PlotlyChart";
import {
  generateCharts,
  getServerHealth,
  streamChat,
} from "../lib/api";
import type { ChatMessage, ChatTrace } from "../lib/api";

// Chat tab — AI sports analyst over the tool-agnostic core engine.
// Port of ui/chat.py: a scrollable message list with the input pinned at the
// bottom (ChatGPT / Claude style). Each assistant turn renders the answer as
// markdown, a live status accordion, a collapsible agent trace, an optional
// route map, and LLM-generated charts.

const PLACEHOLDER =
  "e.g. What are my personal bests?  /  How was my sleep last week?  /  Show HR peaks before sleep in the last 4 weeks";

// One completed assistant turn, including its trace + lazily-loaded charts.
interface AssistantTurn {
  content: string;
  trace: ChatTrace;
  statusSteps: string[];
  durationMs: number;
  charts: unknown[] | null; // null until generateCharts resolves
}

type Turn =
  | { role: "user"; content: string }
  | { role: "assistant"; turn: AssistantTurn };

// Phase → icon, mirroring ui/chat.py `_update_status`.
function statusIcon(msg: string): string {
  if (msg.includes("Phase 1")) return "🔍";
  if (msg.includes("Phase 2")) return "📊";
  if (msg.includes("Phase 3")) return "💬";
  return "⏳";
}

export function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);

  // In-flight assistant turn state.
  const [liveAnswer, setLiveAnswer] = useState("");
  const [liveStatus, setLiveStatus] = useState<string[]>([]);

  const abortRef = useRef<(() => void) | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Turn indices whose chart generation has already been kicked off (once each).
  const chartsFetched = useRef<Set<number>>(new Set());

  // ── Tool availability ──────────────────────────────────────────────────────
  const { data: health, refetch: refetchHealth } = useQuery({
    queryKey: ["health-servers"],
    queryFn: getServerHealth,
    staleTime: 30_000,
  });
  const [refreshing, setRefreshing] = useState(false);
  const reachable = (health?.servers ?? []).filter((s) => s.server_up);
  const noTools = health != null && reachable.length === 0;

  const refreshTools = async () => {
    setRefreshing(true);
    try {
      await fetch("/api/chat/refresh-tools", { method: "POST" });
      await refetchHealth();
    } finally {
      setRefreshing(false);
    }
  };

  // ── Autoscroll to bottom on any change while a turn is in flight ────────────
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns, liveAnswer, liveStatus]);

  // ── Lazily load charts once per completed assistant turn ────────────────────
  useEffect(() => {
    turns.forEach((t, idx) => {
      if (t.role !== "assistant" || t.turn.charts !== null) return;
      if (chartsFetched.current.has(idx)) return;
      chartsFetched.current.add(idx);

      const writeCharts = (figs: unknown[]) =>
        setTurns((prev) => {
          const next = [...prev];
          const cur = next[idx];
          if (cur && cur.role === "assistant") {
            next[idx] = { role: "assistant", turn: { ...cur.turn, charts: figs } };
          }
          return next;
        });

      generateCharts(t.turn.trace)
        .then((figs) => writeCharts(figs))
        .catch(() => writeCharts([]));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turns]);

  useEffect(
    () => () => {
      abortRef.current?.();
    },
    [],
  );

  // ── Send a turn ─────────────────────────────────────────────────────────────
  const send = () => {
    const message = input.trim();
    if (!message || streaming) return;

    // Prior history (excluding the new message), the exact `history` arg shape.
    const history: ChatMessage[] = turns.map((t) =>
      t.role === "user"
        ? { role: "user", content: t.content }
        : { role: "assistant", content: t.turn.content },
    );

    setTurns((prev) => [...prev, { role: "user", content: message }]);
    setInput("");
    setStreaming(true);
    setLiveAnswer("");
    setLiveStatus([]);

    const startedAt = Date.now();
    const steps: string[] = [];
    let answerSoFar = "";
    let finalTrace: ChatTrace = {};

    abortRef.current = streamChat(message, history, {
      onStatus: (msg) => {
        steps.push(`${statusIcon(msg)} ${msg}`);
        setLiveStatus([...steps]);
      },
      onToken: (delta) => {
        answerSoFar += delta;
        setLiveAnswer(answerSoFar);
      },
      onReset: () => {
        answerSoFar = "";
        setLiveAnswer("");
      },
      onTrace: (trace) => {
        finalTrace = trace;
        if (trace.answer) {
          answerSoFar = trace.answer;
          setLiveAnswer(answerSoFar);
        }
      },
      onError: (msg) => {
        finalTrace = { ...finalTrace, error: finalTrace.error ?? msg };
        if (!answerSoFar) {
          answerSoFar = `⚠ ${msg}`;
          setLiveAnswer(answerSoFar);
        }
      },
      onDone: () => {
        const durationMs =
          Object.values(finalTrace.timing ?? {}).reduce((a, b) => a + (b || 0), 0) ||
          Date.now() - startedAt;
        const content = finalTrace.answer || answerSoFar || "*(no answer)*";
        setTurns((prev) => [
          ...prev,
          {
            role: "assistant",
            turn: {
              content,
              trace: { ...finalTrace, question: message },
              statusSteps: [...steps],
              durationMs,
              charts: null,
            },
          },
        ]);
        setStreaming(false);
        setLiveAnswer("");
        setLiveStatus([]);
        abortRef.current = null;
      },
    });
  };

  const clearConversation = () => {
    abortRef.current?.();
    abortRef.current = null;
    chartsFetched.current.clear();
    setTurns([]);
    setLiveAnswer("");
    setLiveStatus([]);
    setStreaming(false);
  };

  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col">
      <PageHeader
        title="Ask anything about your fitness data"
        subtitle="The assistant fetches live data from Strava and Garmin before answering — no guessing, only real numbers."
        right={
          turns.length > 0 ? (
            <button
              type="button"
              onClick={clearConversation}
              className="rounded-md border border-border bg-bg-surface px-3 py-1.5 text-xs text-text-muted hover:border-accent hover:text-text-primary"
            >
              Clear conversation
            </button>
          ) : undefined
        }
      />

      {/* Tool availability banner */}
      {noTools ? (
        <div className="mb-3 flex items-center justify-between gap-3 rounded-card border border-metric-amber/40 bg-metric-amber/10 px-4 py-3 text-sm text-metric-amber">
          <span>
            ⚠ No MCP servers reachable — start them first (`python -m
            servers.strava_mcp`, etc.), then click Refresh.
          </span>
          <button
            type="button"
            disabled={refreshing}
            onClick={refreshTools}
            className="shrink-0 rounded-md border border-border bg-bg-surface px-3 py-1.5 text-xs text-text-primary hover:border-accent disabled:opacity-50"
          >
            {refreshing ? "↻ …" : "↻ Refresh tools"}
          </button>
        </div>
      ) : health && reachable.length < 10 ? (
        <div className="mb-3 flex items-center gap-3 text-xs text-text-muted">
          <span>⚡ {reachable.length} servers reachable (some may be offline)</span>
          <button
            type="button"
            disabled={refreshing}
            onClick={refreshTools}
            className="rounded-md border border-border bg-bg-surface px-2 py-0.5 text-[11px] text-text-muted hover:border-accent hover:text-text-primary disabled:opacity-50"
          >
            {refreshing ? "↻ …" : "↻ Refresh"}
          </button>
        </div>
      ) : null}

      {/* Scrollable message list */}
      <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        {turns.length === 0 && !streaming && (
          <div className="rounded-card border border-dashed border-border bg-bg-surface/40 px-6 py-10 text-center text-sm text-text-muted">
            Ask anything about your fitness data — personal bests, sleep, heart
            rate, routes, weather.
          </div>
        )}

        {turns.map((t, i) =>
          t.role === "user" ? (
            <UserBubble key={i} content={t.content} />
          ) : (
            <AssistantBubble key={i} turn={t.turn} />
          ),
        )}

        {/* In-flight assistant turn */}
        {streaming && (
          <div className="flex gap-3">
            <div className="text-xl leading-none">🏃</div>
            <div className="min-w-0 flex-1">
              <StatusAccordion steps={liveStatus} done={false} />
              {liveAnswer ? (
                <Markdown>{liveAnswer}</Markdown>
              ) : (
                <p className="text-sm italic text-text-muted">⏳ Thinking…</p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Input pinned at the bottom */}
      <div className="mt-3 border-t border-border pt-3">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
          className="flex items-end gap-2"
        >
          <textarea
            rows={1}
            value={input}
            disabled={streaming}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={PLACEHOLDER}
            className="max-h-40 min-h-[2.5rem] flex-1 resize-y rounded-card border border-border bg-bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-accent focus:outline-none disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={streaming || !input.trim()}
            className="h-[2.5rem] shrink-0 rounded-card bg-accent px-4 text-sm font-medium text-white disabled:opacity-40"
          >
            {streaming ? "…" : "Send"}
          </button>
        </form>
      </div>
    </div>
  );
}

// ── Bubbles ───────────────────────────────────────────────────────────────────
function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] rounded-card bg-bg-surface px-4 py-2.5 text-sm text-text-primary">
        <Markdown>{content}</Markdown>
      </div>
    </div>
  );
}

function AssistantBubble({ turn }: { turn: AssistantTurn }) {
  const routeData = turn.trace.route_data as RouteData | null | undefined;
  const charts = turn.charts ?? [];
  return (
    <div className="flex gap-3">
      <div className="text-xl leading-none">🏃</div>
      <div className="min-w-0 flex-1 space-y-2">
        <StatusAccordion
          steps={turn.statusSteps}
          done
          durationMs={turn.durationMs}
          error={!!turn.trace.error}
        />
        <Markdown>{turn.content}</Markdown>

        {/* Agent trace (default closed) */}
        <AgentTrace trace={turn.trace} />

        {/* Route map */}
        {routeData?.tool && <RouteResult routeData={routeData} />}

        {/* LLM-generated charts */}
        {charts.map((fig, i) => (
          <PlotlyFigure key={i} figure={fig} />
        ))}
      </div>
    </div>
  );
}

// ── Status accordion ────────────────────────────────────────────────────────
function StatusAccordion({
  steps,
  done,
  durationMs,
  error,
}: {
  steps: string[];
  done: boolean;
  durationMs?: number;
  error?: boolean;
}) {
  // While in-flight: expanded, listing steps. On completion: collapses to a
  // one-line "✅ Done in Xs" (or "❌ Error after Xms"), expandable again.
  const [open, setOpen] = useState(!done);
  useEffect(() => {
    if (done) setOpen(false);
  }, [done]);

  if (steps.length === 0 && done) return null;

  const label = !done
    ? "⏳ Analysing request…"
    : error
      ? `❌ Error after ${durationMs ?? 0} ms`
      : `✅ Done in ${((durationMs ?? 0) / 1000).toFixed(1)}s`;

  return (
    <div className="rounded-card border border-border bg-bg-surface/40 text-xs">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-3 py-1.5 text-left text-text-muted hover:text-text-primary"
      >
        <span>{label}</span>
        <span>{open ? "▲" : "▼"}</span>
      </button>
      {open && steps.length > 0 && (
        <div className="space-y-0.5 border-t border-border px-3 py-2 text-text-muted">
          {steps.map((s, i) => (
            <div key={i}>{s}</div>
          ))}
        </div>
      )}
    </div>
  );
}
