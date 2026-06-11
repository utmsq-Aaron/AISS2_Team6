"""Generate chart PNG images from MCP tool results for Telegram delivery.

Streamlit-free — uses only matplotlib (headless Agg backend) so charts can be
sent as Telegram photos without a browser. Called by telegram_bridge.py.

Interface:
    can_render(tool_name) -> bool
    render_chart_png(tool_name, result_json) -> bytes | None
"""

from __future__ import annotations

import io
import json
from collections import Counter
from typing import Callable, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ── Dark theme consistent with the Streamlit dashboard ───────────────────────
_BG  = "#0f1117"
_AX  = "#1c2031"
_FG  = "#e0e3ea"
_MUT = "#6b7280"
_GRD = "#2d3348"

plt.rcParams.update({
    "figure.facecolor":  _BG,
    "axes.facecolor":    _AX,
    "axes.labelcolor":   "#aab0bc",
    "axes.edgecolor":    _GRD,
    "text.color":        _FG,
    "xtick.color":       _MUT,
    "ytick.color":       _MUT,
    "grid.color":        _GRD,
    "grid.linestyle":    "--",
    "grid.alpha":        0.5,
    "figure.dpi":        150,
    "font.size":         9,
    "axes.titlesize":    11,
    "axes.titlepad":     8,
    "legend.fontsize":   9,
    "legend.framealpha": 0.3,
})

_ORANGE = "#FC4C02"
_GREEN  = "#22c55e"
_BLUE   = "#3b82f6"
_RED    = "#ef4444"
_AMBER  = "#f59e0b"
_PURPLE = "#a855f7"
_CYAN   = "#06b6d4"
_ROSE   = "#fb7185"

_REGISTRY: Dict[str, Callable] = {}


def _register(*names: str):
    def decorator(fn: Callable) -> Callable:
        for n in names:
            _REGISTRY[n] = fn
        return fn
    return decorator


def can_render(tool_name: str) -> bool:
    bare = tool_name.split("__", 1)[-1] if "__" in tool_name else tool_name
    return bare in _REGISTRY


def render_chart_png(tool_name: str, result: str, user_query: str = "") -> Optional[bytes]:
    """Render a tool result as PNG bytes, or None if unsupported / on error."""
    bare = tool_name.split("__", 1)[-1] if "__" in tool_name else tool_name
    fn = _REGISTRY.get(bare)
    if not fn:
        return None
    try:
        data = json.loads(result) if isinstance(result, str) else result
        if not data or (isinstance(data, dict) and data.get("error")):
            return None
        # Pass user_query to renderers that support it (activities LLM selection)
        try:
            return fn(data, user_query=user_query)
        except TypeError:
            return fn(data)
    except Exception:
        return None


