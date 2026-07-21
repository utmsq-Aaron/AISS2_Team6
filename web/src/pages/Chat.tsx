import { useQuery } from "@tanstack/react-query";
import { Menu } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { AgentTrace } from "../components/chat/AgentTrace";
import { ChatSidebar } from "../components/chat/ChatSidebar";
import { COACH, CoachAvatar } from "../components/chat/CoachAvatar";
import { Markdown } from "../components/chat/Markdown";
import { extractPois, RouteResult } from "../components/chat/RouteResult";
import type { RouteData } from "../components/chat/RouteResult";
import FlythroughModal from "../components/FlythroughModal";
import { PageHeader } from "../components/PageHeader";
import { PlotlyFigure } from "../components/PlotlyChart";
import { generateCharts, getServerHealth, refreshChatTools } from "../lib/api";
import type { FlythroughAction } from "../lib/api";
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
  const select = useChatStore((s) => s.select);
  const coachChatId = useChatStore((s) => s.coachChatId);
  const beginCharts = useChatStore((s) => s.beginCharts);
  const setCharts = useChatStore((s) => s.setCharts);

  const isCoachActive = !!coachChatId && activeId === coachChatId;
  const streaming = !!live?.streaming;
  const [input, setInput] = useState("");
  const [chatListOpen, setChatListOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Load this user's chats once (re-runs only if the user changes).
  useEffect(() => {
    if (user) void init(user);
  }, [user, init]);

  // ── Deep-linked actions (e.g. Coach-tab "Plan route" / "To calendar") ───────
  // Another page navigates here with { state: { autoSend } }; we send that
  // message once and clear the state so a reload/back never re-sends it.
  const location = useLocation();
  const navigate = useNavigate();
  const autoSentRef = useRef(false);          // StrictMode double-effect guard
  useEffect(() => {
    const msg = (location.state as { autoSend?: string } | null)?.autoSend?.trim();
    if (!msg || !user || autoSentRef.current) return;
    autoSentRef.current = true;
    navigate(location.pathname, { replace: true, state: null });
    void (async () => {
      await useChatStore.getState().init(user); // idempotent — wait for chats
      await useChatStore.getState().send(msg);
    })();
  }, [location.state, location.pathname, navigate, user]);

  // ── Tool availability ──────────────────────────────────────────────────────
  const { data: health, refetch: refetchHealth } = useQuery({
    queryKey: ["health-servers"],
    queryFn: getServerHealth,
    staleTime: 30_000,
  });

  // Faster coach poll while the Chat page is mounted — new coach messages (and
  // finished deep-work reports) show up live in the active coach chat.
  useQuery({
    queryKey: ["coach-poll-chat"],
    queryFn: async () => {
      await useChatStore.getState().pollCoach();
      return Date.now();
    },
    refetchInterval: 20_000,
    refetchOnWindowFocus: true,
    staleTime: 0,
  });
  const [refreshing, setRefreshing] = useState(false);
  const reachable = (health?.servers ?? []).filter((s) => s.server_up);
  const noTools = health != null && reachable.length === 0;

  const refreshTools = async () => {
    setRefreshing(true);
    try {
      await refreshChatTools().catch(() => undefined);
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
      <ChatSidebar open={chatListOpen} onClose={() => setChatListOpen(false)} />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile: open the chat-list drawer */}
        <button
          type="button"
          onClick={() => setChatListOpen(true)}
          className="mb-2 flex items-center gap-1.5 self-start rounded-card border border-border bg-bg-surface px-3 py-1.5 text-xs text-text-muted hover:border-accent md:hidden"
        >
          <Menu size={14} strokeWidth={2} /> Chats
        </button>

        <PageHeader
          title="Ask anything about your fitness data"
          subtitle="Live answers from your Strava and Garmin data."
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
            <span>⚡ {reachable.length} services online</span>
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
              <AssistantBubble
                key={i}
                turn={t.turn}
                coach={isCoachActive}
                onViewCoach={coachChatId ? () => void select(coachChatId) : undefined}
              />
            ),
          )}

          {/* In-flight assistant turn */}
          {streaming && (
            <div className="flex gap-3">
              {isCoachActive ? <CoachAvatar /> : <div className="text-xl leading-none">🏃</div>}
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
              aria-label="Chat message"
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

function AssistantBubble({
  turn,
  coach = false,
  onViewCoach,
}: {
  turn: AssistantTurn;
  coach?: boolean;
  onViewCoach?: () => void;
}) {
  const routeData = turn.trace.route_data as RouteData | null | undefined;
  const charts = turn.charts ?? [];
  // First "flythrough" action (mirrors the Streamlit renderer). Rendered as a
  // card the user clicks to open the modal — NOT auto-opened, since the action is
  // persisted and would re-trigger on every chat reload.
  const ft = (turn.trace.actions ?? []).find((a) => a?.type === "flythrough") as
    | FlythroughAction
    | undefined;
  const ftActivityId = ft ? Number(ft.activity_id) : NaN;
  return (
    <div className="flex gap-3">
      {coach ? <CoachAvatar /> : <div className="text-xl leading-none">🏃</div>}
      <div className="min-w-0 flex-1 space-y-2">
        <StatusAccordion
          steps={turn.statusSteps}
          done
          durationMs={turn.durationMs}
          error={!!turn.trace.error}
        />
        <Markdown>{turn.content}</Markdown>
        {turn.backgroundJob && <DeepWorkCard job={turn.backgroundJob} onViewCoach={onViewCoach} />}
        {ft && Number.isFinite(ftActivityId) && (
          <FlythroughCard action={ft} activityId={ftActivityId} />
        )}
        <AgentTrace trace={turn.trace} />
        {routeData?.tool && (
          <RouteResult
            routeData={routeData}
            pois={extractPois(turn.trace as Record<string, unknown>)}
            question={(turn.trace as Record<string, unknown>).user_input as string | undefined}
            answer={turn.content}
          />
        )}
        {charts.map((fig, i) => (
          <PlotlyFigure key={i} figure={fig} />
        ))}
      </div>
    </div>
  );
}

// Deep-work "accepted" card — the turn kicked off a long background analysis; the
// full report lands later in the pinned Coach chat.
function DeepWorkCard({
  job,
  onViewCoach,
}: {
  job: NonNullable<AssistantTurn["backgroundJob"]>;
  onViewCoach?: () => void;
}) {
  return (
    <div className="fd-card space-y-2 border-accent/40 bg-accent/5 px-4 py-3 text-sm">
      <p className="text-text-primary">
        {COACH.emoji} On it — I'll dig into
        {job.topic ? ` "${job.topic}"` : " this"} and post the full report in your Coach chat.
      </p>
      {onViewCoach && (
        <button
          type="button"
          onClick={onViewCoach}
          className="rounded-md border border-accent/50 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20"
        >
          View in Coach chat
        </button>
      )}
    </div>
  );
}

// The flythrough API accepts only {satellite_3d, dark} (api/routers/flythrough.py).
// The MCP tool may emit "dark_3d", "satellite_flat", etc. — collapse to the two.
function mapMode(mode?: string): string {
  return mode === "dark_3d" ? "dark" : "satellite_3d";
}

// 3D-flythrough card — the turn produced a `flythrough` trace action. Clicking
// "Watch flythrough" mounts the FlythroughModal seeded from the action. Not
// auto-opened (the action is persisted; auto-open would re-trigger on reload).
function FlythroughCard({
  action,
  activityId,
}: {
  action: FlythroughAction;
  activityId: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="fd-card space-y-2 border-accent/40 bg-accent/5 px-4 py-3 text-sm">
      <p className="text-text-primary">
        🎥 3D Flythrough
        {action.activity_name ? ` — ${action.activity_name}` : ""}
      </p>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-md border border-accent/50 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20"
      >
        Watch flythrough
      </button>
      {open && (
        <FlythroughModal
          activityId={activityId}
          activityName={action.activity_name}
          initialMode={mapMode(action.mode)}
          initialOrientation={action.orientation}
          initialResolution={action.resolution}
          duration={action.duration_sec}
          onClose={() => setOpen(false)}
        />
      )}
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
        aria-expanded={open}
        className="flex w-full items-center justify-between px-3 py-1.5 text-left text-text-muted hover:text-text-primary"
      >
        <span>{label}</span>
        <span aria-hidden="true">{open ? "▲" : "▼"}</span>
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
