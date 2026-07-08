import { Plus, Trash2, X } from "lucide-react";

import { useChatStore } from "../../store/chatStore";

// Left rail of the Chat panel: a "New chat" button + the user's persistent chats,
// newest first. A small pulse marks a chat whose answer is still streaming (which
// keeps running even while you're viewing another chat). Below `md` it is an
// off-canvas drawer toggled by the "Chats" button in the Chat header.
export function ChatSidebar({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  const chats = useChatStore((s) => s.chats);
  const activeId = useChatStore((s) => s.activeId);
  const live = useChatStore((s) => s.live);
  const select = useChatStore((s) => s.select);
  const newChat = useChatStore((s) => s.newChat);
  const remove = useChatStore((s) => s.remove);

  return (
    <>
      {/* Backdrop — mobile only */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-64 flex-shrink-0 transform flex-col border-r border-border bg-bg-app px-3 py-4 transition-transform duration-200 md:static md:z-auto md:w-60 md:translate-x-0 md:bg-transparent md:px-0 md:py-0 md:pr-3 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {/* Mobile drawer header */}
        <div className="mb-3 flex items-center justify-between md:hidden">
          <span className="fd-label">Chats</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close chats"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-text-muted hover:bg-bg-surface hover:text-text-primary"
          >
            <X size={18} strokeWidth={2} />
          </button>
        </div>

        <button
          type="button"
          onClick={() => {
            void newChat();
            onClose?.();
          }}
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
                  onClick={() => {
                    void select(c.id);
                    onClose?.();
                  }}
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
    </>
  );
}