def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def _xticks(ax, labels: List[str], step: Optional[int] = None) -> None:
    n = len(labels)
    step = step or max(1, n // 8)
    idxs = list(range(0, n, step))
    ax.set_xticks(idxs)
    ax.set_xticklabels([labels[i] for i in idxs], rotation=30, ha="right")


# ── Sleep ──────────────────────────────────────────────────────────────────────

@_register("get_garmin_sleep")
def _sleep(data: dict) -> Optional[bytes]:
    date  = data.get("date", "")
    total = data.get("total_sleep_h") or 0
    deep  = data.get("deep_h") or 0
    rem   = data.get("rem_h") or 0
    light = data.get("light_h") or 0
    awake = data.get("awake_h") or 0
    score = data.get("sleep_score")
    if total <= 0:
        return None

    fig, ax = plt.subplots(figsize=(8, 3.5))
    stages = [("Deep", deep, _BLUE), ("REM", rem, _PURPLE),
               ("Light", light, _GREEN), ("Awake", awake, _AMBER)]
    left = 0.0
    for label, val, color in stages:
        if val and val > 0:
            ax.barh(0, val, left=left, color=color,
                    label=f"{label} {val:.1f}h", height=0.5)
            if val > 0.3:
                ax.text(left + val / 2, 0, f"{val:.1f}h",
                        ha="center", va="center", fontsize=8,
                        color="white", fontweight="bold")
            left += val

    ax.set_xlim(0, max(total * 1.1, 9))
    ax.set_yticks([])
    ax.set_xlabel("Hours")
    title = f"Sleep — {date}   Total: {total:.1f} h"
    if score:
        title += f"   Score: {int(score)}"
    ax.set_title(title)
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Body Battery ───────────────────────────────────────────────────────────────

@_register("get_garmin_body_battery")
def _body_battery(data: dict) -> Optional[bytes]:
    days = [d for d in (data.get("days") or []) if d.get("highest") is not None]
    if not days:
        return None
    dates = [d.get("date") or "" for d in days]
    highs = [d.get("highest") or 0 for d in days]
    lows  = [d.get("lowest")  or 0 for d in days]

    fig, ax = plt.subplots(figsize=(10, 4))
    x = list(range(len(days)))
    ax.fill_between(x, lows, highs, alpha=0.2, color=_GREEN)
    ax.plot(x, highs, color=_GREEN, linewidth=2, marker="o", markersize=4, label="High")
    ax.plot(x, lows,  color=_AMBER, linewidth=1.5, linestyle="--",
            marker="o", markersize=3, label="Low")
    labels = [d[-5:] if len(d) >= 5 else d for d in dates]
    _xticks(ax, labels)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Battery %")
    start = data.get("start_date", ""); end = data.get("end_date", "")
    ax.set_title(f"Body Battery — {start} to {end}" if start else "Body Battery")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Heart-rate intraday ────────────────────────────────────────────────────────

@_register("get_garmin_heart_rate_timeline")
def _hr_timeline(data: dict) -> Optional[bytes]:
    timeline = data.get("timeline") or []
    if not timeline:
        return None
    times = [t["time"] for t in timeline]
    hrs   = [t["hr"]   for t in timeline]
    rhr   = data.get("resting_hr")

    step = max(1, len(times) // 300)
    ts   = times[::step]
    hs   = hrs[::step]
    xs   = list(range(len(ts)))

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.fill_between(xs, hs, alpha=0.2, color=_RED)
    ax.plot(xs, hs, color=_RED, linewidth=1.2)
    if rhr:
        ax.axhline(rhr, color=_MUT, linestyle=":", linewidth=1,
                   label=f"Resting {rhr} bpm")
        ax.legend()
    _xticks(ax, ts)
    ax.set_ylabel("bpm")
    ax.set_title(f"Heart Rate — {data.get('date', '')}")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Activity list ──────────────────────────────────────────────────────────────

@_register("get_activities", "get_garmin_activities")
def _activities(data: dict, user_query: str = "") -> Optional[bytes]:
    acts = data.get("activities") or []
    if not acts:
        return None
    profile = _profile_activities(acts)
    spec    = _llm_chart_spec(profile, user_query=user_query)
    return _render_activities(acts, profile, spec)


# ── Activity data profiler ─────────────────────────────────────────────────────

def _act_date(a: dict) -> str:
    """Return YYYY-MM-DD for an activity, handling both Strava and Garmin date fields."""
    return (a.get("start_date") or a.get("date") or "")[:10]


def _profile_activities(acts: list) -> dict:
    """Compute statistics about an activity list so the LLM can make an informed choice."""
    import datetime as dt

    sport_counts: Counter = Counter(
        a.get("sport_type") or a.get("type") or "Other" for a in acts
    )
    dates = sorted(d for a in acts if len(d := _act_date(a)) == 10)
    span_days = 0
    if len(dates) >= 2:
        try:
            span_days = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days
        except ValueError:
            pass

    n = len(acts)
    return {
        "n_activities": n,
        "sports":        dict(sport_counts),
        "n_sports":      len(sport_counts),
        "dominant_sport": sport_counts.most_common(1)[0][0] if sport_counts else "Unknown",
        "date_from":     dates[0]  if dates else None,
        "date_to":       dates[-1] if dates else None,
        "span_days":     span_days,
        "pct_with_hr":   round(sum(1 for a in acts if a.get("avg_heart_rate") or a.get("avg_hr")) / n * 100),
        "pct_with_pace": round(sum(1 for a in acts if a.get("pace_min_per_km")) / n * 100),
        "pct_with_elev": round(sum(1 for a in acts if a.get("elevation_gain_m")) / n * 100),
        "avg_dist_km":   round(sum(a.get("distance_km", 0) for a in acts) / n, 1),
        "total_dist_km": round(sum(a.get("distance_km", 0) for a in acts), 1),
    }


# ── LLM chart spec ────────────────────────────────────────────────────────────

def _llm_chart_spec(profile: dict, user_query: str = "") -> dict:
    """Ask the LLM which chart type and config best represents this activity set.

    Falls back to smart heuristics if the LLM call fails or times out.
    Available chart types:
      weekly_volume    — weekly distance bars (+ optional count row), stacked by sport if multi
      pace_trend       — pace over time with regression line (running / hiking)
      hr_trend         — avg HR per activity over time
      sport_overview   — pie (count) + bar (distance) side-by-side, good for multi-sport
      activity_scatter — each activity as a dot: date × distance, sized by duration
      elevation_trend  — weekly elevation gain, good for hike-heavy datasets
    """
    import json as _json

    user_context = f"\nUser's question: \"{user_query}\"" if user_query else ""
    prompt = f"""\
You are a data visualisation expert for a fitness-tracking app.
Choose the SINGLE most informative chart for the following activity dataset.
Data profile: {_json.dumps(profile)}{user_context}

Available chart types:
- "weekly_volume":    weekly distance bars, stacked by sport if n_sports > 1
- "pace_trend":       pace (min/km) per activity over time, with trend line
- "hr_trend":         avg heart-rate per activity over time, with trend line
- "sport_overview":   pie (count by sport) + bar (distance by sport), best for n_sports > 2
- "activity_scatter": each activity as a point: x=date, y=distance_km, size=moving_time_hours
- "elevation_trend":  weekly elevation gain bars

Respond with JSON only — no markdown, no extra text:
{{
  "chart": "<type from the list above>",
  "title": "<concise, data-specific title>",
  "subtitle": "<one-line insight about the data, or null>",
  "color_by_sport": <true if stacking/colouring by sport adds information, else false>
}}

Rules (apply in order, stop at first match):
1. User asked about pace/speed → pace_trend (if pct_with_pace > 30)
2. User asked about heart rate → hr_trend (if pct_with_hr > 30)
3. User asked about elevation/climbing → elevation_trend
4. n_sports > 2  →  sport_overview
5. span_days > 90 and pct_with_pace > 40  →  pace_trend
6. span_days > 90 and pct_with_elev > 60  →  elevation_trend
7. span_days > 30  →  weekly_volume
8. pct_with_hr > 50  →  hr_trend
9. default  →  activity_scatter"""

    try:
        from core.llm import get_llm_client
        client, model = get_llm_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
            timeout=12,
        )
        raw = resp.choices[0].message.content or ""
        # Strip possible markdown fences
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return _json.loads(raw)
    except Exception:
        pass

    # ── Heuristic fallback (no LLM) ───────────────────────────────────────────
    n_sp  = profile.get("n_sports", 1)
    span  = profile.get("span_days", 0)
    sport = profile.get("dominant_sport", "")
    if n_sp > 2:
        return {"chart": "sport_overview",   "title": "Activity Overview",
                "subtitle": None, "color_by_sport": True}
    if span > 90 and profile.get("pct_with_pace", 0) > 40:
        return {"chart": "pace_trend",        "title": f"{sport} Pace Trend",
                "subtitle": None, "color_by_sport": False}
    if span > 90 and profile.get("pct_with_elev", 0) > 60:
        return {"chart": "elevation_trend",   "title": "Elevation Gain per Week",
                "subtitle": None, "color_by_sport": n_sp > 1}
    if span > 30:
        return {"chart": "weekly_volume",     "title": "Weekly Training Volume",
                "subtitle": None, "color_by_sport": n_sp > 1}
    if profile.get("pct_with_hr", 0) > 50:
        return {"chart": "hr_trend",          "title": "Heart Rate Over Time",
                "subtitle": None, "color_by_sport": False}
    return     {"chart": "activity_scatter",  "title": "Recent Activities",
                "subtitle": None, "color_by_sport": n_sp > 1}


# ── Chart renderers ────────────────────────────────────────────────────────────

def _render_activities(acts: list, profile: dict, spec: dict) -> Optional[bytes]:
    chart = spec.get("chart", "weekly_volume")
    title = spec.get("title", "Activities")
    sub   = spec.get("subtitle") or ""
    by_sp = spec.get("color_by_sport", False)

    try:
        if chart == "sport_overview":
            return _chart_sport_overview(acts, title)
        if chart == "weekly_volume":
            return _chart_weekly_volume(acts, title, sub, by_sp)
        if chart == "pace_trend":
            return _chart_pace_trend(acts, title, sub)
        if chart == "hr_trend":
            return _chart_hr_trend(acts, title, sub)
        if chart == "elevation_trend":
            return _chart_elevation_trend(acts, title, sub, by_sp)
        if chart == "activity_scatter":
            return _chart_activity_scatter(acts, title, sub, by_sp)
    except Exception:
        pass
    # ultimate fallback
    return _chart_weekly_volume(acts, title, sub, by_sp)


def _sport_color(sport: str) -> str:
    _MAP = {"Run": _ORANGE, "TrailRun": _ORANGE, "VirtualRun": _ORANGE,
            "Ride": _BLUE, "MountainBikeRide": _BLUE, "GravelRide": _BLUE,
            "Hike": _GREEN, "Walk": _GREEN,
            "Swim": _CYAN, "NordicSki": _PURPLE, "AlpineSki": _PURPLE}
    return _MAP.get(sport, _AMBER)


def _dates_and_dists(acts: list):
    import datetime as dt
    dates, dists, hrs_list, paces, elev_list, hr_list = [], [], [], [], [], []
    for a in sorted(acts, key=_act_date):
        raw = _act_date(a)
        try:
            d = dt.date.fromisoformat(raw)
        except ValueError:
            continue
        dates.append(d)
        dists.append(a.get("distance_km") or 0)
        hrs_list.append(a.get("moving_time_hours") or 0)
        # Garmin returns avg_hr; Strava returns avg_heart_rate
        hr = a.get("avg_heart_rate") or a.get("avg_hr")
        hr_list.append(hr)
        paces.append(a.get("pace_min_per_km"))
        elev_list.append(a.get("elevation_gain_m") or 0)
    return dates, dists, hrs_list, paces, elev_list, hr_list


def _weekly_buckets(acts: list, value_fn) -> tuple:
    """Aggregate acts into ISO weeks. Returns (week_labels, values_dict_by_sport)."""
    import datetime as dt
    from collections import defaultdict
    buckets: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for a in acts:
        raw = _act_date(a)
        try:
            d = dt.date.fromisoformat(raw)
            wk = d.strftime("%G-W%V")
        except ValueError:
            continue
        sport = a.get("sport_type") or a.get("type") or "Other"
        buckets[wk][sport] += value_fn(a)
    weeks = sorted(buckets.keys())[-20:]
    return weeks, {wk: dict(buckets[wk]) for wk in weeks}


def _chart_weekly_volume(acts, title, sub, by_sport) -> Optional[bytes]:
    weeks, buckets = _weekly_buckets(acts, lambda a: a.get("distance_km") or 0)
    if not weeks:
        return None
    sports = sorted({s for b in buckets.values() for s in b})
    palette = [_ORANGE, _BLUE, _GREEN, _PURPLE, _AMBER, _CYAN, _RED]
    xs = list(range(len(weeks)))

    fig, ax = plt.subplots(figsize=(10, 4))
    bottom = np.zeros(len(weeks))
    for i, sp in enumerate(sports if by_sport else ["total"]):
        if sp == "total":
            vals = np.array([sum(buckets[w].values()) for w in weeks])
            color = _ORANGE
            label = None
        else:
            vals = np.array([buckets[w].get(sp, 0) for w in weeks])
            color = palette[i % len(palette)]
            label = sp
        ax.bar(xs, vals, bottom=bottom, color=color, alpha=0.85, label=label)
        bottom += vals

    ax.set_ylabel("km")
    ax.set_title(title)
    if sub:
        ax.set_xlabel(sub, fontsize=8, color=_MUT)
    if by_sport and len(sports) > 1:
        ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    _xticks(ax, [w[5:] for w in weeks])
    fig.tight_layout()
    return _fig_to_png(fig)


def _chart_pace_trend(acts, title, sub) -> Optional[bytes]:
    dates, _, _, paces, _, _ = _dates_and_dists(acts)
    valid = [(d, p) for d, p in zip(dates, paces) if p and p < 20]
    if not valid:
        return _chart_weekly_volume(acts, title, sub, False)
    ds, ps = zip(*valid)
    xs = list(range(len(ds)))

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(xs, ps, color=_ORANGE, linewidth=1.5, marker="o", markersize=3)
    # trend
    if len(xs) >= 4:
        z = np.polyfit(xs, ps, 1)
        ax.plot(xs, np.poly1d(z)(xs), "--", color=_MUT, linewidth=1, alpha=0.8, label="Trend")
        direction = "improving ↓" if z[0] < 0 else "slower ↑"
        ax.legend([f"Pace  ({direction})"], fontsize=8)
    ax.invert_yaxis()
    ax.set_ylabel("min/km")
    ax.set_title(title)
    if sub:
        ax.set_xlabel(sub, fontsize=8, color=_MUT)
    ax.grid(alpha=0.3)
    _xticks(ax, [str(d)[:7] for d in ds])
    fig.tight_layout()
    return _fig_to_png(fig)


def _chart_hr_trend(acts, title, sub) -> Optional[bytes]:
    dates, _, _, _, _, hr_list = _dates_and_dists(acts)
    valid = [(d, h) for d, h in zip(dates, hr_list) if h]
    if not valid:
        return _chart_weekly_volume(acts, title, sub, False)
    ds, hs = zip(*valid)
    xs = list(range(len(ds)))

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(xs, hs, color=_RED, linewidth=1.5, marker="o", markersize=3)
    if len(xs) >= 4:
        z = np.polyfit(xs, hs, 1)
        ax.plot(xs, np.poly1d(z)(xs), "--", color=_MUT, linewidth=1, alpha=0.8, label="Trend")
        ax.legend(fontsize=8)
    ax.set_ylabel("bpm")
    ax.set_title(title)
    if sub:
        ax.set_xlabel(sub, fontsize=8, color=_MUT)
    ax.grid(alpha=0.3)
    _xticks(ax, [str(d)[:7] for d in ds])
    fig.tight_layout()
    return _fig_to_png(fig)


def _chart_elevation_trend(acts, title, sub, by_sport) -> Optional[bytes]:
    weeks, buckets = _weekly_buckets(acts, lambda a: a.get("elevation_gain_m") or 0)
    if not weeks:
        return None
    sports = sorted({s for b in buckets.values() for s in b})
    palette = [_GREEN, _BLUE, _ORANGE, _PURPLE, _AMBER]
    xs = list(range(len(weeks)))

    fig, ax = plt.subplots(figsize=(10, 4))
    bottom = np.zeros(len(weeks))
    for i, sp in enumerate(sports if by_sport else ["total"]):
        if sp == "total":
            vals = np.array([sum(buckets[w].values()) for w in weeks])
            color = _GREEN
            label = None
        else:
            vals = np.array([buckets[w].get(sp, 0) for w in weeks])
            color = palette[i % len(palette)]
            label = sp
        ax.bar(xs, vals, bottom=bottom, color=color, alpha=0.85, label=label)
        bottom += vals

    ax.set_ylabel("m")
    ax.set_title(title)
    if sub:
        ax.set_xlabel(sub, fontsize=8, color=_MUT)
    if by_sport and len(sports) > 1:
        ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    _xticks(ax, [w[5:] for w in weeks])
    fig.tight_layout()
    return _fig_to_png(fig)


def _chart_activity_scatter(acts, title, sub, by_sport) -> Optional[bytes]:
    dates, dists, hrs_list, paces, _, _ = _dates_and_dists(acts)
    if not dates:
        return None
    xs = list(range(len(dates)))

    fig, ax = plt.subplots(figsize=(10, 4))
    # size ~ duration, capped so tiny activities are still visible
    sizes = [max(30, min(300, h * 120)) for h in hrs_list]
    if by_sport:
        sport_list = [a.get("sport_type") or a.get("type") or "Other"
                      for a in sorted(acts, key=_act_date)]
        seen: Dict[str, bool] = {}
        for xi, di, si, sp in zip(xs, dists, sizes, sport_list):
            c = _sport_color(sp)
            ax.scatter(xi, di, s=si, color=c, alpha=0.7,
                       label=sp if sp not in seen else "", zorder=3)
            seen[sp] = True
        # dedupe legend
        handles, labels = ax.get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        if len(by_label) > 1:
            ax.legend(by_label.values(), by_label.keys(), fontsize=8)
    else:
        ax.scatter(xs, dists, s=sizes, color=_ORANGE, alpha=0.7, zorder=3)

    ax.set_ylabel("km")
    ax.set_title(title)
    if sub:
        ax.set_xlabel(sub, fontsize=8, color=_MUT)
    ax.grid(alpha=0.3)
    _xticks(ax, [str(d)[:7] for d in dates])
    fig.tight_layout()
    return _fig_to_png(fig)


def _chart_sport_overview(acts, title) -> Optional[bytes]:
    sport_counts: Counter = Counter(
        a.get("sport_type") or a.get("type") or "Other" for a in acts
    )
    sport_dist: Dict[str, float] = {}
    for a in acts:
        t = a.get("sport_type") or a.get("type") or "Other"
        d = a.get("distance_km") or (a.get("distance", 0) / 1000)
        sport_dist[t] = sport_dist.get(t, 0) + (d or 0)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    sports = list(sport_counts.keys())[:8]
    counts = [sport_counts[s] for s in sports]
    palette = [_ORANGE, _BLUE, _GREEN, _PURPLE, _AMBER, _RED, _CYAN, "#84cc16"]
    ax1.pie(counts, labels=sports, colors=palette[:len(sports)],
            autopct="%1.0f%%", startangle=90,
            textprops={"color": _FG, "fontsize": 9},
            pctdistance=0.8,
            wedgeprops={"linewidth": 1, "edgecolor": _BG})
    ax1.set_title(f"Activities by Type  ({len(acts)} total)")

    sd_sorted = sorted(sport_dist.items(), key=lambda x: -x[1])[:8]
    if sd_sorted:
        snames = [s[0] for s in sd_sorted][::-1]
        sdists = [s[1] for s in sd_sorted][::-1]
        colors = [_sport_color(s) for s in snames]
        bars = ax2.barh(snames, sdists, color=colors)
        mx = max(sdists) if sdists else 1
        for bar, val in zip(bars, sdists):
            ax2.text(val + mx * 0.01, bar.get_y() + bar.get_height() / 2,
                     f"{val:.0f} km", va="center", fontsize=8, color=_FG)
        ax2.set_xlabel("km")
        ax2.set_title("Distance by Sport")
        ax2.grid(axis="x", alpha=0.3)

    fig.suptitle(title, fontsize=11, fontweight="bold")
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Activity GPS stream (route + HR heatmap) ──────────────────────────────────

@_register("get_activity_streams")
def _activity_streams(data: dict) -> Optional[bytes]:
    points = data.get("points") or []
    if len(points) < 2:
        return None

    act    = data.get("activity") or {}
    has_hr = any(p.get("hr") for p in points)

    # ── Attempt staticmap-based colored route ─────────────────────────────────
    try:
        from staticmap import CircleMarker, Line, StaticMap

        smap = StaticMap(
            900, 650, padding_x=40, padding_y=40,
            url_template="https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png",
            headers={"User-Agent": "FitDash/1.0"},
        )
        step   = max(1, len(points) // 400)
        pts_dn = points[::step]

        if has_hr:
            hrs       = [p.get("hr") or 0 for p in pts_dn]
            valid_hrs = [h for h in hrs if h > 0]
            hr_min    = min(valid_hrs) if valid_hrs else 100
            hr_max    = max(valid_hrs) if valid_hrs else 200

            def _color(hr: int) -> str:
                t = max(0.0, min(1.0, (hr - hr_min) / max(1, hr_max - hr_min)))
                r = int(34  + t * (239 - 34))
                g = int(197 - t * (197 - 68))
                b = int(94  - t * (94  - 68))
                return f"#{r:02x}{g:02x}{b:02x}"

            for i in range(len(pts_dn) - 1):
                p0, p1 = pts_dn[i], pts_dn[i + 1]
                if p0.get("lon") and p0.get("lat") and p1.get("lon") and p1.get("lat"):
                    smap.add_line(
                        Line([(p0["lon"], p0["lat"]), (p1["lon"], p1["lat"])],
                             _color(hrs[i]), 4)
                    )
        else:
            coords = [(p["lon"], p["lat"]) for p in pts_dn
                      if p.get("lon") and p.get("lat")]
            if coords:
                smap.add_line(Line(coords, _ORANGE, 4))

        first = next((p for p in points if p.get("lon") and p.get("lat")), None)
        last  = next((p for p in reversed(points) if p.get("lon") and p.get("lat")), None)
        if first:
            smap.add_marker(CircleMarker((first["lon"], first["lat"]), _GREEN, 14))
        if last:
            smap.add_marker(CircleMarker((last["lon"],  last["lat"]),  _RED,   14))

        image = smap.render()

        # Overlay HR legend using PIL
        if has_hr:
            try:
                from PIL import Image, ImageDraw
                img  = Image.open(io.BytesIO(_pil_png(image))).convert("RGBA")
                draw = ImageDraw.Draw(img)
                lx, ly = 20, 20
                for i in range(20):
                    t = i / 19
                    r = int(34  + t * (239 - 34))
                    g = int(197 - t * (197 - 68))
                    b = int(94  - t * (94  - 68))
                    draw.rectangle([lx + i * 10, ly, lx + i * 10 + 9, ly + 14],
                                   fill=(r, g, b, 220))
                draw.text((lx,       ly + 18), f"{hr_min} bpm", fill=(200, 200, 200, 255))
                draw.text((lx + 150, ly + 18), f"{hr_max} bpm", fill=(200, 200, 200, 255))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="PNG")
                return buf.getvalue()
            except Exception:
                pass

        return _pil_png(image)

    except Exception:
        pass

    # ── Fallback: matplotlib HR / elevation profile ───────────────────────────
    return _streams_fallback(data, has_hr)


def _pil_png(pil_image) -> bytes:
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return buf.getvalue()


def _streams_fallback(data: dict, has_hr: bool) -> Optional[bytes]:
    points = data.get("points") or []
    has_ele = any(p.get("ele") for p in points)
    n_rows  = sum([has_hr, has_ele]) or 1

    fig, raw_axes = plt.subplots(n_rows, 1, figsize=(10, n_rows * 2.5), squeeze=False)
    axes = [ax for row in raw_axes for ax in row]
    dists = [p.get("dist_m", 0) / 1000 for p in points]
    ax_i  = 0

    if has_hr:
        hrs = [p.get("hr") for p in points]
        axes[ax_i].plot(dists, hrs, color=_RED, linewidth=1.2)
        axes[ax_i].fill_between(dists, hrs, alpha=0.2, color=_RED)
        axes[ax_i].set_ylabel("HR (bpm)")
        axes[ax_i].set_title("Heart Rate")
        axes[ax_i].grid(alpha=0.3)
        ax_i += 1

    if has_ele:
        eles = [p.get("ele") or 0 for p in points]
        axes[ax_i].fill_between(dists, eles, alpha=0.5, color=_BLUE)
        axes[ax_i].plot(dists, eles, color=_BLUE, linewidth=1.2)
        axes[ax_i].set_ylabel("Elevation (m)")
        axes[ax_i].set_title("Elevation Profile")
        axes[ax_i].grid(alpha=0.3)

    axes[-1].set_xlabel("Distance (km)")
    act = data.get("activity") or {}
    fig.suptitle(act.get("name", "Activity"), fontsize=12, fontweight="bold")
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Performance Trends ────────────────────────────────────────────────────────

@_register("analyze_performance_trends")
def _perf_trends(data: dict) -> Optional[bytes]:
    series = data.get("series") or []
    if not series:
        return None

    sport  = data.get("sport_type", "")
    dates  = [s.get("date", "") for s in series]
    paces  = [s.get("pace_min_per_km") for s in series]
    hrs    = [s.get("avg_hr") for s in series]

    has_pace = any(p for p in paces if p)
    has_hr   = any(h for h in hrs   if h)
    if not has_pace and not has_hr:
        return None

    n_rows = sum([has_pace, has_hr])
    fig, raw_axes = plt.subplots(n_rows, 1, figsize=(10, n_rows * 3), squeeze=False)
    axes = [ax for row in raw_axes for ax in row]
    xs   = list(range(len(dates)))
    ax_i = 0

    if has_pace:
        ax = axes[ax_i]
        pace_vals = [p if p else None for p in paces]
        ax.plot(xs, pace_vals, color=_ORANGE, linewidth=2,
                marker="o", markersize=4, label="Pace")
        ax.invert_yaxis()
        # trend line
        valid = [(i, p) for i, p in enumerate(pace_vals) if p]
        if len(valid) >= 4:
            xi_arr = np.array([v[0] for v in valid], dtype=float)
            yi_arr = np.array([v[1] for v in valid], dtype=float)
            z = np.polyfit(xi_arr, yi_arr, 1)
            trend_y = np.poly1d(z)(np.array(xs, dtype=float))
            ax.plot(xs, trend_y, "--", color=_MUT, linewidth=1, alpha=0.7, label="Trend")
        ax.set_ylabel("min/km")
        ax.set_title(f"{sport} Pace Trend (lower = faster)")
        ax.legend()
        ax.grid(alpha=0.3)
        _xticks(ax, [d[:7] for d in dates])
        ax_i += 1

    if has_hr:
        ax = axes[ax_i]
        hr_vals = [h if h else None for h in hrs]
        ax.plot(xs, hr_vals, color=_RED, linewidth=2,
                marker="o", markersize=4, label="Avg HR")
        valid = [(i, h) for i, h in enumerate(hr_vals) if h]
        if len(valid) >= 4:
            xi_arr = np.array([v[0] for v in valid], dtype=float)
            yi_arr = np.array([v[1] for v in valid], dtype=float)
            z = np.polyfit(xi_arr, yi_arr, 1)
            trend_y = np.poly1d(z)(np.array(xs, dtype=float))
            ax.plot(xs, trend_y, "--", color=_MUT, linewidth=1, alpha=0.7, label="Trend")
        ax.set_ylabel("bpm")
        ax.set_title(f"{sport} Heart Rate Trend")
        ax.legend()
        ax.grid(alpha=0.3)
        _xticks(ax, [d[:7] for d in dates])

    fig.tight_layout()
    return _fig_to_png(fig)


# ── Training Load (ATL / CTL / TSB) ──────────────────────────────────────────

@_register("get_training_load")
def _training_load(data: dict) -> Optional[bytes]:
    weeks   = (data.get("weeks") or [])[-16:]
    current = data.get("current") or {}
    if not weeks:
        return None

    week_labels = [w["week_start"] for w in weeks]
    atls = [w["avg_atl"] for w in weeks]
    ctls = [w["avg_ctl"] for w in weeks]
    tsbs = [w["avg_tsb"] for w in weeks]
    xs   = list(range(len(weeks)))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7),
                                    gridspec_kw={"height_ratios": [3, 2]})

    ax1.plot(xs, atls, color=_RED,  linewidth=2, label="ATL (fatigue)")
    ax1.plot(xs, ctls, color=_BLUE, linewidth=2, label="CTL (fitness)")
    ax1.fill_between(xs, atls, ctls, alpha=0.08, color="#94a3b8")
    ax1.set_title("ATL / CTL — Training Load")
    ax1.set_ylabel("Load")
    ax1.legend()
    ax1.grid(alpha=0.3)
    _xticks(ax1, week_labels)

    colors = [_GREEN if t >= 0 else _RED for t in tsbs]
    ax2.bar(xs, tsbs, color=colors, alpha=0.8)
    ax2.axhline(0, color=_MUT, linewidth=1, linestyle="--")
    ax2.set_title("TSB (Form)")
    ax2.set_ylabel("TSB")
    ax2.grid(alpha=0.3)
    _xticks(ax2, week_labels)

    if current:
        atl  = current.get("atl",  0)
        ctl  = current.get("ctl",  0)
        tsb  = current.get("tsb",  0)
        form = current.get("form", "")
        fig.suptitle(
            f"Training Load  ·  ATL {atl:.0f}  CTL {ctl:.0f}  TSB {tsb:+.0f}"
            + (f"  ({form.split(' —')[0]})" if form else ""),
            fontsize=11, fontweight="bold",
        )
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Wellness Trends ───────────────────────────────────────────────────────────

