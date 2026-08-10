// Copy an assistant message's markdown to the clipboard.
//
// This used to be exportPlan.ts, which also produced PDF and XLSX downloads
// (issue #30). Those were dropped along with their jspdf / jspdf-autotable /
// xlsx dependencies; copying is the part that earned its keep.

/** Remove trailing `<!--charts: ...-->` tags (Markdown.tsx / the orchestrator
 *  emit+consume them, see core/agent_trace.py) and any other HTML-comment
 *  trace-noise, then trim trailing whitespace. */
export function stripArtifacts(md: string): string {
  return (md ?? "").replace(/<!--[\s\S]*?-->/g, "").replace(/[ \t\r\n]+$/, "");
}

/** Copy the cleaned message markdown to the clipboard. */
export function copyPlan(md: string): Promise<void> {
  return navigator.clipboard.writeText(stripArtifacts(md));
}
