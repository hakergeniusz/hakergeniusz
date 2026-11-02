#!/usr/bin/env python3
"""Render a GitHub-contribution-style calendar for coding agent token usage.

Reads ~/.pi/agent/token-usage.json (maintained by the Pi agent) and merges in
Claude Code's daily usage extracted from ~/.local/share/claude/ (see
claude_usage.py), ZCode's daily usage from its local telemetry db (see
zcode_usage.py), Qwen Code's daily usage from its chat transcripts (see
qwen_usage.py), and Gemini CLI's daily usage from its session transcripts
(see gemini_usage.py), then writes a self-contained SVG with light/dark
support.

Usage:
    python3 token-heatmap.py --output token-heatmap.svg
    python3 token-heatmap.py --today 2026-10-29 --theme dark --output /tmp/preview.svg
    python3 token-heatmap.py --no-claude --no-zcode --no-qwen --no-gemini   # Pi data only

CI modes (used by the profile-repo workflow, where no local telemetry exists):
    python3 token-heatmap.py --emit-series daily.json      # local: collect + dump
    python3 token-heatmap.py --series daily.json --output token-heatmap.svg
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import sys

WEEKS = 53
CELL = 10
GAP = 2
PITCH = CELL + GAP
PAD = 22
GRID_X = 48
GRID_Y = 134
CARD_W = 700
CARD_H = 264

# levels are fixed token-count buckets so the map is stable over time
LEVELS = (20_000_000, 100_000_000, 400_000_000, 1_000_000_000)

DARK = {
    "card": "#0d1117",
    "border": "#21262d",
    "fg": "#e6edf3",
    "muted": "#7d8590",
    "nodata": "#11161d",
    "nodata_stroke": "#11161d",
    "c0": "#161b22",
    "c1": "#3d1a06",
    "c2": "#7a2c0b",
    "c3": "#c2410c",
    "c4": "#f97316",
    "c5": "#fde047",
    "record": "#fde047",
    "divider": "#21262d",
}

LIGHT = {
    "card": "#ffffff",
    "border": "#d0d7de",
    "fg": "#1f2328",
    "muted": "#656d76",
    "nodata": "#f1f3f6",
    "nodata_stroke": "#f1f3f6",
    "c0": "#eff2f5",
    "c1": "#ffe3c2",
    "c2": "#ffc078",
    "c3": "#fd8a3b",
    "c4": "#e8590c",
    "c5": "#a3340b",
    "record": "#7c2d12",
    "divider": "#d8dee4",
}


def compact(n: float) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(int(n))


def level(tokens: float) -> int:
    if tokens <= 0:
        return 0
    for i, edge in enumerate(LEVELS, start=1):
        if tokens < edge:
            return i
    return 5


def palette_rules(p: dict) -> str:
    return (
        f".card{{fill:{p['card']};stroke:{p['border']};stroke-width:1}}"
        f".fg{{fill:{p['fg']}}}"
        f".muted{{fill:{p['muted']}}}"
        f".divider{{stroke:{p['divider']};stroke-width:1}}"
        f".nodata{{fill:{p['nodata']};stroke:{p['nodata_stroke']};stroke-width:1}}"
        f".c0{{fill:{p['c0']}}}.c1{{fill:{p['c1']}}}.c2{{fill:{p['c2']}}}"
        f".c3{{fill:{p['c3']}}}.c4{{fill:{p['c4']}}}.c5{{fill:{p['c5']}}}"
        f".record{{stroke:{p['record']};stroke-width:1.5}}"
        f".glow{{fill:{p['record']}}}"
    )


def css(p: dict, auto: bool) -> str:
    base = (
        "text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}"
        ".mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace}"
        + palette_rules(p)
        + ".glow{opacity:.12;animation:heatpulse 3s ease-in-out infinite}"
        "@keyframes heatpulse{0%,100%{opacity:.06}50%{opacity:.34}}"
        "@media (prefers-reduced-motion:reduce){.glow{animation:none;opacity:.12}}"
    )
    if not auto:
        return base
    # GitHub flips prefers-color-scheme with the reader's theme.
    return base + "@media (prefers-color-scheme: light){" + palette_rules(LIGHT) + "}"


def merge_claude_usage(data: dict, claude_dir: str | None = None) -> None:
    """Fold Claude Code daily usage into the Pi byDay/total structures.

    claude_dir=None uses the default set: the local dir plus archive dirs
    from other machines (see claude_usage.py).
    """
    import claude_usage  # imported lazily: --series mode runs without collectors

    try:
        if claude_dir:
            claude = claude_usage.claude_daily(claude_dir)
        else:
            claude = claude_usage.claude_daily_all()
    except Exception as exc:  # claude data is optional; never fail the render
        print(f"claude usage unavailable ({exc}); using Pi data only", file=sys.stderr)
        return
    by_day = data.setdefault("byDay", {})
    for date, usage in claude.items():
        day = by_day.setdefault(date, {})
        day["total"] = day.get("total", 0) + usage["total"]
        day["requests"] = day.get("requests", 0) + usage["requests"]
    total = data.get("total")
    if isinstance(total, dict):
        total["requests"] = total.get("requests", 0) + sum(u["requests"] for u in claude.values())


def merge_zcode_usage(data: dict, db_path: str | None = None) -> None:
    """Fold ZCode daily usage into the Pi byDay/total structures.

    db_path=None uses the default set: the live db plus archive dbs from
    previous installs (see zcode_usage.py).
    """
    import zcode_usage  # imported lazily: --series mode runs without collectors

    try:
        if db_path:
            zcode = zcode_usage.zcode_daily(db_path)
        else:
            zcode = zcode_usage.zcode_daily_all()
    except Exception as exc:  # zcode data is optional; never fail the render
        print(f"zcode usage unavailable ({exc}); continuing without it", file=sys.stderr)
        return
    by_day = data.setdefault("byDay", {})
    for date, usage in zcode.items():
        day = by_day.setdefault(date, {})
        day["total"] = day.get("total", 0) + usage["total"]
        day["requests"] = day.get("requests", 0) + usage["requests"]
    total = data.get("total")
    if isinstance(total, dict):
        total["requests"] = total.get("requests", 0) + sum(u["requests"] for u in zcode.values())


def merge_qwen_usage(data: dict, qwen_root: str | None = None) -> None:
    """Fold Qwen Code daily usage into the Pi byDay/total structures.

    qwen_root=None uses the default set: the live home plus archive homes from
    other installs (see qwen_usage.py).
    """
    import qwen_usage  # imported lazily: --series mode runs without collectors

    try:
        if qwen_root:
            qwen = qwen_usage.qwen_daily(qwen_root)
        else:
            qwen = qwen_usage.qwen_daily_all()
    except Exception as exc:  # qwen data is optional; never fail the render
        print(f"qwen usage unavailable ({exc}); continuing without it", file=sys.stderr)
        return
    by_day = data.setdefault("byDay", {})
    for date, usage in qwen.items():
        day = by_day.setdefault(date, {})
        day["total"] = day.get("total", 0) + usage["total"]
        day["requests"] = day.get("requests", 0) + usage["requests"]
    total = data.get("total")
    if isinstance(total, dict):
        total["requests"] = total.get("requests", 0) + sum(u["requests"] for u in qwen.values())


def merge_gemini_usage(data: dict, gemini_root: str | None = None) -> None:
    """Fold Gemini CLI daily usage into the Pi byDay/total structures.

    gemini_root=None uses the default set: the live home plus archive homes
    from other installs, with one shared message-id dedup set (see
    gemini_usage.py).
    """
    import gemini_usage  # imported lazily: --series mode runs without collectors

    try:
        if gemini_root:
            gemini = gemini_usage.gemini_daily(gemini_root)
        else:
            gemini = gemini_usage.gemini_daily_all()
    except Exception as exc:  # gemini data is optional; never fail the render
        print(f"gemini usage unavailable ({exc}); continuing without it", file=sys.stderr)
        return
    by_day = data.setdefault("byDay", {})
    for date, usage in gemini.items():
        day = by_day.setdefault(date, {})
        day["total"] = day.get("total", 0) + usage["total"]
        day["requests"] = day.get("requests", 0) + usage["requests"]
    total = data.get("total")
    if isinstance(total, dict):
        total["requests"] = total.get("requests", 0) + sum(u["requests"] for u in gemini.values())


def build(data: dict, today: dt.date, theme: str) -> str:
    by_day: dict[str, dict] = data.get("byDay", {})
    days = {dt.date.fromisoformat(k): v for k, v in by_day.items() if v.get("total", 0) >= 0}
    first_day = min(days) if days else today

    total = sum(v.get("total", 0) for v in days.values())
    requests = sum(v.get("requests", 0) for v in days.values())
    requests = data.get("total", {}).get("requests", requests) or requests
    active = sum(1 for v in days.values() if v.get("total", 0) > 0)
    # recent burn rate: last 30 days, counted over days with any usage
    recent = [days[d] for d in (today - dt.timedelta(days=i) for i in range(30)) if d in days]
    recent_active = sum(1 for v in recent if v.get("total", 0) > 0)
    avg = sum(v.get("total", 0) for v in recent) / recent_active if recent_active else 0
    record_day, record = max(days.items(), key=lambda kv: kv[1].get("total", 0)) if days else (today, {"total": 0, "requests": 0})

    # streak ending today (or last recorded day if today is missing)
    streak = 0
    cursor = today if today in days else max(days) if days else today
    while cursor in days and days[cursor].get("total", 0) > 0:
        streak += 1
        cursor -= dt.timedelta(days=1)

    updated = data.get("updatedAt")
    if isinstance(updated, (int, float)) and updated > 0:
        updated_txt = dt.datetime.fromtimestamp(updated / 1000, dt.timezone.utc).strftime("%H:%M UTC")
    else:
        updated_txt = ""

    # calendar: 53 columns of weeks starting on Monday, ending with the current week
    start = today - dt.timedelta(days=(WEEKS - 1) * 7 + today.weekday())

    p = DARK if theme != "light" else LIGHT
    auto = theme == "auto"

    cells: list[str] = []
    grid_marks: list[str] = []

    for col in range(WEEKS):
        for row in range(7):
            d = start + dt.timedelta(days=col * 7 + row)
            if d > today:
                continue
            x = GRID_X + col * PITCH
            y = GRID_Y + row * PITCH
            if d < first_day or d not in days:
                cls, label = "nodata", "no data"
            else:
                tokens = days[d].get("total", 0)
                reqs = days[d].get("requests", 0)
                cls = f"c{level(tokens)}"
                reqs_txt = f", {reqs:,} requests" if reqs else ""
                label = f"{d:%b} {d.day}, {d.year}: {compact(tokens)} tokens{reqs_txt}"
                if d == record_day and tokens > 0:
                    cls += " record"
                    grid_marks.append(
                        f'<rect class="glow" x="{x - 2}" y="{y - 2}" width="{CELL + 4}" '
                        f'height="{CELL + 4}" rx="3"/>'
                    )
            cells.append(
                f'<rect class="{cls}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2">'
                f"<title>{html.escape(label)}</title></rect>"
            )

    # month labels on the first week of each month
    months: list[str] = []
    last_month = None
    last_col = -99
    for col in range(WEEKS):
        d = start + dt.timedelta(days=col * 7)
        if d.month != last_month and col - last_col >= 3:
            months.append(
                f'<text class="mono muted" x="{GRID_X + col * PITCH}" y="{GRID_Y - 8}" '
                f'font-size="9.5">{d:%b}</text>'
            )
            last_month = d.month
            last_col = col

    # weekday labels
    weekday_labels = []
    for row, name in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
        y = GRID_Y + row * PITCH + CELL - 1
        weekday_labels.append(
            f'<text class="mono muted" x="{PAD}" y="{y}" font-size="9.5">{name}</text>'
        )

    # legend, right-aligned: LESS [ ][ ][ ][ ][ ] MORE
    legend_y = CARD_H - 36
    more_w, less_w, cells_w = 27, 25, 5 * CELL + 4 * 2
    legend_x = CARD_W - PAD - more_w - 7 - cells_w
    legend = []
    for i in range(5):
        legend.append(
            f'<rect class="c{i + 1}" x="{legend_x + i * (CELL + 2)}" y="{legend_y}" '
            f'width="{CELL}" height="{CELL}" rx="2"/>'
        )
    legend_txt = (
        f'<text class="mono muted" x="{legend_x - 7}" y="{legend_y + CELL - 1}" '
        f'font-size="9" text-anchor="end">LESS</text>'
        f'<text class="mono muted" x="{legend_x + cells_w + 7}" y="{legend_y + CELL - 1}" '
        f'font-size="9">MORE</text>'
    )

    streak_label = "DAY STREAK"
    record_label = f"RECORD DAY · {record_day:%b} {record_day.day}".upper()

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_W}" height="{CARD_H}" viewBox="0 0 {CARD_W} {CARD_H}" role="img" aria-label="Coding agent token usage (Pi + Claude + ZCode + Qwen + Gemini), last 12 months">
<title>Coding agent token usage (Pi + Claude + ZCode + Qwen + Gemini) — {compact(total)} tokens over {active} days</title>
<defs>
<linearGradient id="flare" x1="0" y1="0" x2="1" y2="0">
<stop offset="0" stop-color="#fb923c"/><stop offset=".55" stop-color="#f97316"/><stop offset="1" stop-color="#fde047"/>
</linearGradient>
<style>{css(p, auto)}</style>
</defs>
<rect class="card" x="0.5" y="0.5" width="{CARD_W - 1}" height="{CARD_H - 1}" rx="14"/>
<g class="mono">
<rect x="{PAD}" y="18" width="8" height="8" rx="2" fill="url(#flare)"/>
<text class="muted" x="{PAD + 14}" y="26" font-size="10.5" letter-spacing="2">PI + CLAUDE + ZCODE + QWEN + GEMINI // TOKEN BURN</text>
<text class="muted" x="{CARD_W - PAD}" y="26" font-size="9.5" text-anchor="end" letter-spacing="1.2">LAST 12 MONTHS{f"  ·  UPDATED {updated_txt}" if updated_txt else ""}</text>
</g>
<g>
<text class="fg" x="{PAD}" y="78" font-size="32" font-weight="700" fill="url(#flare)">{compact(total)}</text>
<text class="mono muted" x="{PAD}" y="96" font-size="9" letter-spacing="1.4">TOTAL TOKENS</text>
<text class="fg" x="{PAD + 168}" y="78" font-size="32" font-weight="700">{compact(record.get("total", 0))}</text>
<text class="mono muted" x="{PAD + 168}" y="96" font-size="9" letter-spacing="1.4">{record_label}</text>
<text class="fg" x="{PAD + 336}" y="78" font-size="32" font-weight="700">{streak}</text>
<text class="mono muted" x="{PAD + 336}" y="96" font-size="9" letter-spacing="1.4">{streak_label}</text>
<text class="fg" x="{PAD + 504}" y="78" font-size="32" font-weight="700">{compact(avg)}</text>
<text class="mono muted" x="{PAD + 504}" y="96" font-size="9" letter-spacing="1.4">AVG / DAY · 30D</text>
</g>
<line class="divider" x1="{PAD}" y1="110" x2="{CARD_W - PAD}" y2="110"/>
<g class="mono">
{"".join(months)}
{"".join(weekday_labels)}
</g>
<g>
{"".join(grid_marks)}
{"".join(cells)}
</g>
<g class="mono">
<text class="muted" x="{PAD}" y="{CARD_H - 27}" font-size="9">{active} active days · {requests:,} requests · tracking since {first_day:%Y-%m-%d}</text>
{"".join(legend)}
{legend_txt}
</g>
</svg>
"""