@_register("get_garmin_wellness_trends")
def _wellness_trends(data: dict) -> Optional[bytes]:
    trend = data.get("trend") or []
    if not trend:
        return None

    dates   = [t["date"] for t in trend]
    sleeps  = [t.get("total_sleep_h")    or 0    for t in trend]
    bb_hi   = [t.get("body_battery_high") or None for t in trend]
    rhr     = [t.get("resting_hr")        or None for t in trend]
    steps   = [t.get("steps")             or 0    for t in trend]
    stress  = [t.get("avg_stress")                for t in trend]
    has_stress = any(s is not None for s in stress)

    n_rows = 5 if has_stress else 4
    fig, axes = plt.subplots(n_rows, 1, figsize=(10, 3 * n_rows + 1))
    if n_rows == 1:
        axes = [axes]
    xs = list(range(len(dates)))
    short = [d[-5:] for d in dates]

    def _setup(ax, title, ylabel):
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3)
        _xticks(ax, short)

    axes[0].bar(xs, sleeps, color=_BLUE, alpha=0.8)
    axes[0].axhline(8, color=_MUT, linestyle="--", linewidth=0.8)
    _setup(axes[0], "Sleep Duration", "h")

    if any(b for b in bb_hi if b):
        axes[1].fill_between(xs, bb_hi, alpha=0.3, color=_GREEN)
        axes[1].plot(xs, bb_hi, color=_GREEN, linewidth=2)
        axes[1].set_ylim(0, 100)
    else:
        axes[1].text(0.5, 0.5, "No body battery data", ha="center",
                     va="center", transform=axes[1].transAxes, color=_MUT)
    _setup(axes[1], "Body Battery (end-of-day high)", "%")

    if any(r for r in rhr if r):
        axes[2].plot(xs, rhr, color=_RED, linewidth=2, marker="o", markersize=3)
    else:
        axes[2].text(0.5, 0.5, "No resting HR data", ha="center",
                     va="center", transform=axes[2].transAxes, color=_MUT)
    _setup(axes[2], "Resting Heart Rate", "bpm")

    axes[3].bar(xs, steps, color=_AMBER, alpha=0.8)
    axes[3].axhline(10_000, color=_MUT, linestyle="--", linewidth=0.8)
    _setup(axes[3], "Daily Steps", "steps")

    if has_stress:
        stress_vals = [s or 0 for s in stress]
        axes[4].fill_between(xs, stress_vals, alpha=0.25, color=_PURPLE)
        axes[4].plot(xs, stress_vals, color=_PURPLE, linewidth=2, marker="o", markersize=3)
        axes[4].axhline(25, color=_GREEN, linestyle=":", linewidth=0.8)
        axes[4].axhline(50, color=_AMBER, linestyle=":", linewidth=0.8)
        axes[4].axhline(75, color=_RED,   linestyle=":", linewidth=0.8)
        axes[4].set_ylim(0, 100)
        _setup(axes[4], "Avg Stress Level", "0–100")

    if dates:
        fig.suptitle(f"Wellness Trends — {dates[0][:10]} to {dates[-1][:10]}",
                     fontsize=12, fontweight="bold")
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Body Composition ─────────────────────────────────────────────────────────

