#!/usr/bin/env python3
"""Generate a GitHub-style contribution heatmap as a repository-local SVG."""

from __future__ import annotations

import html
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

GRAPHQL_URL = "https://api.github.com/graphql"
USERNAME = os.environ.get("GITHUB_USERNAME", "dporkka")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
OUTPUT = Path(os.environ.get("GRAPH_OUTPUT", "images/contribution-graph.svg"))

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            contributionCount
            contributionLevel
            date
            weekday
          }
        }
      }
    }
  }
}
"""

LEVEL_CLASS = {
    "NONE": "level-0",
    "FIRST_QUARTILE": "level-1",
    "SECOND_QUARTILE": "level-2",
    "THIRD_QUARTILE": "level-3",
    "FOURTH_QUARTILE": "level-4",
}


def fetch_calendar() -> dict:
    if not TOKEN:
        raise RuntimeError("GH_TOKEN or GITHUB_TOKEN is required")

    body = json.dumps({"query": QUERY, "variables": {"login": USERNAME}}).encode()
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "dporkka-profile-contribution-graph",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL request failed: HTTP {exc.code}: {detail}") from exc

    if payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL returned errors: {payload['errors']}")

    user = payload.get("data", {}).get("user")
    if not user:
        raise RuntimeError(f"GitHub user {USERNAME!r} was not found")

    return user["contributionsCollection"]["contributionCalendar"]


def render_svg(calendar: dict) -> str:
    weeks = calendar["weeks"]
    total = calendar["totalContributions"]

    cell = 10
    gap = 3
    step = cell + gap
    left = 38
    top = 30
    right = 18
    bottom = 42
    width = left + max(1, len(weeks)) * step + right
    height = top + 7 * step + bottom

    month_labels: list[tuple[int, str]] = []
    previous_month = None
    last_label_x = -999
    rects: list[str] = []

    for week_index, week in enumerate(weeks):
        x = left + week_index * step
        days = week.get("contributionDays", [])

        if days:
            first = date.fromisoformat(days[0]["date"])
            if first.month != previous_month and x - last_label_x >= 34:
                month_labels.append((x, first.strftime("%b")))
                last_label_x = x
            previous_month = first.month

        for day in days:
            weekday = int(day["weekday"])
            y = top + weekday * step
            count = int(day["contributionCount"])
            level = LEVEL_CLASS.get(day["contributionLevel"], "level-0")
            day_text = html.escape(day["date"])
            noun = "contribution" if count == 1 else "contributions"
            rects.append(
                f'<rect class="day {level}" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2">'
                f'<title>{count} {noun} on {day_text}</title></rect>'
            )

    months = "".join(
        f'<text class="label" x="{x}" y="16">{html.escape(label)}</text>'
        for x, label in month_labels
    )

    weekdays = "".join(
        f'<text class="label weekday" x="0" y="{top + row * step + 9}">{label}</text>'
        for row, label in ((1, "Mon"), (3, "Wed"), (5, "Fri"))
    )

    legend_x = width - 5 * step - 72
    legend_y = height - 24
    legend = [f'<text class="label" x="{legend_x - 28}" y="{legend_y + 9}">Less</text>']
    for index in range(5):
        legend.append(
            f'<rect class="day level-{index}" x="{legend_x + index * step}" y="{legend_y}" '
            f'width="{cell}" height="{cell}" rx="2" />'
        )
    legend.append(
        f'<text class="label" x="{legend_x + 5 * step + 4}" y="{legend_y + 9}">More</text>'
    )

    summary = f"{total:,} contributions in the last year"

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">{html.escape(USERNAME)} GitHub contribution activity</title>
<desc id="desc">{html.escape(summary)}</desc>
<style>
  .label {{ fill: #57606a; font: 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
  .summary {{ fill: #1f2328; font: 600 12px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
  .day {{ shape-rendering: geometricPrecision; }}
  .level-0 {{ fill: #ebedf0; }}
  .level-1 {{ fill: #9be9a8; }}
  .level-2 {{ fill: #40c463; }}
  .level-3 {{ fill: #30a14e; }}
  .level-4 {{ fill: #216e39; }}
  @media (prefers-color-scheme: dark) {{
    .label {{ fill: #8b949e; }}
    .summary {{ fill: #f0f6fc; }}
    .level-0 {{ fill: #161b22; }}
    .level-1 {{ fill: #0e4429; }}
    .level-2 {{ fill: #006d32; }}
    .level-3 {{ fill: #26a641; }}
    .level-4 {{ fill: #39d353; }}
  }}
</style>
{months}
{weekdays}
{''.join(rects)}
<text class="summary" x="{left}" y="{height - 13}">{html.escape(summary)}</text>
{''.join(legend)}
</svg>
'''


def main() -> int:
    calendar = fetch_calendar()
    svg = render_svg(calendar)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(svg, encoding="utf-8")
    print(f"Wrote {OUTPUT} ({calendar['totalContributions']:,} contributions)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
