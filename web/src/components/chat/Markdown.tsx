import type { ReactNode } from "react";

// A tiny, dependency-free Markdown renderer covering the subset the chat
// orchestrator emits: headings (#…), bullet lists (-, *, •), bold **x**, italic
// *x*, inline `code`, links [t](u), and line breaks. Anything unrecognised falls
// back to plain text, so it never throws on unexpected input.

let _key = 0;
function nextKey(): number {
  return _key++;
}

// ── Inline spans: bold, italic, code, links ──────────────────────────────────
function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  // Order matters: code first (so ** inside backticks is literal), then links,
  // then bold, then italic.
  const pattern =
    /(`[^`]+`)|(\[[^\]]+\]\([^)]+\))|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\n]+\*)|(_[^_\n]+_)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) {
      out.push(
        <code
          key={nextKey()}
          className="rounded bg-bg-surface px-1 py-0.5 font-mono text-[0.85em] text-text-primary"
        >
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith("[")) {
      const lm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok);
      if (lm) {
        out.push(
          <a
            key={nextKey()}
            href={lm[2]}
            target="_blank"
            rel="noreferrer noopener"
            className="text-accent underline"
          >
            {lm[1]}
          </a>,
        );
      } else {
        out.push(tok);
      }
    } else if (tok.startsWith("**") || tok.startsWith("__")) {
      out.push(
        <strong key={nextKey()} className="font-semibold text-text-primary">
          {tok.slice(2, -2)}
        </strong>,
      );
    } else {
      // single * or _ → italic
      out.push(
        <em key={nextKey()} className="italic">
          {tok.slice(1, -1)}
        </em>,
      );
    }
    last = pattern.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// ── Block-level: headings, lists, paragraphs ──────────────────────────────────
export function Markdown({ children }: { children: string }) {
  _key = 0; // deterministic keys per render
  const src = children ?? "";
  const lines = src.split("\n");
  const blocks: ReactNode[] = [];

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // Blank line → spacing
    if (!trimmed) {
      i += 1;
      continue;
    }

    // Heading: #, ##, ###
    const h = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (h) {
      const level = h[1].length;
      const cls =
        level <= 1
          ? "text-base font-semibold text-text-primary"
          : level === 2
            ? "text-sm font-semibold text-text-primary"
            : "text-sm font-medium text-text-primary";
      blocks.push(
        <div key={nextKey()} className={`mt-2 ${cls}`}>
          {renderInline(h[2])}
        </div>,
      );
      i += 1;
      continue;
    }

    // Bullet list: -, *, •  (consume consecutive items)
    if (/^([-*•])\s+/.test(trimmed) || /^\d+\.\s+/.test(trimmed)) {
      const ordered = /^\d+\.\s+/.test(trimmed);
      const items: ReactNode[] = [];
      while (i < lines.length) {
        const t = lines[i].trim();
        const bm = /^([-*•])\s+(.*)$/.exec(t) || /^\d+\.\s+(.*)$/.exec(t);
        if (!bm) break;
        const content = bm[bm.length - 1];
        items.push(
          <li key={nextKey()} className="ml-1">
            {renderInline(content)}
          </li>,
        );
        i += 1;
      }
      blocks.push(
        ordered ? (
          <ol key={nextKey()} className="ml-5 list-decimal space-y-0.5">
            {items}
          </ol>
        ) : (
          <ul key={nextKey()} className="ml-5 list-disc space-y-0.5">
            {items}
          </ul>
        ),
      );
      continue;
    }

    // Paragraph: gather until blank line / heading / list
    const para: string[] = [];
    while (i < lines.length) {
      const t = lines[i].trim();
      if (
        !t ||
        /^(#{1,4})\s+/.test(t) ||
        /^([-*•])\s+/.test(t) ||
        /^\d+\.\s+/.test(t)
      )
        break;
      para.push(lines[i]);
      i += 1;
    }
    blocks.push(
      <p key={nextKey()} className="leading-relaxed">
        {para.map((p, idx) => (
          <span key={idx}>
            {renderInline(p)}
            {idx < para.length - 1 && <br />}
          </span>
        ))}
      </p>,
    );
  }

  return <div className="space-y-2 text-sm text-text-primary">{blocks}</div>;
}