@_register("get_garmin_body_composition")
def _body_composition(data: dict) -> Optional[bytes]:
    measurements = data.get("measurements") or []
    if not measurements:
        return None
    dates   = [m.get("date", "") for m in measurements]
    weights = [m.get("weight_kg") for m in measurements]
    fats    = [m.get("body_fat_pct") for m in measurements]
    has_fat = any(f for f in fats if f)
    n_rows  = 2 if has_fat else 1
    fig, raw_axes = plt.subplots(n_rows, 1, figsize=(10, n_rows * 3), squeeze=False)
    axes = [ax for row in raw_axes for ax in row]
    short = [d[-5:] if len(d) >= 5 else d for d in dates]
    xs = list(range(len(dates)))

    axes[0].plot(xs, weights, color=_ORANGE, linewidth=2, marker="o", markersize=4)
    axes[0].set_ylabel("kg")
    axes[0].set_title("Weight")
    axes[0].grid(alpha=0.3)
    _xticks(axes[0], short)

    if has_fat:
        axes[1].fill_between(xs, fats, alpha=0.3, color=_CYAN)
        axes[1].plot(xs, fats, color=_CYAN, linewidth=2, marker="o", markersize=3)
        axes[1].set_ylabel("%")
        axes[1].set_title("Body Fat %")
        axes[1].grid(alpha=0.3)
        _xticks(axes[1], short)

    latest = data.get("latest") or {}
    title = "Body Composition"
    if latest.get("weight_kg"):
        title += f"  |  {latest['weight_kg']:.1f} kg"
    if latest.get("bmi"):
        title += f"  BMI {latest['bmi']:.1f}"
    fig.suptitle(title, fontsize=11, fontweight="bold")
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Garmin GPS Track (elevation-colored route map) ────────────────────────────