def collect(args: argparse.Namespace) -> dict:
    """Load Pi usage and fold in the optional external collectors."""
    with open(args.input, encoding="utf-8") as fh:
        data = json.load(fh)

    if not args.no_claude:
        merge_claude_usage(data, args.claude_input)
    if not args.no_zcode:
        merge_zcode_usage(data, args.zcode_input)
    if not args.no_qwen:
        merge_qwen_usage(data, args.qwen_input)
    if not args.no_gemini:
        merge_gemini_usage(data, args.gemini_input)
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.expanduser("~/.pi/agent/token-usage.json"))
    ap.add_argument(
        "--claude-input",
        default=None,
        help="Claude Code data dir (default: local dir + ~/.local/share/claude-archive* dirs)",
    )
    ap.add_argument("--no-claude", action="store_true", help="render Pi data only")
    ap.add_argument(
        "--zcode-input",
        default=None,
        help="ZCode sqlite db (default: live db + ~/.zcode/cli/db/db-prev-*.sqlite archives)",
    )
    ap.add_argument("--no-zcode", action="store_true", help="skip ZCode usage")
    ap.add_argument(
        "--qwen-input",
        default=None,
        help="Qwen Code home (default: live home + ~/.local/share/qwen-archive*/.qwen archives)",
    )
    ap.add_argument("--no-qwen", action="store_true", help="skip Qwen Code usage")
    ap.add_argument(
        "--gemini-input",
        default=None,
        help="Gemini CLI tmp home (default: live home + ~/.local/share/gemini-archive*/.gemini/tmp archives)",
    )
    ap.add_argument("--no-gemini", action="store_true", help="skip Gemini CLI usage")
    ap.add_argument(
        "--emit-series",
        metavar="PATH",
        default=None,
        help="collect locally and dump the merged daily series as JSON (no SVG)",
    )
    ap.add_argument(
        "--series",
        metavar="PATH",
        default=None,
        help="render from a pre-collected series JSON instead of local telemetry",
    )
    ap.add_argument("--output", default="token-heatmap.svg")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--theme", choices=("auto", "dark", "light"), default="auto")
    args = ap.parse_args()

    if args.emit_series and args.series:
        ap.error("--emit-series and --series are mutually exclusive")

    if args.emit_series:
        data = collect(args)
        # slim series: only the fields build() reads — small enough to commit
        # to the public profile repo on every change
        series = {
            "byDay": {
                day: {"total": day_usage.get("total", 0), "requests": day_usage.get("requests", 0)}
                for day, day_usage in data.get("byDay", {}).items()
            },
            "total": {"requests": data.get("total", {}).get("requests", 0)},
            "updatedAt": data.get("updatedAt"),
        }
        payload = json.dumps(series, separators=(",", ":"), sort_keys=True)
        with open(args.emit_series, "w", encoding="utf-8") as fh:
            fh.write(payload)
        print(f"wrote {args.emit_series} ({len(payload):,} bytes)")
        return 0

    if args.series:
        with open(args.series, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not isinstance(data.get("byDay"), dict):
            print(f"{args.series}: expected a series JSON with a byDay object", file=sys.stderr)
            return 1
    else:
        data = collect(args)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    svg = build(data, today, args.theme)

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"wrote {args.output} ({len(svg):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
