import { create } from "zustand";

import {
  createChat as apiCreate,
  deleteChat as apiDelete,
  getChat as apiGet,
  listChats as apiList,
  markCoachRead,
  streamChat,
} from "../lib/api";
import type { ChatMessage, ChatSummary, ChatTrace, StoredMessage } from "../lib/api";

// Persistent chat sessions. The in-flight SSE stream is driven from THIS store
// (module scope), not from the Chat component — so switching chats or navigating
// to another panel never aborts an answer in progress. Turns are persisted
// server-side per turn (see api/routers/chat.py), so history survives restarts.

export interface AssistantTurn {
  content: string;
  trace: ChatTrace;
  statusSteps: string[];
  durationMs: number;
  charts: unknown[] | null; // null until charts are generated for this turn
  // Set when this turn kicked off a long background analysis (a `background_job`
  // trace action). The finished report lands later in the Coach chat.
  backgroundJob?: { jobId?: string; topic?: string } | null;
}
export type Turn =
  | { role: "user"; content: string }
  | { role: "assistant"; turn: AssistantTurn };

interface Live {
  streaming: boolean;
  answer: string;
  status: string[];
  startedAt: number;
}

// Module-scope (non-reactive): stream aborters and the once-per-turn chart guard.
// Deliberately NOT cleared on component unmount — that's what keeps streams alive.
const aborters = new Map<string, () => void>();
const chartsFetched = new Set<string>();

/** Forget the chart-generation guard for a chat, so its charts regenerate after a
 *  turn-reload (toTurns() resets each turn's charts to null; without this the guard
 *  would keep them from ever being re-fetched). */
function clearChartsFetched(chatId: string): void {
  for (const k of Array.from(chartsFetched)) {
    if (k.startsWith(chatId + ":")) chartsFetched.delete(k);
  }
}

/** Reload-detection signature for the coach chat: advances whenever the server's
 *  coach chat changes; compared against coachSeenSig (what turns[coach] renders). */
function coachSig(updatedAt: string | undefined, count: number | undefined): string {
  return `${updatedAt ?? ""}|${count ?? 0}`;
}

/** True for the pinned system Coach chat entry. */
function isCoach(c: ChatSummary): boolean {
  return c.special === "coach" || c.pinned === true;
}

/** Pinned/coach first, then most-recently-updated first. Pure. */
function sortChats(chats: ChatSummary[]): ChatSummary[] {
  return [...chats].sort((a, b) => {
    const ca = isCoach(a) ? 1 : 0;
    const cb = isCoach(b) ? 1 : 0;
    if (ca !== cb) return cb - ca; // coach first
    return (b.updated_at ?? "").localeCompare(a.updated_at ?? ""); // newest first
  });
}

/** Pull the deep-work background-job signal out of a finished trace, if present. */
function backgroundJobFromTrace(trace?: ChatTrace): { jobId?: string; topic?: string } | null {
  const action = (trace?.actions ?? []).find((a) => a?.type === "background_job");
  if (!action) return null;
  return {
    jobId: typeof action.job_id === "string" ? action.job_id : undefined,
    topic: typeof action.topic === "string" ? action.topic : undefined,
  };
}

function statusIcon(msg: string): string {
  if (msg.includes("Phase 1")) return "🔍";
  if (msg.includes("Phase 2")) return "📊";
  if (msg.includes("Phase 3")) return "💬";
  return "⏳";
}

function traceDuration(trace?: ChatTrace): number {
  return Object.values(trace?.timing ?? {}).reduce((a, b) => a + (b || 0), 0);
}

function toTurns(messages: StoredMessage[]): Turn[] {
  return messages.map((m) =>
    m.role === "assistant"
      ? {
          role: "assistant" as const,
          turn: {
            content: m.content,
            trace: m.trace ?? {},
            statusSteps: [],
            durationMs: traceDuration(m.trace),
            charts: null,
          },
        }
      : { role: "user" as const, content: m.content },
  );
}

interface ChatState {
  chats: ChatSummary[];
  activeId: string | null;
  turns: Record<string, Turn[]>;
  loaded: Record<string, boolean>;
  live: Record<string, Live>;
  forUser: string | null;
  coachChatId: string | null;
  coachUnread: number;
  // Signature (updated_at|message_count) of what turns[coach] currently RENDERS —
  // decoupled from store.chats so a reload skipped mid-stream is re-detected later.
  coachSeenSig: string | null;

