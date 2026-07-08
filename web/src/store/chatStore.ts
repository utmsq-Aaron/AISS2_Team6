import { create } from "zustand";

import {
  createChat as apiCreate,
  deleteChat as apiDelete,
  getChat as apiGet,
  listChats as apiList,
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

  init: (user: string) => Promise<void>;
  refreshChats: () => Promise<void>;
  select: (id: string) => Promise<void>;
  newChat: () => Promise<void>;
  remove: (id: string) => Promise<void>;
  send: (text: string) => Promise<void>;
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

  init: async (user) => {
    if (get().forUser === user) return; // already initialised for this user
    // New (or first) user → reset everything and load their chats.
    aborters.clear();
    chartsFetched.clear();
    set({ chats: [], activeId: null, turns: {}, loaded: {}, live: {}, forUser: user });
    await get().refreshChats();
    const { chats } = get();
    if (chats.length) await get().select(chats[0].id);
  },

  refreshChats: async () => {
    try {
      set({ chats: await apiList() });
    } catch {
      /* offline / 401 handled elsewhere */
    }
  },

  select: async (id) => {
    set({ activeId: id });
    if (get().loaded[id]) return;
    try {
      const chat = await apiGet(id);
      set((s) => ({
        turns: { ...s.turns, [id]: toTurns(chat.messages) },
        loaded: { ...s.loaded, [id]: true },
      }));
    } catch {
      /* ignore */
    }
  },

  newChat: async () => {
    const chat = await apiCreate();
    set((s) => ({
      chats: [
        { id: chat.id, title: chat.title || "New chat", message_count: 0,
          created_at: chat.created_at, updated_at: chat.updated_at },
        ...s.chats,
      ],
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
