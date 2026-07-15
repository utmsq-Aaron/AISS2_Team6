// Client-side export of a chat "training plan" (which is just the assistant
// message's markdown `content`) to PDF and Excel/XLSX. Issue #30.
//
// Two layers, deliberately separated so the parsing/building logic can be
// unit-tested headless (node) without a DOM:
//   • PURE string layer — stripArtifacts / parseMarkdownTables / planTitle.
//     No DOM, no library imports; framework-free and node-testable.
//   • DOWNLOAD layer — downloadPlanPdf / downloadPlanXlsx / copyPlan.
//     Touch jsPDF / SheetJS / the clipboard and trigger a browser download.
//
// SheetJS (`xlsx`) SECURITY NOTE: the known npm advisories for `xlsx`
// (prototype-pollution / ReDoS) are in the PARSE path — i.e. `XLSX.read`ing
// UNTRUSTED spreadsheet files. This module only ever WRITES (aoa_to_sheet /
// book_new / writeFile) from our own in-memory data, so those advisories do
// not apply here.

import { jsPDF } from "jspdf";
import autoTable from "jspdf-autotable";
import * as XLSX from "xlsx";

// ── Pure string layer (DOM-free, node-testable) ──────────────────────────────

/** Remove trailing `<!--charts: ...-->` tags (Markdown.tsx / the orchestrator
 *  emit+consume them, see core/agent_trace.py) and any other HTML-comment
 *  trace-noise, then trim trailing whitespace. */
export function stripArtifacts(md: string): string {
  return (md ?? "").replace(/<!--[\s\S]*?-->/g, "").replace(/[ \t\r\n]+$/, "");
}

/** Strip inline markdown emphasis/link/code syntax down to plain text, so it
 *  reads cleanly in a PDF cell or an Excel line (no stray `**`/backticks). */
function stripInline(text: string): string {
  return (text ?? "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*\n]+)\*/g, "$1")
    .replace(/_([^_\n]+)_/g, "$1")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "$1 ($2)")
    .trim();
}

/** Split one GFM table row `| a | b |` into trimmed cells (outer pipes dropped). */
function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

/** True if a line looks like a pipe-delimited row `| ... |`. */
function isTableRow(line: string): boolean {
  return /^\|.*\|$/.test(line.trim());
}

/** True only for a GFM separator row: every cell is `---` / `:--` / `--:` /
 *  `:-:` (dashes with optional alignment colons). This is the GUARD that keeps
 *  ordinary pipe-containing lines from being mis-read as a table. */
function isTableSeparator(line: string): boolean {
  if (!isTableRow(line)) return false;
  const cells = splitRow(line);
  return cells.length > 0 && cells.every((c) => /^:?-+:?$/.test(c));
}

/** Try to parse a GFM table starting at `lines[i]`: a header row immediately
 *  followed by a separator row, then the contiguous `| ... |` body rows.
 *  Returns the parsed table plus `end` (index just past the last body row), or
 *  null if no valid table begins at `i`. */
function tableAt(
  lines: string[],
  i: number,
): { headers: string[]; rows: string[][]; end: number } | null {
  const header = lines[i] ?? "";
  if (!isTableRow(header)) return null;
  const sep = lines[i + 1] ?? "";
  if (!isTableSeparator(sep)) return null; // separator guard
  const headers = splitRow(header);
  const rows: string[][] = [];
  let j = i + 2;
  while (j < lines.length && isTableRow(lines[j])) {
    rows.push(splitRow(lines[j]));
    j += 1;
  }
  return { headers, rows, end: j };
}

/** Detect every GFM table in the markdown. A table is a header row `| a | b |`
 *  IMMEDIATELY followed by a `| --- | --- |` separator, then its contiguous
 *  `| ... |` rows. Returns one entry per table; `[]` when there are none. */
export function parseMarkdownTables(md: string): { headers: string[]; rows: string[][] }[] {
  const lines = (md ?? "").split("\n");
  const out: { headers: string[]; rows: string[][] }[] = [];
  let i = 0;
  while (i < lines.length) {
    const t = tableAt(lines, i);
    if (t) {
      out.push({ headers: t.headers, rows: t.rows });
      i = t.end;
    } else {
      i += 1;
    }
  }
  return out;
}

/** Slugified title from the first `#`/`##`… heading, else "training-plan". */
export function planTitle(md: string): string {
  for (const line of (md ?? "").split("\n")) {
    const h = /^\s*#{1,6}\s+(.*\S)\s*$/.exec(line);
    if (h) {
      const slug = stripInline(h[1])
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
      if (slug) return slug;
    }
  }
  return "training-plan";
}

// ── Download layer (browser/library — not node-testable) ──────────────────────

/** A worksheet name Excel accepts: ≤31 chars, none of `[]:*?/\`. */
function sheetName(idx: number, total: number): string {
  if (total <= 1) return "Plan";
  return `Table ${idx + 1}`;
}

