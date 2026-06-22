import { Plus, Trash2 } from "lucide-react";

import { useChatStore } from "../../store/chatStore";

// Left rail of the Chat panel: a "New chat" button + the user's persistent chats,
// newest first. A small pulse marks a chat whose answer is still streaming (which
// keeps running even while you're viewing another chat).
export function ChatSidebar() {
  const chats = useChatStore((s) => s.chats);
  const activeId = useChatStore((s) => s.activeId);
  const live = useChatStore((s) => s.live);
  const select = useChatStore((s) => s.select);
  const newChat = useChatStore((s) => s.newChat);
  const remove = useChatStore((s) => s.remove);

  return (
    <aside className="flex w-60 flex-shrink-0 flex-col border-r border-border pr-3">
      <button
        type="button"
        onClick={() => void newChat()}
        className="mb-3 flex items-center justify-center gap-1.5 rounded-card border border-border bg-bg-surface px-3 py-2 text-sm font-medium text-text-primary hover:border-accent"
      >
        <Plus size={15} strokeWidth={2} /> New chat
      </button>

      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
        {chats.length === 0 && (
          <p className="px-2 py-4 text-center text-xs text-text-muted">No chats yet.</p>
        )}
        {chats.map((c) => {
          const isActive = c.id === activeId;
          const streaming = live[c.id]?.streaming;
          return (
            <div
              key={c.id}
              className={`group flex items-center gap-1.5 rounded-md px-2.5 py-2 text-sm ${
                isActive ? "bg-bg-surface text-text-primary" : "text-text-muted hover:bg-bg-surface/60"
              }`}
            >
              <button
                type="button"
                onClick={() => void select(c.id)}
                className="min-w-0 flex-1 truncate text-left"
                title={c.title}
              >
                {streaming && (
                  <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent align-middle" />
                )}
                {c.title || "New chat"}
              </button>
              <button
                type="button"
                onClick={() => void remove(c.id)}
                title="Delete chat"
                className="shrink-0 text-text-muted opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
              >
                <Trash2 size={14} strokeWidth={2} />
              </button>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
