"""Automated technical-robustness test suite for the live Training Copilot stack.

This is the *technical* counterpart to the persona quality evaluation in
``evaluation/run_e2e.py``: instead of asking whether the answers are good, it asks
whether the plumbing holds — latency, error rates, hostile input, an unreachable
server, concurrent load, and whether the agent layer still routes a question to
the right MCP server.

Structural rule (the same one ``evaluation/config.py`` states): **this package is
a consumer of ``core``; ``core`` never imports ``evaluation``.** The suite drives
the stack through exactly the client the app itself uses — ``core.host.ToolHost``
— so what it measures is what the agents experience. It needs no MLflow and no
LLM key; only ``--with-orchestrator`` spends tokens.

Five probes:

1. **sweep**       — N valid calls per fixtured tool; latency percentiles + error rate.
2. **malformed**   — hostile arguments; the contract is a *structured error*, never
                     an exception, and the server must survive (a valid call after).
3. **unreachable** — a dead endpoint must degrade (empty tool list / error string)
                     within the timeout, and must not take healthy servers down.
4. **concurrency** — the same calls at rising parallelism; success rate + latency.
5. **selection**   — read-only prompts through ``FitDashOrchestrator``; did the
                     right server's tools show up in the trace? (opt-in, costs tokens)

Safety: default-deny. Only tools with an explicit fixture in ``fixtures.py`` are
ever called, and every candidate is additionally checked against the write-path
veto (``fixtures.write_path_reason``). See ``README.md`` in this directory.

Run from the repo root:

    python -m evaluation.robustness.run_robustness
    python -m evaluation.robustness.run_robustness --repeat 3 --only weather__,routes__
    python -m evaluation.robustness.run_robustness --skip-sweep --concurrency 1,8
    python -m evaluation.robustness.run_robustness --with-orchestrator   # spends LLM tokens
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]          # repo root (AISS2_Team6/)
if str(ROOT) not in sys.path:                        # allow running from anywhere
    sys.path.insert(0, str(ROOT))

from core.config import MCP_SERVERS, SEP            # noqa: E402  (after the path fix)
from core.host import ToolHost                      # noqa: E402

from .fixtures import (  # noqa: E402
    FIXTURES, SKIP, malformed_variants, pace_seconds, skip_reason, write_path_reason,
)

REPORTS_DIR = ROOT / "evaluation" / "reports"

# A port nothing listens on (RFC 863 "discard"), used for the unreachable probe.
GHOST_URL = "http://127.0.0.1:9/mcp"
GHOST_TIMEOUT = 5.0
# How long past the timeout a graceful degradation may still take.
GHOST_SLACK_S = 3.0

# The two cheap public-API tools the concurrency probe alternates between.
CONCURRENCY_POOL = ("weather__get_weather_forecast", "routes__geocode")

# Read-only selection probes: prompt → tool-name prefixes that would prove the
# orchestrator reached the right specialist. Phrasing is strictly read-only
# ("show", "check", "how was", "find") — nothing here asks to create or change.
SELECTION_PROMPTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("How was my sleep last night?", ("garmin__",)),
    ("Check my HRV and body battery from the last few days.", ("garmin__",)),
    ("How is my training load right now compared to the past weeks?", ("strava__", "garmin__")),
    ("Show me my activities from the last two weeks.", ("strava__", "garmin__")),
    ("Is my running pace improving over the recent runs?", ("strava__",)),
    ("What is the weather like for a ride tomorrow morning?", ("weather__",)),
    ("Check the pollen and UV levels in Karlsruhe today.", ("weather__",)),
    ("Where do I have a free slot in my calendar this week for a long run?", ("calendar__",)),
    ("What is on my schedule for the next three days?", ("calendar__",)),
    ("Find me a 10 km running loop starting in Karlsruhe.", ("routes__",)),
    ("What does the sports-science literature say about polarized training?",
     ("search_fitness_literature",)),
    ("Show me an overview of my season so far.", ("athlete__", "strava__")),
)


# ── Small helpers ─────────────────────────────────────────────────────────────

def _timestamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile (q in 0..1). No numpy dependency."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return round(s[0], 1)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return round(s[int(k)], 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def _latency_stats(values: Sequence[float]) -> Dict[str, Optional[float]]:
    return {
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": round(max(values), 1) if values else None,
        "mean_ms": round(sum(values) / len(values), 1) if values else None,
    }


def _result_error(raw: str) -> Optional[str]:
    """The tool's error, or ``None`` if the result looks like a normal reply.

    Stricter than ``core.agent_trace.error_of``: an ``error`` *key* counts even
    when its value is falsy, and an empty result is itself an error — this is a
    health probe, not a rendering path.
    """
    if not (raw or "").strip():
        return "empty result"
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None                                  # plain text is a valid reply
    if isinstance(data, dict) and "error" in data:
        return str(data.get("error") or "unspecified error")
    return None


_NUMBER_RE = re.compile(r"\d+")
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")


def _normalize_error(message: str) -> str:
    """Collapse an error message into a taxonomy key (ids/numbers/quotes masked)."""
    text = " ".join((message or "").split()).lower()
    text = _QUOTED_RE.sub("'…'", text)
    text = _NUMBER_RE.sub("<n>", text)
    return text[:100] or "unspecified error"


def _short(value: Any) -> str:
    """Compact repr of an argument value for a variant label."""
    if isinstance(value, str) and len(value) > 24:
        return f"<str×{len(value)}>"
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= 40 else text[:37] + "…"


def _variant_label(valid: Dict[str, Any], bad: Dict[str, Any]) -> str:
    """Describe a malformed variant as its diff against the valid arguments."""
    parts: List[str] = []
    for key in sorted(set(valid) | set(bad)):
        if key not in bad:
            parts.append(f"-{key}")                       # dropped (maybe required)
        elif key not in valid:
            parts.append(f"+{key}={_short(bad[key])}")    # unknown extra argument
        elif bad[key] != valid[key]:
            parts.append(f"{key}={_short(bad[key])}")     # wrong type / out of domain
    return ", ".join(parts) or "(identical to valid args)"


def _selected(name: str, only: Sequence[str]) -> bool:
    return not only or any(fragment in name for fragment in only)


def _server_of(name: str) -> str:
    return name.partition(SEP)[0]


# ── The one primitive every probe is built from ───────────────────────────────

async def _timed_call(host: ToolHost, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """One tool call, timed and classified. Never raises.

    ``crashed`` records a violation of the ToolHost contract (errors come back as
    ``{"error": …}`` strings, exceptions never escape) — that is a FAIL, not noise.
    """
    started = time.perf_counter()
    try:
        raw = await host.acall_tool(name, args)
    except Exception as exc:                              # contract violation
        return {
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "ok": False, "crashed": True,
            "error": f"{type(exc).__name__}: {exc}", "chars": 0,
        }
    error = _result_error(raw)
    return {
        "ms": round((time.perf_counter() - started) * 1000, 1),
        "ok": error is None, "crashed": False,
        "error": error, "chars": len(raw or ""),
    }


# ── Probe 1 · sweep ───────────────────────────────────────────────────────────

async def probe_sweep(host: ToolHost, targets: Sequence[str], repeat: int) -> Dict[str, Any]:
    """N sequential valid calls per fixtured tool; per-tool and per-server rollups."""
    tools: List[Dict[str, Any]] = []
    for name in targets:
        args = FIXTURES[name]["args"]
        pace = pace_seconds(name)
        calls: List[Dict[str, Any]] = []
        for i in range(repeat):
            if pace and i:
                await asyncio.sleep(pace)
            calls.append(await _timed_call(host, name, args))
        latencies = [c["ms"] for c in calls]
        ok = sum(1 for c in calls if c["ok"])
        record = {
            "tool": name,
            "server": _server_of(name),
            "args": args,
            "calls": len(calls),
            "ok": ok,
            "err": len(calls) - ok,
            "crashes": sum(1 for c in calls if c["crashed"]),
            "success_rate": round(ok / len(calls), 3) if calls else 0.0,
            "errors": sorted({c["error"] for c in calls if c["error"]}),
            "chars_max": max((c["chars"] for c in calls), default=0),
            "latencies_ms": latencies,
        }
        record.update(_latency_stats(latencies))
        tools.append(record)
        mark = "✓" if record["err"] == 0 else "✗"
        print(f"  {mark} {name:<44} {ok}/{len(calls)} ok  "
              f"p50 {record['p50_ms']} ms  max {record['max_ms']} ms")

    by_server: Dict[str, Dict[str, Any]] = {}
    for record in tools:
        bucket = by_server.setdefault(record["server"], {"tools": 0, "calls": 0, "ok": 0,
                                                         "err": 0, "latencies": []})
        bucket["tools"] += 1
        bucket["calls"] += record["calls"]
        bucket["ok"] += record["ok"]
        bucket["err"] += record["err"]
        bucket["latencies"].extend(record["latencies_ms"])
    for bucket in by_server.values():
        latencies = bucket.pop("latencies")
        bucket["success_rate"] = round(bucket["ok"] / bucket["calls"], 3) if bucket["calls"] else 0.0
        bucket.update(_latency_stats(latencies))

    return {"enabled": True, "repeat": repeat, "tools": tools, "by_server": by_server}


# ── Probe 2 · malformed input ────────────────────────────────────────────────

async def probe_malformed(host: ToolHost, targets: Sequence[str]) -> Dict[str, Any]:
    """Hostile arguments must produce structured errors, and the server must live."""
    tools: List[Dict[str, Any]] = []
    for name in targets:
        variants = malformed_variants(name)
        if not variants:
            continue
        valid_args = FIXTURES[name]["args"]
        pace = pace_seconds(name)
        results: List[Dict[str, Any]] = []
        for bad in variants:
            if pace:
                await asyncio.sleep(pace)
            outcome = await _timed_call(host, name, bad)
            if outcome["crashed"]:
                verdict, kind = "FAIL", "exception escaped ToolHost"
            elif outcome["error"]:
                verdict, kind = "PASS", "structured error"
            else:
                verdict, kind = "PASS", "normal reply (input tolerated)"
            results.append({
                "label": _variant_label(valid_args, bad),
                "args": bad,
                "outcome": kind,
                "verdict": verdict,
                "ms": outcome["ms"],
                "error": outcome["error"],
            })
        if pace:
            await asyncio.sleep(pace)
        survives = await _timed_call(host, name, valid_args)
        record = {
            "tool": name,
            "server": _server_of(name),
            "variants": results,
            "passed": sum(1 for r in results if r["verdict"] == "PASS"),
            "failed": sum(1 for r in results if r["verdict"] == "FAIL"),
            "survives": {"ok": survives["ok"], "ms": survives["ms"], "error": survives["error"]},
        }
        tools.append(record)
        mark = "✓" if record["failed"] == 0 and survives["ok"] else "✗"
        print(f"  {mark} {name:<44} {record['passed']}/{len(results)} handled  "
              f"survives={'yes' if survives['ok'] else 'NO'}")

    return {
        "tools": tools,
        "totals": {
            "tools": len(tools),
            "variants": sum(len(t["variants"]) for t in tools),
            "passed": sum(t["passed"] for t in tools),
            "failed": sum(t["failed"] for t in tools),
            "survived": sum(1 for t in tools if t["survives"]["ok"]),
        },
    }


# ── Probe 3 · unreachable server ─────────────────────────────────────────────

async def probe_unreachable() -> Dict[str, Any]:
    """A dead endpoint degrades (skip-not-fail) and never blocks past the timeout."""
    bound_ms = (GHOST_TIMEOUT + GHOST_SLACK_S) * 1000
    checks: List[Dict[str, Any]] = []
    ghost = ToolHost({"ghost": GHOST_URL}, timeout=GHOST_TIMEOUT)

    started = time.perf_counter()
    tools = await ghost.alist_tools()
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    checks.append({
        "name": "alist_tools on a dead endpoint returns []",
        "passed": tools == [] and elapsed <= bound_ms,
        "duration_ms": elapsed, "bound_ms": bound_ms,
        "detail": f"{len(tools)} tool(s) returned",
    })

    started = time.perf_counter()
    raw = await ghost.acall_tool("ghost__x", {})
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    error = _result_error(raw)
    checks.append({
        "name": "acall_tool on a dead endpoint returns an error string",
        "passed": bool(error) and elapsed <= bound_ms,
        "duration_ms": elapsed, "bound_ms": bound_ms,
        "detail": (error or raw)[:160],
    })

    weather_url = MCP_SERVERS.get("weather")
    if weather_url:
        mixed = ToolHost({"weather": weather_url, "ghost": GHOST_URL}, timeout=GHOST_TIMEOUT)
        started = time.perf_counter()
        tools = await mixed.alist_tools()
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        names = [t["function"]["name"] for t in tools]
        only_weather = bool(names) and all(n.startswith("weather" + SEP) for n in names)
        checks.append({
            "name": "mixed registry: the healthy server still lists, the dead one is skipped",
            "passed": only_weather and elapsed <= bound_ms,
            "duration_ms": elapsed, "bound_ms": bound_ms,
            "detail": f"{len(names)} tool(s), servers={sorted({_server_of(n) for n in names})}",
        })

    for check in checks:
        print(f"  {'✓' if check['passed'] else '✗'} {check['name']}  ({check['duration_ms']} ms)")

    return {
        "endpoint": GHOST_URL, "timeout_s": GHOST_TIMEOUT, "bound_ms": bound_ms,
        "checks": checks,
        "passed": sum(1 for c in checks if c["passed"]),
        "failed": sum(1 for c in checks if not c["passed"]),
    }


# ── Probe 4 · concurrency ────────────────────────────────────────────────────

async def _bounded_call(host: ToolHost, semaphore: asyncio.Semaphore,
                        name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """A timed call that first has to acquire a slot (module level: no loop closure)."""
    async with semaphore:
        return await _timed_call(host, name, args)


async def probe_concurrency(host: ToolHost, levels: Sequence[int], n_calls: int,
                            pool: Sequence[str]) -> Dict[str, Any]:
    """The same cheap calls at rising parallelism, bounded by a semaphore."""
    if not pool:
        return {"skipped": "no concurrency-pool tool available (inventory or --only)",
                "pool": [], "levels": []}

    plan = [(pool[i % len(pool)], FIXTURES[pool[i % len(pool)]]["args"]) for i in range(n_calls)]
    results: List[Dict[str, Any]] = []
    for level in levels:
        semaphore = asyncio.Semaphore(level)
        started = time.perf_counter()
        calls = await asyncio.gather(
            *[_bounded_call(host, semaphore, name, args) for name, args in plan])
        wall = round(time.perf_counter() - started, 2)
        ok = sum(1 for c in calls if c["ok"])
        record = {
            "level": level,
            "calls": len(calls),
            "ok": ok,
            "err": len(calls) - ok,
            "crashes": sum(1 for c in calls if c["crashed"]),
            "success_rate": round(ok / len(calls), 3) if calls else 0.0,
            "wall_s": wall,
            "throughput_rps": round(len(calls) / wall, 2) if wall else None,
            "errors": sorted({c["error"] for c in calls if c["error"]})[:5],
        }
        record.update(_latency_stats([c["ms"] for c in calls]))
        results.append(record)
        print(f"  {'✓' if record['err'] == 0 else '✗'} concurrency {level:>2}: "
              f"{ok}/{len(calls)} ok  p50 {record['p50_ms']} ms  p95 {record['p95_ms']} ms  "
              f"{wall} s wall")

    return {"pool": list(pool), "calls_per_level": n_calls, "levels": results}


# ── Probe 5 · orchestrator tool selection (opt-in; spends LLM tokens) ─────────

async def probe_selection() -> Dict[str, Any]:
    """Read-only prompts through the real orchestrator; check the trace's tools.

    Serialized on purpose — the A2A stack is a shared, stateful resource and this
    probe measures routing, not throughput. Each call passes ``history=[]`` and
    leaves ``user`` unset, so no real user's per-user memory is touched (the same
    choice ``evaluation/agent_under_test.py`` makes for the persona harness).
    """
    from core.orchestrator import FitDashOrchestrator   # local: costs an LLM client

    orchestrator = FitDashOrchestrator()
    prompts: List[Dict[str, Any]] = []
    for prompt, expected in SELECTION_PROMPTS:
        started = time.perf_counter()
        try:
            answer, trace = await asyncio.to_thread(orchestrator.run, prompt, [])
            failure = None
        except Exception as exc:
            answer, trace, failure = "", {}, f"{type(exc).__name__}: {exc}"
        elapsed = round((time.perf_counter() - started) * 1000, 1)

        tools = [tc.get("tool") for tc in (trace.get("tool_calls") or []) if tc.get("tool")]
        agents = [a.get("agent") for a in (trace.get("agents") or []) if a.get("agent")]
        matched = sorted({t for t in tools if any(t.startswith(p) for p in expected)})
        record = {
            "prompt": prompt,
            "expect": list(expected),
            "passed": bool(matched) and failure is None,
            "matched_tools": matched,
            "tools": tools,
            "agents": agents,
            "answer_chars": len(answer or ""),
            "ms": elapsed,
            "trace_error": trace.get("error") if isinstance(trace, dict) else None,
            "error": failure,
        }
        prompts.append(record)
        print(f"  {'✓' if record['passed'] else '✗'} {prompt[:56]:<56} "
              f"→ {', '.join(agents) or 'no specialist'}  ({len(tools)} tool call(s), "
              f"{elapsed / 1000:.1f} s)")

    passed = sum(1 for p in prompts if p["passed"])
    return {
        "enabled": True,
        "prompts": prompts,
        "passed": passed,
        "total": len(prompts),
        "pass_rate": round(passed / len(prompts), 3) if prompts else 0.0,
    }


# ── Summary ───────────────────────────────────────────────────────────────────

def build_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    """Totals, per-server rates and the error taxonomy, computed from the sections."""
    sweep = report.get("sweep") or {}
    sweep_tools: List[Dict[str, Any]] = sweep.get("tools") or []
    calls = sum(t["calls"] for t in sweep_tools)
    ok = sum(t["ok"] for t in sweep_tools)
    latencies = [ms for t in sweep_tools for ms in t.get("latencies_ms") or []]

    taxonomy: Dict[str, Dict[str, Any]] = {}

    def _tally(message: Optional[str], tool: str, source: str) -> None:
        """Count one error under its normalized pattern (raw example kept, ≤120 chars)."""
        if not message:
            return
        key = _normalize_error(message)
        entry = taxonomy.setdefault(key, {"pattern": key, "count": 0, "example": message[:120],
                                          "tools": [], "sources": []})
        entry["count"] += 1
        if tool not in entry["tools"]:
            entry["tools"].append(tool)
        if source not in entry["sources"]:
            entry["sources"].append(source)

    for tool in sweep_tools:
        for message in tool["errors"]:
            _tally(message, tool["tool"], "sweep")
    for tool in (report.get("malformed") or {}).get("tools") or []:
        for variant in tool["variants"]:
            _tally(variant["error"], tool["tool"], "malformed")          # expected by design
        _tally(tool["survives"]["error"], tool["tool"], "malformed-survives")
    for level in (report.get("concurrency") or {}).get("levels") or []:
        for message in level["errors"]:
            _tally(message, f"concurrency-{level['level']}", "concurrency")

    malformed = (report.get("malformed") or {}).get("totals") or {}
    unreachable = report.get("unreachable") or {}
    concurrency = (report.get("concurrency") or {}).get("levels") or []
    selection = report.get("selection") or {}

    return {
        "sweep": {
            "tools": len(sweep_tools),
            "calls": calls,
            "ok": ok,
            "err": calls - ok,
            "success_rate": round(ok / calls, 3) if calls else None,
            "crashes": sum(t["crashes"] for t in sweep_tools),
            "tools_fully_ok": sum(1 for t in sweep_tools if t["err"] == 0),
            "tools_degraded": [t["tool"] for t in sweep_tools if 0 < t["ok"] < t["calls"]],
            "tools_failing": [t["tool"] for t in sweep_tools if t["ok"] == 0],
            **_latency_stats(latencies),
        },
        "per_server_success_rate": {
            server: bucket["success_rate"]
            for server, bucket in sorted((sweep.get("by_server") or {}).items())
        },
        "malformed": {
            "variants": malformed.get("variants", 0),
            "handled": malformed.get("passed", 0),
            "escaped_exceptions": malformed.get("failed", 0),
            "servers_survived": f"{malformed.get('survived', 0)}/{malformed.get('tools', 0)}",
        },
        "unreachable": {
            "checks": len(unreachable.get("checks") or []),
            "passed": unreachable.get("passed", 0),
            "failed": unreachable.get("failed", 0),
        },
        "concurrency": {
            str(level["level"]): {"success_rate": level["success_rate"],
                                  "p50_ms": level["p50_ms"], "p95_ms": level["p95_ms"]}
            for level in concurrency
        },
        "selection": {
            "enabled": bool(selection.get("enabled")),
            "passed": selection.get("passed", 0),
            "total": selection.get("total", 0),
            "pass_rate": selection.get("pass_rate"),
        },
        "error_taxonomy": sorted(taxonomy.values(), key=lambda e: (-e["count"], e["pattern"]))[:10],
    }


def _rule(title: str, width: int = 78) -> str:
    """``── Title ─────…`` padded to a fixed width."""
    head = f"── {title} "
    return head + "─" * max(3, width - len(head))


def print_summary(report: Dict[str, Any]) -> None:
    """A compact human table on stdout — the JSON stays the source of truth."""
    summary = report["summary"]
    meta = report["meta"]

    print("\n" + "═" * 78)
    print(f"  ROBUSTNESS SUMMARY · {meta['generated_at_utc']} · {meta['duration_s']} s")
    print("═" * 78)

    inventory = meta["inventory"]
    print(f"\n  Inventory: {inventory['total_tools']} live tool(s) across "
          f"{len(inventory['by_server'])} server(s) · {meta['fixtured']} fixtured · "
          f"{meta['selected']} selected · {meta['skipped']} skipped")

    sweep = report.get("sweep") or {}
    if sweep.get("enabled"):
        stats = summary["sweep"]
        print("\n" + _rule(f"Sweep (repeat {sweep['repeat']})"))
        print(f"  {'server':<14}{'tools':>6}{'calls':>7}{'ok':>6}{'err':>6}"
              f"{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}")
        for server, bucket in sorted((sweep.get("by_server") or {}).items()):
            print(f"  {server:<14}{bucket['tools']:>6}{bucket['calls']:>7}{bucket['ok']:>6}"
                  f"{bucket['err']:>6}{_num(bucket['p50_ms']):>10}{_num(bucket['p95_ms']):>10}"
                  f"{_num(bucket['max_ms']):>10}")
        print(f"  {'TOTAL':<14}{stats['tools']:>6}{stats['calls']:>7}{stats['ok']:>6}"
              f"{stats['err']:>6}{_num(stats['p50_ms']):>10}{_num(stats['p95_ms']):>10}"
              f"{_num(stats['max_ms']):>10}")
        rate = stats["success_rate"]
        print(f"  success rate: {'n/a' if rate is None else f'{rate * 100:.1f}%'}"
              f" · crashes: {stats['crashes']}")
        if stats["tools_failing"]:
            print(f"  ✗ failing:  {', '.join(stats['tools_failing'])}")
        if stats["tools_degraded"]:
            print(f"  ⚠ flaky:    {', '.join(stats['tools_degraded'])}")

    malformed = summary["malformed"]
    print("\n" + _rule("Malformed input"))
    print(f"  {malformed['handled']}/{malformed['variants']} hostile variant(s) handled as "
          f"structured errors or tolerated replies · "
          f"{malformed['escaped_exceptions']} exception(s) escaped ToolHost")
    print(f"  servers still answering a valid call afterwards: {malformed['servers_survived']}")

    unreachable = summary["unreachable"]
    print("\n" + _rule("Unreachable server"))
    print(f"  {unreachable['passed']}/{unreachable['checks']} degradation check(s) passed")

    if summary["concurrency"]:
        print("\n" + _rule("Concurrency"))
        print(f"  {'level':<8}{'success':>10}{'p50 ms':>10}{'p95 ms':>10}")
        for level, values in summary["concurrency"].items():
            print(f"  {level:<8}{values['success_rate'] * 100:>9.1f}%"
                  f"{_num(values['p50_ms']):>10}{_num(values['p95_ms']):>10}")

    selection = summary["selection"]
    if selection["enabled"]:
        print("\n" + _rule("Orchestrator tool selection"))
        print(f"  {selection['passed']}/{selection['total']} prompt(s) reached the expected server")
        for prompt in (report["selection"]["prompts"] or []):
            if not prompt["passed"]:
                print(f"    ✗ {prompt['prompt'][:60]} (expected {'/'.join(prompt['expect'])}, "
                      f"got {', '.join(prompt['tools']) or 'no tool call'})")

    if summary["error_taxonomy"]:
        print("\n" + _rule(f"Error taxonomy (top {len(summary['error_taxonomy'])})"))
        for entry in summary["error_taxonomy"]:
            example = " ".join(entry["example"].split())
            print(f"  {entry['count']:>3}× [{'/'.join(entry['sources'])}] {example[:88]}")

    print()


def _num(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.0f}"


# ── Orchestration ─────────────────────────────────────────────────────────────

async def run_all(args: argparse.Namespace) -> Dict[str, Any]:
    started = time.perf_counter()
    host = ToolHost()

    print(f"▶ Discovering tools from {len(host.servers)} configured MCP server(s) …")
    inventory = await host.alist_tools()
    live_names = [t["function"]["name"] for t in inventory]
    by_server: Dict[str, int] = {}
    for name in live_names:
        by_server[_server_of(name)] = by_server.get(_server_of(name), 0) + 1
    unreachable_servers = sorted(set(host.servers) - set(by_server))
    print(f"  {len(live_names)} tool(s) live: "
          + ", ".join(f"{s}×{n}" for s, n in sorted(by_server.items()))
          + (f"  ·  not reachable: {', '.join(unreachable_servers)}" if unreachable_servers else ""))

    only = [f for f in (args.only or "").split(",") if f.strip()]
    targets, skipped = [], []
    for name in live_names:
        veto = write_path_reason(name)
        if veto or name not in FIXTURES:
            skipped.append({"tool": name, "reason": veto or skip_reason(name)})
        elif _selected(name, only):
            targets.append(name)
        else:
            skipped.append({"tool": name, "reason": "filtered out by --only"})
    targets.sort()
    missing = sorted(set(FIXTURES) - set(live_names))
    print(f"  {len(targets)} tool(s) selected · {len(skipped)} skipped"
          + (f" · {len(missing)} fixtured tool(s) not live" if missing else ""))

    report: Dict[str, Any] = {
        "meta": {
            "generated_at_utc": _utc_now(),
            "python": sys.version.split()[0],
            "servers": dict(host.servers),
            "args": {
                "repeat": args.repeat, "concurrency": args.concurrency,
                "conc_calls": args.conc_calls, "only": args.only,
                "skip_sweep": args.skip_sweep, "with_orchestrator": args.with_orchestrator,
            },
            "inventory": {
                "total_tools": len(live_names),
                "by_server": dict(sorted(by_server.items())),
                "unreachable_servers": unreachable_servers,
            },
            "fixtured": len(FIXTURES),
            "selected": len(targets),
            "skipped": len(skipped),
            "fixtured_not_live": missing,
            "skip_list": sorted(skipped, key=lambda s: s["tool"]),
            "known_skip_entries": len(SKIP),
        },
    }

    if args.skip_sweep:
        print("\n── SWEEP · skipped (--skip-sweep) ──")
        report["sweep"] = {"enabled": False, "repeat": args.repeat, "tools": [],
                           "by_server": {}, "reason": "--skip-sweep"}
    else:
        print(f"\n── SWEEP · {len(targets)} tool(s) × {args.repeat} call(s) ──")
        report["sweep"] = await probe_sweep(host, targets, args.repeat)

    malformed_targets = [t for t in targets if malformed_variants(t)]
    print(f"\n── MALFORMED · {len(malformed_targets)} tool(s) ──")
    report["malformed"] = await probe_malformed(host, malformed_targets)

    print("\n── UNREACHABLE ──")
    report["unreachable"] = await probe_unreachable()

    levels = sorted({int(x) for x in args.concurrency.split(",") if x.strip()})
    pool = [t for t in CONCURRENCY_POOL if t in live_names and _selected(t, only)]
    print(f"\n── CONCURRENCY · levels {levels} × {args.conc_calls} call(s) ──")
    report["concurrency"] = await probe_concurrency(host, levels, args.conc_calls, pool)

    if args.with_orchestrator:
        print(f"\n── ORCHESTRATOR SELECTION · {len(SELECTION_PROMPTS)} read-only prompt(s) ──")
        report["selection"] = await probe_selection()
    else:
        report["selection"] = {"enabled": False,
                               "reason": "not requested (--with-orchestrator)",
                               "prompts": [], "passed": 0, "total": 0}

    report["meta"]["duration_s"] = round(time.perf_counter() - started, 1)
    report["summary"] = build_summary(report)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Technical-robustness probes for the live Training Copilot MCP stack.")
    parser.add_argument("--repeat", type=int, default=5,
                        help="valid calls per tool in the sweep (default 5)")
    parser.add_argument("--concurrency", default="1,4,8",
                        help="comma-separated parallelism levels (default 1,4,8)")
    parser.add_argument("--conc-calls", type=int, default=16,
                        help="calls fired per concurrency level (default 16)")
    parser.add_argument("--only", default="",
                        help="comma-separated substrings; only matching tools are exercised")
    parser.add_argument("--skip-sweep", action="store_true",
                        help="skip probe 1 (the latency/error sweep)")
    parser.add_argument("--with-orchestrator", action="store_true",
                        help="also run the tool-selection probe (spends LLM tokens)")
    parser.add_argument("--out", default=None,
                        help="JSON output path (default evaluation/reports/robustness-<ts>.json)")
    args = parser.parse_args(argv)

    if args.repeat < 1:
        sys.exit("✗ --repeat must be ≥ 1")
    if args.conc_calls < 1:
        sys.exit("✗ --conc-calls must be ≥ 1")

    report = asyncio.run(run_all(args))

    out = Path(args.out) if args.out else REPORTS_DIR / f"robustness-{_timestamp()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print_summary(report)
    print(f"✓ Done. Report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