  init: (user: string) => Promise<void>;
  refreshChats: () => Promise<void>;
  select: (id: string) => Promise<void>;
  newChat: () => Promise<void>;
  remove: (id: string) => Promise<void>;
  send: (text: string) => Promise<void>;
  pollCoach: () => Promise<void>;
  markCoachSeen: () => void;
  beginCharts: (chatId: string, idx: number) => boolean;
  setCharts: (chatId: string, idx: number, figs: unknown[]) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  chats: [],
  activeId: null,
  turns: {},
  loaded: {},
  live: {},
  forUser: null,
  coachChatId: null,
  coachUnread: 0,
  coachSeenSig: null,

  init: async (user) => {
    if (get().forUser === user) return; // already initialised for this user
    // New (or first) user → reset everything and load their chats.
    aborters.clear();
    chartsFetched.clear();
    set({
      chats: [], activeId: null, turns: {}, loaded: {}, live: {}, forUser: user,
      coachChatId: null, coachUnread: 0, coachSeenSig: null,
    });
    await get().refreshChats();
    const { chats } = get();
    if (chats.length) await get().select(chats[0].id);
  },

  refreshChats: async () => {
    try {
      const chats = sortChats(await apiList());
      const coach = chats.find(isCoach);
      set({
        chats,
        coachChatId: coach?.id ?? null,
        coachUnread: coach?.unread ?? 0,
      });
    } catch {
      /* offline / 401 handled elsewhere */
    }
  },

  select: async (id) => {
    set({ activeId: id });
    if (id === get().coachChatId) get().markCoachSeen();
    if (get().loaded[id]) return;
    try {
      const chat = await apiGet(id);
      const isCoachChat = id === get().coachChatId;
      set((s) => ({
        turns: { ...s.turns, [id]: toTurns(chat.messages) },
        loaded: { ...s.loaded, [id]: true },
        // Record what turns[coach] now renders so pollCoach won't redundantly reload.
        ...(isCoachChat
          ? { coachSeenSig: coachSig(chat.updated_at, chat.messages?.length) }
          : {}),
      }));
    } catch {
      /* ignore */
    }
  },

  newChat: async () => {
    const chat = await apiCreate();
    set((s) => ({
      chats: sortChats([
        { id: chat.id, title: chat.title || "New chat", message_count: 0,
          created_at: chat.created_at, updated_at: chat.updated_at },
        ...s.chats,
      ]),
      turns: { ...s.turns, [chat.id]: [] },
      loaded: { ...s.loaded, [chat.id]: true },
      activeId: chat.id,
    }));
  },

  remove: async (id) => {
    try {
      await apiDelete(id);
    } catch {
      /* ignore */
    }
    aborters.delete(id);
    set((s) => {
      const chats = s.chats.filter((c) => c.id !== id);
      const turns = { ...s.turns };
      const loaded = { ...s.loaded };
      const live = { ...s.live };
      delete turns[id];
      delete loaded[id];
      delete live[id];
      const activeId = s.activeId === id ? (chats[0]?.id ?? null) : s.activeId;
      return { chats, turns, loaded, live, activeId };
    });
    const a = get().activeId;
    if (a && !get().loaded[a]) await get().select(a);
  },

  // Re-fetch the chat list to keep the coach badge + coach turns fresh. Cheap
  // enough to run on an interval / window focus. When the coach chat's
  // updated_at advances AND it's the active chat, reload its turns so new coach
  // messages appear live; otherwise just let coachUnread drive the badge.
  pollCoach: async () => {
    let chats: ChatSummary[];
    try {
      chats = sortChats(await apiList());
    } catch {
      return; // offline / 401 handled elsewhere
    }
    const coach = chats.find(isCoach);
    const active = coach ? get().activeId === coach.id : false;
    set({
      chats,
      coachChatId: coach?.id ?? null,
      // If the user is currently viewing the Coach chat it counts as seen — don't let
      // a fresh delivery resurrect the badge (we also clear it server-side below).
      coachUnread: active ? 0 : (coach?.unread ?? 0),
    });
    if (!coach) return;
    if (active && (coach.unread ?? 0) > 0) void markCoachRead().catch(() => {});
    // Detect advance against what turns[coach] currently RENDERS (coachSeenSig) —
    // NOT against store.chats, which we just overwrote above. That way a reload
    // skipped while streaming is still re-detected on a later (non-streaming) tick
    // instead of the message being lost forever.
    const advanced = coachSig(coach.updated_at, coach.message_count) !== get().coachSeenSig;
    // Reload turns live only when the Coach chat is active AND not mid-stream —
    // reloading during a stream would drop the optimistic (not-yet-persisted) user
    // bubble. Clear the chart guard so any charts regenerate after the reload.
    if (advanced && active && !get().live[coach.id]?.streaming) {
      try {
        const chat = await apiGet(coach.id);
        clearChartsFetched(coach.id);
        set((s) => ({
          turns: { ...s.turns, [coach.id]: toTurns(chat.messages) },
          loaded: { ...s.loaded, [coach.id]: true },
          coachSeenSig: coachSig(chat.updated_at, chat.messages?.length),
        }));
      } catch {
        /* ignore */
      }
    }
  },