@_register("get_activity_gps_track")
def _gps_track(data: dict) -> Optional[bytes]:
    points = data.get("points") or []
    if len(points) < 2:
        return None

    act_id   = data.get("activity_id", "")
    has_ele  = any(p.get("ele") is not None for p in points)

    try:
        from staticmap import CircleMarker, Line, StaticMap

        smap = StaticMap(
            900, 650, padding_x=40, padding_y=40,
            url_template="https://cartodb-basemaps-a.global.ssl.fastly.net/dark_all/{z}/{x}/{y}.png",
            headers={"User-Agent": "FitDash/1.0"},
        )
        step   = max(1, len(points) // 400)
        pts_dn = points[::step]

        if has_ele:
            eles       = [p.get("ele") or 0 for p in pts_dn]
            valid_eles = [e for e in eles if e != 0]
            ele_min    = min(valid_eles) if valid_eles else 0
            ele_max    = max(valid_eles) if valid_eles else 1000

            def _ele_color(ele: float) -> str:
                t = max(0.0, min(1.0, (ele - ele_min) / max(1, ele_max - ele_min)))
                r = int(59  + t * (239 - 59))
                g = int(130 - t * (130 - 68))
                b = int(246 - t * (246 - 68))
                return f"#{r:02x}{g:02x}{b:02x}"

            for i in range(len(pts_dn) - 1):
                p0, p1 = pts_dn[i], pts_dn[i + 1]
                if p0.get("lon") and p0.get("lat") and p1.get("lon") and p1.get("lat"):
                    smap.add_line(
                        Line([(p0["lon"], p0["lat"]), (p1["lon"], p1["lat"])],
                             _ele_color(eles[i]), 4)
                    )
        else:
            coords = [(p["lon"], p["lat"]) for p in pts_dn
                      if p.get("lon") and p.get("lat")]
            if coords:
                smap.add_line(Line(coords, _BLUE, 4))

        first = next((p for p in points if p.get("lon") and p.get("lat")), None)
        last  = next((p for p in reversed(points) if p.get("lon") and p.get("lat")), None)
        if first:
            smap.add_marker(CircleMarker((first["lon"], first["lat"]), _GREEN, 14))
        if last:
            smap.add_marker(CircleMarker((last["lon"],  last["lat"]),  _RED,   14))

        image = smap.render()

        if has_ele and valid_eles:
            try:
                from PIL import Image, ImageDraw
                img  = Image.open(io.BytesIO(_pil_png(image))).convert("RGBA")
                draw = ImageDraw.Draw(img)
                lx, ly = 20, 20
                for i in range(20):
                    t = i / 19
                    r = int(59  + t * (239 - 59))
                    g = int(130 - t * (130 - 68))
                    b = int(246 - t * (246 - 68))
                    draw.rectangle([lx + i * 10, ly, lx + i * 10 + 9, ly + 14],
                                   fill=(r, g, b, 220))
                draw.text((lx,       ly + 18), f"{int(ele_min)} m", fill=(200, 200, 200, 255))
                draw.text((lx + 148, ly + 18), f"{int(ele_max)} m", fill=(200, 200, 200, 255))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="PNG")
                return buf.getvalue()
            except Exception:
                pass

        return _pil_png(image)

    except Exception:
        pass

    # Fallback: matplotlib lat/lon scatter plot (no tile server needed)
    lats = [p["lat"] for p in points if p.get("lat") and p.get("lon")]
    lons = [p["lon"] for p in points if p.get("lat") and p.get("lon")]
    if not lats:
        return None

    if has_ele:
        eles = [p.get("ele") or 0 for p in points if p.get("lat") and p.get("lon")]
        n_rows = 2
    else:
        eles = None
        n_rows = 1

    fig, raw_axes = plt.subplots(n_rows, 1, figsize=(10, n_rows * 3.5), squeeze=False)
    axes = [ax for row in raw_axes for ax in row]

    # Route plot
    ax0 = axes[0]
    ax0.plot(lons, lats, color=_BLUE, linewidth=1.5, zorder=2)
    ax0.scatter([lons[0]], [lats[0]], c=_GREEN, s=80, zorder=3)
    ax0.scatter([lons[-1]], [lats[-1]], c=_RED, s=80, zorder=3)
    ax0.set_xlabel("Longitude"); ax0.set_ylabel("Latitude")
    ax0.set_title(f"GPS Track — Activity {act_id}  ({data.get('total_points', len(points))} points)")
    ax0.set_aspect("equal"); ax0.grid(alpha=0.3)

    if has_ele and eles is not None:
        ax1 = axes[1]
        dists = list(range(len(eles)))
        ax1.fill_between(dists, eles, alpha=0.4, color=_BLUE)
        ax1.plot(dists, eles, color=_BLUE, linewidth=1.2)
        ax1.set_ylabel("Elevation (m)"); ax1.set_xlabel("GPS Points")
        ax1.set_title("Elevation Profile"); ax1.grid(alpha=0.3)

    fig.tight_layout()
    return _fig_to_png(fig)


# ── Daily Health Summary ──────────────────────────────────────────────────────

@_register("get_garmin_daily_health")
def _daily_health(data: dict) -> Optional[bytes]:
    date    = data.get("date", "")
    steps   = data.get("steps") or 0
    stress  = data.get("avg_stress") or 0
    rhr     = data.get("resting_hr")
    bb_now  = data.get("body_battery_now")
    bb_max  = data.get("body_battery_max")
    cal     = data.get("active_calories") or 0
    floors  = data.get("floors_climbed") or 0
    int_min = data.get("intensity_minutes") or 0

    fig, axes = plt.subplots(2, 3, figsize=(12, 6))
    axs = [ax for row in axes for ax in row]

    def _gauge(ax, value, vmax, label, color, unit="", goal=None):
        ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.set_aspect("equal")
        ax.axis("off")
        theta = np.linspace(np.pi, 0, 100)
        x_arc = 0.5 + 0.4 * np.cos(theta)
        y_arc = 0.5 + 0.4 * np.sin(theta)
        ax.plot(x_arc, y_arc, color=_GRD, linewidth=8, solid_capstyle="round")
        frac = min(1.0, value / max(1, vmax))
        if frac > 0:
            theta_f = np.linspace(np.pi, np.pi - frac * np.pi, 60)
            ax.plot(0.5 + 0.4 * np.cos(theta_f), 0.5 + 0.4 * np.sin(theta_f),
                    color=color, linewidth=8, solid_capstyle="round")
        disp = f"{int(value):,}" if isinstance(value, (int, float)) else str(value)
        ax.text(0.5, 0.38, disp + unit, ha="center", va="center",
                fontsize=13, fontweight="bold", color=_FG)
        ax.text(0.5, 0.18, label, ha="center", va="center", fontsize=9, color=_MUT)
        if goal:
            ax.text(0.5, 0.06, f"goal {goal:,}", ha="center", fontsize=7, color=_MUT)

    _gauge(axs[0], steps, 12000, "Steps",   _AMBER, goal=10000)
    _gauge(axs[1], stress, 100,  "Stress",  _RED   if stress > 50 else _GREEN)
    _gauge(axs[2], bb_max or bb_now or 0, 100, "Body Battery", _GREEN, "%")
    _gauge(axs[3], cal,    800,  "Active Calories", _ORANGE, " kcal")
    _gauge(axs[4], int_min, 60, "Intensity Min", _BLUE, " min", goal=30)
    _gauge(axs[5], floors, 15, "Floors",   _CYAN)

    title = f"Daily Health — {date}"
    if rhr:
        title += f"   RHR: {rhr} bpm"
    fig.suptitle(title, fontsize=12, fontweight="bold")
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Intraday Steps ────────────────────────────────────────────────────────────

