"""Timing and memory for the render spike. Runs unchanged inside the container (adds cgroup numbers).

    uv run python bench/measure.py [--runs 10] [--out out/bench.json]

Scenarios (12-slide sample deck, one Mermaid diagram):
  cold_first_job    first job in a fresh process: Playwright driver + Chromium + render
  per_job           production model: driver + Chromium launched per job, N sequential jobs
  shared_driver     long-lived Playwright driver, Chromium per job
  warm_render       one Chromium, N renders (pure render cost)
  concurrent_3      3 jobs at once behind semaphore(2), per-job lifecycle
  concurrent_6      6 jobs at once behind semaphore(2), per-job lifecycle (steady-state peak)
  og_card           OG card (1200x630) per job, warm
  mermaid_only      one diagram to sanitised SVG + 2x PNG, warm
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".ms-playwright"))

import psutil  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from render import OgSpec, RenderLimiter, RenderSession, render_carousel, render_carousel_in  # noqa: E402
from render.carousel import parse_deck  # noqa: E402
from render.mermaid import render_mermaid  # noqa: E402
from render.og import render_og_card_in  # noqa: E402
from render.spec import DeckSpec  # noqa: E402
from render.telemetry import MemorySampler  # noqa: E402

MiB = 1024 * 1024


def stats(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))]
    return {
        "n": len(values),
        "min_s": round(ordered[0], 3),
        "median_s": round(statistics.median(ordered), 3),
        "p90_s": round(p90, 3),
        "max_s": round(ordered[-1], 3),
    }


def stage_medians(timings: list[dict[str, float]]) -> dict[str, float]:
    keys = sorted({k for t in timings for k in t})
    return {k: round(statistics.median(t.get(k, 0.0) for t in timings), 3) for k in keys}


async def one_job(deck: DeckSpec, playwright: Any = None) -> tuple[float, dict[str, float], int]:
    t0 = time.perf_counter()
    async with RenderSession(playwright=playwright) as session:
        out = await render_carousel_in(session, deck)
    dt = time.perf_counter() - t0
    stages = dict(session.timer.stages)
    stages["teardown"] = dt - session.timer.total
    return dt, stages, len(out.pdf)


async def main(runs: int, spec_json: str) -> dict[str, Any]:
    deck = parse_deck(spec_json)
    results: dict[str, Any] = {
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpus": os.cpu_count(),
            "ram_gib": round(psutil.virtual_memory().total / 1024**3, 1),
            "python": sys.version.split()[0],
        },
        "deck": {"slides": len(deck.slides), "diagrams": sum(s.type == "diagram" for s in deck.slides)},
    }
    gc.collect()
    results["python_baseline_rss_mib"] = round(psutil.Process().memory_info().rss / MiB, 1)

    with MemorySampler() as mem:
        dt, stages, pdf_bytes = await one_job(deck)
    results["cold_first_job"] = {"wall_s": round(dt, 3), "stages_s": stages, "memory": mem.peaks.as_mib()}
    results["pdf_bytes"] = pdf_bytes

    times, timings = [], []
    with MemorySampler() as mem:
        for _ in range(runs):
            dt, stages, _ = await one_job(deck)
            times.append(dt)
            timings.append(stages)
    results["per_job"] = {
        **stats(times),
        "stages_median_s": stage_medians(timings),
        "memory": mem.peaks.as_mib(),
    }

    times, timings = [], []
    async with async_playwright() as pw:
        with MemorySampler() as mem:
            for _ in range(runs):
                dt, stages, _ = await one_job(deck, playwright=pw)
                times.append(dt)
                timings.append(stages)
    results["shared_driver"] = {
        **stats(times),
        "stages_median_s": stage_medians(timings),
        "memory": mem.peaks.as_mib(),
    }

    times = []
    async with RenderSession() as session:
        with MemorySampler() as mem:
            for _ in range(runs):
                t0 = time.perf_counter()
                await render_carousel_in(session, deck)
                times.append(time.perf_counter() - t0)
        og_times = []
        for _ in range(runs):
            t0 = time.perf_counter()
            await render_og_card_in(session, OgSpec(title="Give every AI agent its own identity"))
            og_times.append(time.perf_counter() - t0)
        mm_times = []
        diagram = next(s for s in deck.slides if s.type == "diagram").mermaid  # type: ignore[union-attr]
        for _ in range(runs):
            t0 = time.perf_counter()
            await render_mermaid(session, diagram, png=True)
            mm_times.append(time.perf_counter() - t0)
    results["warm_render"] = {**stats(times), "memory": mem.peaks.as_mib()}
    results["og_card"] = stats(og_times)
    results["mermaid_only"] = stats(mm_times)

    for n in (3, 6):
        limiter = RenderLimiter(2)
        with MemorySampler() as mem:
            t0 = time.perf_counter()
            outs = await asyncio.gather(*(render_carousel(deck, limiter=limiter) for _ in range(n)))
            wall = time.perf_counter() - t0
        assert len({o.pdf for o in outs}) == 1
        results[f"concurrent_{n}"] = {
            "jobs": n,
            "semaphore": 2,
            "max_in_flight": limiter.max_in_flight,
            "wall_s": round(wall, 3),
            "per_deck_s": round(wall / n, 3),
            "memory": mem.peaks.as_mib(),
        }

    return results


def summarise(r: dict[str, Any]) -> None:
    print(json.dumps(r["host"]))
    print(f"python baseline RSS: {r['python_baseline_rss_mib']} MiB   pdf: {r['pdf_bytes']} bytes")
    c = r["cold_first_job"]
    print(f"cold first job: {c['wall_s']}s  stages={c['stages_s']}")
    for key in ("per_job", "shared_driver", "warm_render", "og_card", "mermaid_only"):
        v = r[key]
        line = f"{key:14s} median {v['median_s']}s  p90 {v['p90_s']}s  (n={v['n']})"
        if "memory" in v:
            m = v["memory"]
            line += (
                f"  peak tree RSS {m['tree_rss_mib']} MiB; chromium RSS {m['chromium_rss_mib']}"
                f" / unique {m['chromium_unique_mib']}; driver RSS {m['driver_rss_mib']}"
                f" / unique {m['driver_unique_mib']}"
            )
        print(line)
        if "stages_median_s" in v:
            print(f"{'':14s} stages {v['stages_median_s']}")
    for key in ("concurrent_3", "concurrent_6"):
        v = r[key]
        m = v["memory"]
        print(
            f"{key:14s} wall {v['wall_s']}s ({v['per_deck_s']}s/deck, max in flight {v['max_in_flight']})"
            f"  peak tree RSS {m['tree_rss_mib']} MiB  chromium RSS {m['chromium_rss_mib']} / unique "
            f"{m['chromium_unique_mib']}  python {m['python_rss_mib']}  driver {m['driver_rss_mib']}"
            f" / unique {m['driver_unique_mib']}"
            f"  procs {m['chromium_processes']}  by kind {m['by_kind_rss_mib']}"
            + (f"  cgroup peak {m['cgroup_peak_mib']}" if m["cgroup_peak_mib"] is not None else "")
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "bench.json")
    args = ap.parse_args()
    spec = (ROOT / "tests/fixtures/sample_deck.json").read_text(encoding="utf-8")
    results = asyncio.run(main(args.runs, spec))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    summarise(results)