/** Render the plan markdown to a PDF and trigger a download of
 *  `<filename>.pdf`. Walks the lines (headings → larger bold font; bullet /
 *  ordered → indented; paragraphs → wrapped) and renders each detected GFM
 *  table via jspdf-autotable. */
export function downloadPlanPdf(md: string, filename: string): void {
  const base = filename || "training-plan";
  const clean = stripArtifacts(md);
  const lines = clean.split("\n");

  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const pageW = doc.internal.pageSize.getWidth();
  const pageH = doc.internal.pageSize.getHeight();
  const margin = 40;
  const maxW = pageW - margin * 2;
  let y = margin;

  const ensure = (h: number) => {
    if (y + h > pageH - margin) {
      doc.addPage();
      y = margin;
    }
  };

  // Title
  doc.setFont("helvetica", "bold");
  doc.setFontSize(16);
  const titleLines = doc.splitTextToSize(base, maxW);
  doc.text(titleLines, margin, y);
  y += titleLines.length * 20 + 8;

  let i = 0;
  while (i < lines.length) {
    // GFM table → autoTable
    const tbl = tableAt(lines, i);
    if (tbl) {
      autoTable(doc, {
        head: [tbl.headers],
        body: tbl.rows.length ? tbl.rows : [tbl.headers.map(() => "")],
        startY: y + 4,
        margin: { left: margin, right: margin },
        styles: { fontSize: 9, cellPadding: 4 },
        headStyles: { fillColor: [45, 212, 191], textColor: [17, 24, 39] },
        theme: "grid",
      });
      const last = (doc as unknown as { lastAutoTable?: { finalY?: number } }).lastAutoTable;
      y = (last && typeof last.finalY === "number" ? last.finalY : y) + 14;
      i = tbl.end;
      continue;
    }

    const line = lines[i].trim();

    // Blank → vertical gap
    if (!line) {
      y += 6;
      i += 1;
      continue;
    }

    // Heading (#…######)
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      const size = level <= 1 ? 14 : level === 2 ? 12 : 11;
      doc.setFont("helvetica", "bold");
      doc.setFontSize(size);
      const wrapped = doc.splitTextToSize(stripInline(h[2]), maxW);
      ensure(wrapped.length * (size + 3) + 6);
      doc.text(wrapped, margin, y);
      y += wrapped.length * (size + 3) + 6;
      i += 1;
      continue;
    }

    // Bullet or ordered list item → marker + indented wrapped text
    const bullet = /^([-*•])\s+(.*)$/.exec(line);
    const ordered = /^(\d+)\.\s+(.*)$/.exec(line);
    if (bullet || ordered) {
      const marker = bullet ? "•" : `${ordered![1]}.`;
      const body = stripInline(bullet ? bullet[2] : ordered![2]);
      const indent = 16;
      doc.setFont("helvetica", "normal");
      doc.setFontSize(10);
      const wrapped = doc.splitTextToSize(body, maxW - indent);
      ensure(wrapped.length * 13 + 2);
      doc.text(marker, margin, y);
      doc.text(wrapped, margin + indent, y);
      y += wrapped.length * 13 + 2;
      i += 1;
      continue;
    }

    // Paragraph
    doc.setFont("helvetica", "normal");
    doc.setFontSize(10);
    const wrapped = doc.splitTextToSize(stripInline(lines[i]), maxW);
    ensure(wrapped.length * 13 + 4);
    doc.text(wrapped, margin, y);
    y += wrapped.length * 13 + 4;
    i += 1;
  }

  doc.save(`${base}.pdf`);
}

/** Export the plan to an XLSX workbook and trigger a download of
 *  `<filename>.xlsx`. One worksheet per detected GFM table; if the plan has no
 *  table, a single-column sheet with one non-empty line per row. WRITE-ONLY
 *  (see the SheetJS security note at the top of this file). */
export function downloadPlanXlsx(md: string, filename: string): void {
  const base = filename || "training-plan";
  const clean = stripArtifacts(md);
  const tables = parseMarkdownTables(clean);
  const wb = XLSX.utils.book_new();

  if (tables.length > 0) {
    tables.forEach((t, idx) => {
      const aoa: string[][] = [t.headers, ...t.rows];
      const ws = XLSX.utils.aoa_to_sheet(aoa);
      XLSX.utils.book_append_sheet(wb, ws, sheetName(idx, tables.length));
    });
  } else {
    const rows = clean
      .split("\n")
      .map((l) => stripInline(l))
      .filter((l) => l.length > 0)
      .map((l) => [l]);
    const ws = XLSX.utils.aoa_to_sheet(rows.length ? rows : [["(empty plan)"]]);
    XLSX.utils.book_append_sheet(wb, ws, "Plan");
  }

  XLSX.writeFile(wb, `${base}.xlsx`);
}

/** Copy the cleaned plan markdown to the clipboard. */
export function copyPlan(md: string): Promise<void> {
  return navigator.clipboard.writeText(stripArtifacts(md));
}