@_register("get_garmin_steps_timeline")
def _steps_timeline(data: dict) -> Optional[bytes]:
    date    = data.get("date", "")
    buckets = data.get("buckets_15min") or []
    if not buckets:
        return None

    times  = [b["time"] for b in buckets]
    steps  = [b.get("steps") or 0 for b in buckets]
    levels = [b.get("activity_level", "") for b in buckets]

    _LEVEL_COLOR = {
        "active":    _GREEN,
        "highly_active": _ORANGE,
        "sedentary": _MUT,
        "sleeping":  _BLUE,
    }
    colors = [_LEVEL_COLOR.get(lv.lower() if lv else "", _AMBER) for lv in levels]

    fig, ax = plt.subplots(figsize=(12, 4))
    xs = list(range(len(times)))
    bars = ax.bar(xs, steps, color=colors, alpha=0.85, width=0.85)

    total = sum(steps)
    ax.axhline(total / max(len(times), 1), color=_MUT, linestyle="--",
               linewidth=0.8, alpha=0.6)

    # Show only every ~2h of labels
    step = max(1, len(times) // 12)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels(times[::step], rotation=30, ha="right", fontsize=7)
    ax.set_ylabel("Steps (15 min)")
    ax.set_title(f"Steps Timeline — {date}   Total: {total:,} steps")
    ax.grid(axis="y", alpha=0.3)

    # Legend patches
    import matplotlib.patches as mpatches
    legend_items = [
        mpatches.Patch(color=_GREEN,  label="Active"),
        mpatches.Patch(color=_ORANGE, label="Very active"),
        mpatches.Patch(color=_MUT,    label="Sedentary"),
        mpatches.Patch(color=_BLUE,   label="Sleeping"),
    ]
    ax.legend(handles=legend_items, fontsize=8, loc="upper right")
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Intraday Stress ───────────────────────────────────────────────────────────

@_register("get_garmin_stress_timeline")
def _stress_timeline(data: dict) -> Optional[bytes]:
    date     = data.get("date", "")
    timeline = data.get("timeline") or []
    if not timeline:
        return None

    times   = [t["time"]   for t in timeline]
    stresses = [t["stress"] for t in timeline]
    avg     = data.get("avg_stress")
    mx      = data.get("max_stress")
    mx_time = data.get("max_stress_time", "")

    # Gradient coloring by zone
    _ZONE_COLOR = {
        "low":       _GREEN,
        "medium":    _AMBER,
        "high":      _RED,
        "very_high": _ROSE,
    }
    colors = [_ZONE_COLOR.get(t.get("category", "low"), _GREEN) for t in timeline]

    fig, ax = plt.subplots(figsize=(12, 4))
    xs = list(range(len(times)))
    ax.fill_between(xs, stresses, alpha=0.15, color=_RED)
    ax.plot(xs, stresses, color=_MUT, linewidth=0.8, alpha=0.6)

    # Colored scatter points by zone
    for cat, color in _ZONE_COLOR.items():
        pts = [(x, s) for x, s, t in zip(xs, stresses, timeline)
               if t.get("category") == cat]
        if pts:
            ax.scatter([p[0] for p in pts], [p[1] for p in pts],
                       c=color, s=10, zorder=3, alpha=0.7)

    if avg:
        ax.axhline(avg, color=_AMBER, linestyle="--", linewidth=1,
                   label=f"Avg {avg}")
    ax.axhline(25,  color=_GREEN,  linestyle=":", linewidth=0.6, alpha=0.5)
    ax.axhline(50,  color=_AMBER,  linestyle=":", linewidth=0.6, alpha=0.5)
    ax.axhline(75,  color=_RED,    linestyle=":", linewidth=0.6, alpha=0.5)

    step = max(1, len(times) // 12)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels(times[::step], rotation=30, ha="right", fontsize=7)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Stress")
    title = f"Stress Timeline — {date}"
    if avg: title += f"   Avg: {avg}"
    if mx:  title += f"   Peak: {mx}"
    if mx_time: title += f" at {mx_time}"
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return _fig_to_png(fig)


# ── HRV Status ────────────────────────────────────────────────────────────────

@_register("get_garmin_hrv_status")
def _hrv_status(data: dict) -> Optional[bytes]:
    date    = data.get("date", "")
    hrv     = data.get("last_night_hrv")
    bl_lo   = data.get("baseline_low")
    bl_bal_lo  = data.get("baseline_balanced_low")
    bl_bal_hi  = data.get("baseline_balanced_high")
    status  = data.get("status", "")
    feedback = data.get("feedback", "")

    if hrv is None:
        return None

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.set_xlim(0, 100); ax.set_ylim(0, 1); ax.axis("off")

    # Draw HRV scale bar (0–120 ms typical range)
    _max_hrv = 120.0
    bar_y = 0.55; bar_h = 0.18

    # Baseline zone
    if bl_bal_lo and bl_bal_hi:
        x0 = bl_bal_lo / _max_hrv * 100
        x1 = bl_bal_hi / _max_hrv * 100
        ax.barh(bar_y, x1 - x0, left=x0, height=bar_h,
                color=_GREEN, alpha=0.3, label="Balanced baseline")

    # Low zone
    if bl_lo:
        ax.barh(bar_y, bl_lo / _max_hrv * 100, height=bar_h,
                color=_RED, alpha=0.15, label="Below baseline")

    # Full background bar
    ax.barh(bar_y, 100, height=bar_h, color=_GRD, alpha=0.4, zorder=0)

    # Current HRV marker
    hrv_x = min(99.0, hrv / _max_hrv * 100)
    status_color = (_GREEN if "balanced" in (status or "").lower()
                    else _AMBER if "unbalanced" in (status or "").lower()
                    else _RED)
    ax.axvline(hrv_x, ymin=0.3, ymax=0.85,
               color=status_color, linewidth=4, zorder=5)
    ax.text(hrv_x, 0.78, f"{hrv:.0f} ms",
            ha="center", va="bottom", fontsize=14, fontweight="bold",
            color=status_color)

    # Labels
    ax.text(0,   0.42, "0",        ha="left",   va="top", fontsize=7, color=_MUT)
    ax.text(100, 0.42, f"{int(_max_hrv)}ms", ha="right", va="top", fontsize=7, color=_MUT)

    # Status text
    status_label = (status or "").replace("_", " ").title()
    ax.text(50, 0.28, status_label, ha="center", fontsize=12, fontweight="bold",
            color=status_color)
    if feedback:
        fb_clean = feedback.replace("_", " ").lower()
        ax.text(50, 0.12, fb_clean, ha="center", fontsize=9, color=_MUT,
                style="italic")

    ax.legend(loc="upper left", fontsize=8, framealpha=0.3)
    ax.set_title(f"HRV Status — {date}   Last night: {hrv:.0f} ms",
                 fontsize=11, fontweight="bold")
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Training Metrics (VO2max, race predictions, training status) ──────────────

@_register("get_garmin_training_metrics")
def _training_metrics(data: dict) -> Optional[bytes]:
    vo2 = data.get("vo2max_running")
    status  = data.get("training_status", "")
    load_7  = data.get("training_load_7d")
    load_28 = data.get("training_load_28d")
    preds   = data.get("race_predictions") or {}
    ready   = data.get("training_readiness")
    date    = data.get("date", "")

    if not any([vo2, status, preds]):
        return None

    fig = plt.figure(figsize=(12, 5))

    # Left panel: VO2max gauge + training status
    ax_left = fig.add_axes([0.02, 0.1, 0.4, 0.8])
    ax_left.set_xlim(0, 1); ax_left.set_ylim(0, 1); ax_left.axis("off")

    if vo2:
        # VO2max arc gauge (25=poor … 65=elite)
        vo2_norm = (vo2 - 25.0) / 40.0
        color = (_GREEN if vo2 > 50 else _AMBER if vo2 > 40 else _RED)
        theta = np.linspace(np.pi, 0, 100)
        ax_left.plot(0.5 + 0.38 * np.cos(theta), 0.65 + 0.38 * np.sin(theta),
                     color=_GRD, linewidth=10, solid_capstyle="round", zorder=1)
        frac = max(0.01, min(1.0, vo2_norm))
        theta_f = np.linspace(np.pi, np.pi - frac * np.pi, 80)
        ax_left.plot(0.5 + 0.38 * np.cos(theta_f), 0.65 + 0.38 * np.sin(theta_f),
                     color=color, linewidth=10, solid_capstyle="round", zorder=2)
        ax_left.text(0.5, 0.55, f"{vo2:.1f}", ha="center", fontsize=22,
                     fontweight="bold", color=color)
        ax_left.text(0.5, 0.42, "VO2max (ml/kg/min)", ha="center",
                     fontsize=9, color=_MUT)

    if status:
        ax_left.text(0.5, 0.28, status.replace("_", " ").title(),
                     ha="center", fontsize=11, color=_AMBER, fontweight="bold")

    info_parts = []
    if ready:    info_parts.append(f"Readiness: {ready}")
    if load_7:   info_parts.append(f"7d load: {load_7:.0f}")
    if load_28:  info_parts.append(f"28d load: {load_28:.0f}")
    ax_left.text(0.5, 0.12, "   ".join(info_parts), ha="center",
                 fontsize=8, color=_FG)

    # Right panel: race predictions
    ax_right = fig.add_axes([0.44, 0.1, 0.54, 0.8])
    ax_right.set_xlim(0, 1); ax_right.set_ylim(0, 1); ax_right.axis("off")
    ax_right.set_title("Race Predictions", fontsize=10, color=_FG, pad=4)

    races = [("5 km", preds.get("5k")), ("10 km", preds.get("10k")),
             ("Half", preds.get("half_marathon")), ("Marathon", preds.get("marathon"))]
    present = [(r, t) for r, t in races if t]
    if present:
        palette_r = [_ORANGE, _BLUE, _GREEN, _PURPLE]
        for i, (race, time_str) in enumerate(present):
            y = 0.82 - i * 0.20
            ax_right.text(0.05, y, race, fontsize=10, color=_MUT, va="center")
            ax_right.text(0.95, y, time_str, fontsize=14, color=palette_r[i],
                          va="center", ha="right", fontweight="bold")
            ax_right.axhline(y - 0.08, color=_GRD, linewidth=0.5, alpha=0.5)
    else:
        ax_right.text(0.5, 0.5, "No race predictions available",
                      ha="center", va="center", fontsize=9, color=_MUT)

    fig.suptitle(f"Training Metrics — {date}", fontsize=12, fontweight="bold")
    return _fig_to_png(fig)


# ── Activity Detail (lap splits + HR zones) ───────────────────────────────────

@_register("get_garmin_activity_detail")
def _activity_detail(data: dict) -> Optional[bytes]:
    laps     = data.get("laps") or []
    hr_zones = data.get("hr_zones") or []
    name     = data.get("name", "Activity")
    date     = data.get("date", "")
    atype    = data.get("type", "")

    if not laps and not hr_zones:
        return None

    n_rows = sum([bool(laps), bool(hr_zones)])
    fig, raw_axes = plt.subplots(n_rows, 1, figsize=(10, n_rows * 3.5), squeeze=False)
    axes = [ax for row in raw_axes for ax in row]
    ax_i = 0

    if laps:
        lap_nums  = [l.get("lap", i + 1) for i, l in enumerate(laps)]
        paces     = [l.get("pace_min_per_km") for l in laps]
        lap_hr    = [l.get("avg_hr") for l in laps]
        has_pace  = any(p for p in paces if p and p < 20)
        has_hr    = any(h for h in lap_hr if h)

        ax = axes[ax_i]; ax_i += 1
        xs = list(range(len(lap_nums)))
        if has_pace:
            valid_p = [p if p and p < 20 else None for p in paces]
            ax.bar(xs, valid_p, color=_ORANGE, alpha=0.85, label="Pace (min/km)")
            ax.invert_yaxis()
            ax.set_ylabel("min/km  (lower=faster)")
            if has_hr:
                ax2 = ax.twinx()
                ax2.plot(xs, lap_hr, color=_RED, linewidth=1.5,
                         marker="o", markersize=4, label="Avg HR")
                ax2.set_ylabel("bpm", color=_RED)
                ax2.tick_params(axis="y", colors=_RED)
                ax2.legend(loc="upper right", fontsize=8)
        else:
            dists = [l.get("distance_km", 0) for l in laps]
            ax.bar(xs, dists, color=_BLUE, alpha=0.85)
            ax.set_ylabel("km")

        ax.set_xticks(xs)
        ax.set_xticklabels([f"Lap {n}" for n in lap_nums], rotation=30, ha="right")
        ax.set_title("Lap Splits")
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(axis="y", alpha=0.3)

    if hr_zones:
        ax = axes[ax_i]
        zones = [f"Z{z.get('zone', i + 1)}" for i, z in enumerate(hr_zones)]
        times = [z.get("time_min", 0) for z in hr_zones]
        zone_colors = [_BLUE, _GREEN, _AMBER, _ORANGE, _RED][:len(zones)]
        bars = ax.barh(zones, times, color=zone_colors, alpha=0.85)
        mx = max(times) if times else 1
        for bar, val in zip(bars, times):
            if val > 0:
                ax.text(val + mx * 0.01, bar.get_y() + bar.get_height() / 2,
                        f"{val:.0f} min", va="center", fontsize=8, color=_FG)
        ax.set_xlabel("Minutes")
        ax.set_title("HR Zone Distribution")
        ax.grid(axis="x", alpha=0.3)

    fig.suptitle(f"{name} — {date} ({atype})", fontsize=11, fontweight="bold")
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Weekly Training Trends ────────────────────────────────────────────────────

@_register("get_training_trends")
def _training_trends(data: dict) -> Optional[bytes]:
    weeks = data.get("weeks") or []
    if not weeks:
        return None
    dates = [w.get("week_start", "") for w in weeks]
    dists = [w.get("distance_km", 0) for w in weeks]
    xs = list(range(len(weeks)))
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(xs, dists, color=_ORANGE, alpha=0.85)
    ax.set_xticks(xs)
    ax.set_xticklabels([d[:7] if len(d) >= 7 else d for d in dates], rotation=45, ha="right")
    ax.set_ylabel("km")
    ax.set_title("Weekly Training Volume")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Year-over-Year Breakdown ──────────────────────────────────────────────────

@_register("get_yearly_breakdown")
def _yearly_breakdown(data: dict) -> Optional[bytes]:
    years = data.get("years") or []
    if not years:
        return None
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar([str(y.get("year", "")) for y in years],
           [y.get("total_distance_km", 0) for y in years],
           color=_ORANGE, alpha=0.85)
    ax.set_ylabel("km")
    ax.set_title("Year-over-Year Total Distance")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Activity vs Baseline Comparison ──────────────────────────────────────────

@_register("compare_activity_to_baseline")
def _compare_baseline(data: dict) -> Optional[bytes]:
    comparisons = data.get("comparisons") or {}
    activity    = data.get("activity") or {}
    if not comparisons:
        return None
    _METRIC_LABELS = {
        "pace_min_per_km":  "Pace",
        "avg_hr_bpm":       "Avg HR",
        "distance_km":      "Distance",
        "elevation_m":      "Elevation",
        "elevation_per_km": "Elev/km",
    }
    metrics, pcts = [], []
    for key, label in _METRIC_LABELS.items():
        v = comparisons.get(key)
        if v and v.get("difficulty_percentile") is not None:
            metrics.append(label)
            pcts.append(v["difficulty_percentile"])
    if not metrics:
        return None

    zone_colors = [_RED if p >= 85 else _AMBER if p >= 65 else _BLUE if p >= 35 else _GREEN
                   for p in pcts]
    fig, ax = plt.subplots(figsize=(8, max(3, len(metrics) * 0.8)))
    bars = ax.barh(metrics, pcts, color=zone_colors, alpha=0.85)
    mx = max(pcts) if pcts else 1
    for bar, val in zip(bars, pcts):
        ax.text(val + mx * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{val:.0f}th", va="center", fontsize=8, color=_FG)
    ax.axvline(50, color=_MUT, linewidth=1, linestyle="--")
    ax.set_xlabel("Difficulty percentile")
    act_name = activity.get("name", "Activity")
    act_date = activity.get("date", "")
    assessment = data.get("assessment", "")
    ax.set_title(f"{act_name} — {act_date}\n{assessment.title()}" if assessment else f"{act_name} — {act_date}")
    ax.set_xlim(0, 110)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Activity Stats ────────────────────────────────────────────────────────────

@_register("get_activity_stats")
def _activity_stats(data: dict) -> Optional[bytes]:
    breakdown = data.get("sport_breakdown") or {}
    if not breakdown:
        return None

    sports = list(breakdown.keys())
    distances = [breakdown[s].get("distance_km", 0) for s in sports]
    times     = [breakdown[s].get("time_hours", 0)  for s in sports]
    counts    = [breakdown[s].get("count", 0)        for s in sports]

    # Sort by distance descending
    order = sorted(range(len(sports)), key=lambda i: distances[i], reverse=True)
    sports    = [sports[i]    for i in order]
    distances = [distances[i] for i in order]
    times     = [times[i]     for i in order]
    counts    = [counts[i]    for i in order]

    colors = [_ORANGE, _BLUE, _GREEN, _AMBER, _PURPLE, _CYAN, _RED, _ROSE]
    bar_colors = [colors[i % len(colors)] for i in range(len(sports))]
    xs = list(range(len(sports)))

    fig, axes = plt.subplots(1, 2, figsize=(12, max(4, len(sports) * 0.7 + 2)))

    axes[0].barh(xs, distances, color=bar_colors, alpha=0.85)
    axes[0].set_yticks(xs)
    axes[0].set_yticklabels(sports)
    axes[0].set_xlabel("km")
    axes[0].set_title("Distance by Sport")
    axes[0].grid(axis="x", alpha=0.3)
    for i, (d, c) in enumerate(zip(distances, counts)):
        axes[0].text(d + max(distances) * 0.01, i, f"{d:.0f} km ({c}x)",
                     va="center", fontsize=8, color=_FG)

    axes[1].barh(xs, times, color=bar_colors, alpha=0.85)
    axes[1].set_yticks(xs)
    axes[1].set_yticklabels(sports)
    axes[1].set_xlabel("hours")
    axes[1].set_title("Time by Sport")
    axes[1].grid(axis="x", alpha=0.3)
    for i, t in enumerate(times):
        axes[1].text(t + max(times) * 0.01, i, f"{t:.0f} h",
                     va="center", fontsize=8, color=_FG)

    total_dist = data.get("total_distance_km", 0)
    total_acts = data.get("total_activities", 0)
    fig.suptitle(f"All-Time Stats — {total_acts} activities · {total_dist:,.0f} km total",
                 fontsize=12, fontweight="bold")
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Personal Bests ────────────────────────────────────────────────────────────

@_register("get_personal_bests")
def _personal_bests(data: dict) -> Optional[bytes]:
    top_dist = data.get("top_5_by_distance") or []
    top_fast = data.get("top_5_fastest") or []
    top_elev = data.get("top_5_by_elevation") or []

    # Only render if there's something to show
    sections = [(top_dist, "Top 5 by Distance", "distance_km", "km"),
                (top_elev, "Top 5 by Elevation", "elevation_gain_m", "m")]
    valid = [(items, title, key, unit) for items, title, key, unit in sections if items]
    if not valid:
        return None

    n = len(valid) + (1 if top_fast else 0)
    fig, raw_axes = plt.subplots(1, n, figsize=(6 * n, 5), squeeze=False)
    axes = list(raw_axes[0])

    idx = 0
    for items, title, key, unit in valid:
        names  = [a.get("name", "")[:20] or a.get("type", "") for a in items]
        values = [a.get(key, 0) for a in items]
        ys = list(range(len(names)))
        axes[idx].barh(ys, values, color=_ORANGE, alpha=0.85)
        axes[idx].set_yticks(ys)
        axes[idx].set_yticklabels(names, fontsize=8)
        axes[idx].set_xlabel(unit)
        axes[idx].set_title(title)
        axes[idx].grid(axis="x", alpha=0.3)
        mx = max(values) if values else 1
        for yi, (v, a) in enumerate(zip(values, items)):
            date = (a.get("date") or "")[:10]
            axes[idx].text(v + mx * 0.01, yi, f"{v:.1f} {unit}  {date}",
                           va="center", fontsize=7, color=_FG)
        idx += 1

    if top_fast:
        names  = [a.get("name", "")[:20] or a.get("type", "") for a in top_fast]
        paces  = [a.get("pace_min_per_km") for a in top_fast]
        valid_paces = [(n2, p) for n2, p in zip(names, paces) if p]
        if valid_paces:
            names2, paces2 = zip(*valid_paces)
            ys = list(range(len(names2)))
            axes[idx].barh(ys, paces2, color=_BLUE, alpha=0.85)
            axes[idx].set_yticks(ys)
            axes[idx].set_yticklabels(names2, fontsize=8)
            axes[idx].set_xlabel("min/km")
            axes[idx].set_title("Top 5 Fastest (Pace)")
            axes[idx].invert_xaxis()
            axes[idx].grid(axis="x", alpha=0.3)

    biggest_week = data.get("biggest_week") or {}
    streak = data.get("longest_streak_days", 0)
    subtitle = ""
    if biggest_week:
        subtitle += f"Best week: {biggest_week.get('distance_km', 0):.0f} km"
    if streak:
        subtitle += f"  |  Longest streak: {streak} days"
    if subtitle:
        fig.suptitle(subtitle.strip(), fontsize=10, fontweight="bold")

    fig.tight_layout()
    return _fig_to_png(fig)


# ── Weather Forecast ──────────────────────────────────────────────────────────

@_register("get_weather_forecast")
def _weather_forecast(data: dict) -> Optional[bytes]:
    forecast = data.get("forecast") or []
    if not forecast:
        return None

    dates     = [f["date"] for f in forecast]
    temp_min  = [f.get("temp_min_c") or 0 for f in forecast]
    temp_max  = [f.get("temp_max_c") or 0 for f in forecast]
    rain_prob = [f.get("precip_probability_pct") or 0 for f in forecast]
    wind      = [f.get("wind_max_kmh") or 0 for f in forecast]

    xs    = list(range(len(dates)))
    short = [d[-5:] for d in dates]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7))

    # Temperature band
    ax1.fill_between(xs, temp_min, temp_max, alpha=0.25, color=_ORANGE)
    ax1.plot(xs, temp_max, color=_ORANGE, linewidth=2, marker="o", markersize=4, label="Max")
    ax1.plot(xs, temp_min, color=_BLUE,   linewidth=2, marker="o", markersize=4,
             linestyle="--", label="Min")
    ax1.axhline(20, color=_MUT, linestyle=":", linewidth=0.8, label="20°C ideal upper")
    ax1.axhline(5,  color=_MUT, linestyle=":",  linewidth=0.8, label="5°C ideal lower")
    ax1.legend(loc="upper right", fontsize=8)
    ax1.set_ylabel("°C")
    ax1.set_title("Temperature")
    ax1.grid(alpha=0.3)
    _xticks(ax1, short)

    # Rain probability — color by threshold
    bar_colors = [_BLUE if p < 30 else (_AMBER if p < 60 else _RED) for p in rain_prob]
    ax2.bar(xs, rain_prob, color=bar_colors, alpha=0.85)
    ax2.axhline(30, color=_MUT, linestyle=":", linewidth=0.8)
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("%")
    ax2.set_title("Rain Probability")
    ax2.grid(alpha=0.3)
    _xticks(ax2, short)

    # Annotate each bar with wind speed
    for xi, (rp, wd) in enumerate(zip(rain_prob, wind)):
        if wd:
            ax2.text(xi, min(rp + 3, 95), f"{wd:.0f}", ha="center", fontsize=7, color=_MUT)
    ax2.text(0.99, 0.98, "numbers = wind km/h", transform=ax2.transAxes,
             ha="right", va="top", fontsize=7, color=_MUT)

    loc = data.get("location", "Karlsruhe")
    fig.suptitle(f"Weather Forecast — {loc}", fontsize=12, fontweight="bold")
    fig.tight_layout()
    return _fig_to_png(fig)


# ── Gear Mileage ──────────────────────────────────────────────────────────────

@_register("get_gear_info")
def _gear_info(data: dict) -> Optional[bytes]:
    from matplotlib.patches import Patch as _Patch

    shoes = data.get("shoes") or []
    bikes = data.get("bikes") or []
    all_items = [(s, "shoe") for s in shoes] + [(b, "bike") for b in bikes]
    if not all_items:
        return None

    names  = []
    for item, _ in all_items:
        brand = item.get("brand") or item.get("model") or ""
        label = f"{item.get('name') or 'Unknown'}" + (f" ({brand})" if brand else "")
        names.append(label[:35])
    distances  = [item.get("distance_km", 0)      for item, _ in all_items]
    primaries  = [item.get("primary", False)       for item, _ in all_items]
    type_list  = [t                                for _, t    in all_items]
    bar_colors = [_ORANGE if t == "shoe" else _BLUE for t in type_list]

    fig, ax = plt.subplots(figsize=(10, max(4, len(all_items) * 0.9 + 1.5)))
    ys = list(range(len(names)))
    ax.barh(ys, distances, color=bar_colors, alpha=0.85)
    ax.set_yticks(ys)
    ax.set_yticklabels(names)
    ax.set_xlabel("km")
    ax.set_title("Gear Mileage")
    ax.grid(axis="x", alpha=0.3)

    mx = max(distances) if distances else 1
    for yi, (d, prim) in enumerate(zip(distances, primaries)):
        star = " ★" if prim else ""
        ax.text(d + mx * 0.01, yi, f"{d:,.0f} km{star}", va="center", fontsize=8, color=_FG)

    legend_handles = []
    if any(t == "shoe" for t in type_list):
        legend_handles.append(_Patch(color=_ORANGE, label="Running Shoes"))
    if any(t == "bike" for t in type_list):
        legend_handles.append(_Patch(color=_BLUE, label="Bikes"))
    if legend_handles:
        ax.legend(handles=legend_handles, loc="lower right")

    fig.tight_layout()
    return _fig_to_png(fig)
