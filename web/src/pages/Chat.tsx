import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { AgentTrace } from "../components/chat/AgentTrace";
import { ChatSidebar } from "../components/chat/ChatSidebar";
import { Markdown } from "../components/chat/Markdown";
import { RouteResult } from "../components/chat/RouteResult";
import type { RouteData } from "../components/chat/RouteResult";
import { PageHeader } from "../components/PageHeader";
import { PlotlyFigure } from "../components/PlotlyChart";
import { generateCharts, getServerHealth } from "../lib/api";
import { useAuthStore } from "../store/authStore";
import type { AssistantTurn } from "../store/chatStore";
import { useChatStore } from "../store/chatStore";

// Chat tab — persistent, per-user conversations. The chat list lives on the left;
// streams are driven from the chat store, so switching chats or panels never
// interrupts an answer in progress, and history survives a server restart.

const PLACEHOLDER =
  "e.g. What are my personal bests?  /  How was my sleep last week?  /  Show HR peaks before sleep in the last 4 weeks";

export function Chat() {
  const user = useAuthStore((s) => s.user);

  const activeId = useChatStore((s) => s.activeId);
  const turns = useChatStore((s) => (s.activeId ? (s.turns[s.activeId] ?? []) : []));
  const live = useChatStore((s) => (s.activeId ? s.live[s.activeId] : undefined));
  const init = useChatStore((s) => s.init);
  const send = useChatStore((s) => s.send);
  const beginCharts = useChatStore((s) => s.beginCharts);
  const setCharts = useChatStore((s) => s.setCharts);

  const streaming = !!live?.streaming;
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  // Load this user's chats once (re-runs only if the user changes).
  useEffect(() => {
    if (user) void init(user);
  }, [user, init]);

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

  // ── Autoscroll while a turn is in flight ────────────────────────────────────
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns, live?.answer, live?.status, activeId]);

  // ── Lazily generate charts once per completed assistant turn (active chat) ──
  useEffect(() => {
    if (!activeId) return;
    turns.forEach((t, idx) => {
      if (t.role !== "assistant" || t.turn.charts !== null) return;
      if (!beginCharts(activeId, idx)) return;
      generateCharts(t.turn.trace)
        .then((figs) => setCharts(activeId, idx, figs))
        .catch(() => setCharts(activeId, idx, []));
    });
  }, [turns, activeId, beginCharts, setCharts]);

  const submit = () => {
    const message = input.trim();
    if (!message || streaming) return;
    setInput("");
    void send(message);
  };

  return (
    <div className="flex h-[calc(100vh-6rem)] gap-4">
      <ChatSidebar />

      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader
          title="Ask anything about your fitness data"
          subtitle="The assistant fetches live data from Strava and Garmin before answering — no guessing, only real numbers."
        />

        {/* Tool availability banner */}
        {noTools ? (
          <div className="mb-3 flex items-center justify-between gap-3 rounded-card border border-metric-amber/40 bg-metric-amber/10 px-4 py-3 text-sm text-metric-amber">
            <span>⚠ No MCP servers reachable — start them first, then click Refresh.</span>
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
              Ask anything about your fitness data — personal bests, sleep, heart rate,
              routes, weather. Your chats are saved on the left.
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
                <StatusAccordion steps={live?.status ?? []} done={false} />
                {live?.answer ? (
                  <Markdown>{live.answer}</Markdown>
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
              submit();
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
                  submit();
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
        <AgentTrace trace={turn.trace} />
        {routeData?.tool && <RouteResult routeData={routeData} />}
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
