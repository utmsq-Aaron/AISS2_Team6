// Keep a section you just expanded actually visible.
//
// Every collapsible on the Dashboard (the Analysis section cards, a training row
// with its map and charts) opens content that is usually taller than the space
// left below it, so the useful part landed off-screen and had to be scrolled to
// by hand. These helpers scroll it into view instead.
//
// Two things make this less trivial than scrollIntoView():
//   • The app scrolls inside <main class="overflow-y-auto">, not the window, so
//     we resolve the nearest scrollable ancestor rather than assuming the page.
//   • Panels grow AFTER they open — streams are fetched, a MapLibre canvas and
//     Plotly charts mount — so one scroll at open time under-shoots. We follow
//     the element's size for a short settling window and then stop.
// The scroll is always clamped so the panel's own header can never be pushed out
// of view: revealing the bottom must not cost you the thing you just clicked.

import { useEffect, useRef } from "react";

const EDGE_MARGIN = 12; // breathing room against the container edges (px)
const SETTLE_MS = 1500; // how long we keep following async content growth

function scrollParent(el: HTMLElement): HTMLElement {
  let p = el.parentElement;
  while (p) {
    const overflowY = getComputedStyle(p).overflowY;
    if ((overflowY === "auto" || overflowY === "scroll") && p.scrollHeight > p.clientHeight) {
      return p;
    }
    p = p.parentElement;
  }
  return (document.scrollingElement as HTMLElement | null) ?? document.documentElement;
}

/** Scroll the nearest scroll container just far enough to show all of `el`. */
export function revealElement(el: HTMLElement | null): void {
  if (!el) return;
  const scroller = scrollParent(el);
  const isPage = scroller === document.scrollingElement || scroller === document.documentElement;
  const view = isPage
    ? { top: 0, bottom: window.innerHeight }
    : scroller.getBoundingClientRect();

  const r = el.getBoundingClientRect();
  const below = r.bottom - (view.bottom - EDGE_MARGIN); // how far it hangs off the bottom
  if (below <= 1) return; // already fully visible — don't move the page for nothing

  const room = r.top - (view.top + EDGE_MARGIN); // scrolling past this hides its header
  const delta = Math.min(below, room);
  if (delta <= 1) return;

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  scroller.scrollBy({ top: delta, behavior: reduced ? "auto" : "smooth" });
}

/**
 * Ref for a collapsible's content. Whenever `open` flips to true the element is
 * revealed, and kept revealed while its content finishes loading.
 */
export function useRevealOnExpand<T extends HTMLElement>(open: boolean) {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    if (!open) return;
    const el = ref.current;
    if (!el) return;

    // One pass after layout, then follow the element as async content grows it.
    const raf = requestAnimationFrame(() => revealElement(el));
    let first = true;
    const ro = new ResizeObserver(() => {
      // ResizeObserver reports the current size on observe(); that pass is the
      // rAF above, and running it twice would stack two smooth scrolls.
      if (first) {
        first = false;
        return;
      }
      revealElement(el);
    });
    ro.observe(el);
    const stop = window.setTimeout(() => ro.disconnect(), SETTLE_MS);

    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(stop);
      ro.disconnect();
    };
  }, [open]);

  return ref;
}