  // Clear the coach unread badge locally + on the server (best-effort).
  markCoachSeen: () => {
    if (get().coachUnread === 0) return;
    set({ coachUnread: 0 });
    void markCoachRead().catch(() => {});
  },

  send: async (text) => {
    const msg = text.trim();
    if (!msg) return;

    let id = get().activeId;
    if (!id) {
      await get().newChat();
      id = get().activeId;
    }
    if (!id) return;
    const chatId = id;
    if (get().live[chatId]?.streaming) return; // already answering in this chat

    // History is loaded server-side from the stored chat (chat_id), but pass the
    // client view too for robustness.
    const prior = get().turns[chatId] ?? [];
    const history: ChatMessage[] = prior.map((t) =>
      t.role === "user"
        ? { role: "user", content: t.content }
        : { role: "assistant", content: t.turn.content },
    );

    set((s) => ({
      turns: { ...s.turns, [chatId]: [...(s.turns[chatId] ?? []), { role: "user", content: msg }] },
      live: { ...s.live, [chatId]: { streaming: true, answer: "", status: [], startedAt: Date.now() } },
    }));

    const setLive = (patch: Partial<Live>) =>
      set((s) => ({ live: { ...s.live, [chatId]: { ...s.live[chatId], ...patch } } }));

    const steps: string[] = [];
    let answerSoFar = "";
    let finalTrace: ChatTrace = {};

    const abort = streamChat(
      msg,
      history,
      {
        onStatus: (m) => {
          steps.push(`${statusIcon(m)} ${m}`);
          setLive({ status: [...steps] });
        },
        onToken: (d) => {
          answerSoFar += d;
          setLive({ answer: answerSoFar });
        },
        onReset: () => {
          answerSoFar = "";
          setLive({ answer: "" });
        },
        onTrace: (tr) => {
          finalTrace = tr;
          if (tr.answer) {
            answerSoFar = tr.answer;
            setLive({ answer: answerSoFar });
          }
        },
        onError: (m) => {
          finalTrace = { ...finalTrace, error: finalTrace.error ?? m };
          if (!answerSoFar) {
            answerSoFar = `⚠ ${m}`;
            setLive({ answer: answerSoFar });
          }
        },
        onDone: () => {
          const startedAt = get().live[chatId]?.startedAt ?? Date.now();
          const durationMs = traceDuration(finalTrace) || Date.now() - startedAt;
          const content = finalTrace.answer || answerSoFar || "*(no answer)*";
          const backgroundJob = backgroundJobFromTrace(finalTrace);
          set((s) => ({
            turns: {
              ...s.turns,
              [chatId]: [
                ...(s.turns[chatId] ?? []),
                {
                  role: "assistant",
                  turn: {
                    content,
                    trace: { ...finalTrace, question: msg },
                    statusSteps: [...steps],
                    durationMs,
                    charts: null,
                    backgroundJob,
                  },
                },
              ],
            },
            live: { ...s.live, [chatId]: { streaming: false, answer: "", status: [], startedAt } },
          }));
          aborters.delete(chatId);
          void get().refreshChats(); // pick up the new title / updated ordering
        },
      },
      chatId,
    );
    aborters.set(chatId, abort);
  },

  beginCharts: (chatId, idx) => {
    const key = `${chatId}:${idx}`;
    if (chartsFetched.has(key)) return false;
    chartsFetched.add(key);
    return true;
  },

  setCharts: (chatId, idx, figs) =>
    set((s) => {
      const arr = s.turns[chatId];
      if (!arr) return {};
      const cur = arr[idx];
      if (!cur || cur.role !== "assistant") return {};
      const next = [...arr];
      next[idx] = { role: "assistant", turn: { ...cur.turn, charts: figs } };
      return { turns: { ...s.turns, [chatId]: next } };
    }),
}));
