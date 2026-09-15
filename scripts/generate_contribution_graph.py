#!/usr/bin/env python3
"""Generate a GitHub-style contribution heatmap and daily commit-count page."""

from __future__ import annotations

import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

GRAPHQL_URL = "https://api.github.com/graphql"
USERNAME = os.environ.get("GITHUB_USERNAME", "dporkka")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
GRAPH_OUTPUT = Path(os.environ.get("GRAPH_OUTPUT", "images/contribution-graph.svg"))
COMMITS_OUTPUT = Path(os.environ.get("COMMITS_OUTPUT", "COMMITS.md"))

CALENDAR_QUERY = """
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

COMMITS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      commitContributionsByRepository(maxRepositories: 100) {
        contributions(
          first: 100
          orderBy: {field: OCCURRED_AT, direction: ASC}
        ) {
          nodes {
            commitCount
            occurredAt
          }
          pageInfo {
            hasNextPage
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


def graphql(query: str, variables: dict) -> dict:
    if not TOKEN:
        raise RuntimeError("GH_TOKEN or GITHUB_TOKEN is required")

    body = json.dumps({"query": query, "variables": variables}).encode()
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

    return payload.get("data", {})


def fetch_calendar() -> dict:
    data = graphql(CALENDAR_QUERY, {"login": USERNAME})
    user = data.get("user")
    if not user:
        raise RuntimeError(f"GitHub user {USERNAME!r} was not found")
    return user["contributionsCollection"]["contributionCalendar"]


def fetch_commit_counts(start_day: date, end_day: date) -> dict[str, int]:
    """Fetch exact per-day commit contributions in <=90-day windows.

    GitHub's commit-contribution connection is capped at 100 nodes per
    repository. A 90-day query window guarantees one repository cannot need
    more than 90 daily nodes, avoiding pagination truncation.
    """

    counts: defaultdict[str, int] = defaultdict(int)
    window_start = start_day

    while window_start <= end_day:
        window_end = min(window_start + timedelta(days=89), end_day)
        variables = {
            "login": USERNAME,
            "from": f"{window_start.isoformat()}T00:00:00Z",
            "to": f"{window_end.isoformat()}T23:59:59Z",
        }
        data = graphql(COMMITS_QUERY, variables)
        user = data.get("user")
        if not user:
            raise RuntimeError(f"GitHub user {USERNAME!r} was not found")

        collection = user["contributionsCollection"]
        for repository_group in collection["commitContributionsByRepository"]:
            connection = repository_group["contributions"]
            if connection["pageInfo"]["hasNextPage"]:
                raise RuntimeError(
                    "Commit contribution query unexpectedly requires pagination; "
                    "reduce the query window."
                )
            for node in connection["nodes"]:
                day = node["occurredAt"][:10]
                counts[day] += int(node["commitCount"])

        window_start = window_end + timedelta(days=1)

    return dict(counts)


def calendar_days(calendar: dict) -> list[dict]:
    return [
        day
        for week in calendar["weeks"]
        for day in week.get("contributionDays", [])
    ]


def render_svg(calendar: dict, commit_counts: dict[str, int]) -> str:
    weeks = calendar["weeks"]
    total = calendar["totalContributions"]
    total_commits = sum(commit_counts.values())

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
            contribution_count = int(day["contributionCount"])
            commit_count = int(commit_counts.get(day["date"], 0))
            level = LEVEL_CLASS.get(day["contributionLevel"], "level-0")
            day_text = html.escape(day["date"])
            commit_noun = "commit" if commit_count == 1 else "commits"
            contribution_noun = "contribution" if contribution_count == 1 else "contributions"
            rects.append(
                f'<rect class="day {level}" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2">'
                f'<title>{commit_count} {commit_noun} · {contribution_count} {contribution_noun} on {day_text}</title></rect>'
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

    summary = f"{total_commits:,} commits · {total:,} GitHub contributions in the last year"

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


def render_commits_markdown(calendar: dict, commit_counts: dict[str, int]) -> str:
    days = sorted(calendar_days(calendar), key=lambda item: item["date"], reverse=True)
    total_commits = sum(commit_counts.values())
    total_contributions = int(calendar["totalContributions"])

    lines = [
        "# Daily Git Commits",
        "",
        "This page is generated automatically from GitHub's contribution data. "
        "The **Commits** column is the number of commit contributions GitHub reports for that date; "
        "**All contributions** is the value used by the profile contribution calendar.",
        "",
        f"**{total_commits:,} commits · {total_contributions:,} total GitHub contributions in the displayed year**",
        "",
        "[← Back to profile](./README.md)",
        "",
    ]

    current_month: tuple[int, int] | None = None
    for day in days:
        parsed = date.fromisoformat(day["date"])
        month_key = (parsed.year, parsed.month)
        if month_key != current_month:
            if current_month is not None:
                lines.append("")
            lines.extend(
                [
                    f"## {parsed.strftime('%B %Y')}",
                    "",
                    "| Date | Commits | All contributions |",
                    "|---|---:|---:|",
                ]
            )
            current_month = month_key

        day_text = day["date"]
        commit_count = int(commit_counts.get(day_text, 0))
        contribution_count = int(day["contributionCount"])
        query = urllib.parse.quote(
            f"author:{USERNAME} committer-date:{day_text}", safe=""
        )
        commit_url = f"https://github.com/search?q={query}&type=commits"
        date_label = f"{parsed.strftime('%a, %b')} {parsed.day}, {parsed.year}"
        lines.append(
            f"| [{date_label}]({commit_url}) | **{commit_count}** | {contribution_count} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "Commit details are limited to repositories visible to the workflow token. "
            "If you want private-repository commit counts included, add a `PROFILE_TOKEN` secret "
            "with permission to read those contributions.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    calendar = fetch_calendar()
    days = calendar_days(calendar)
    if not days:
        raise RuntimeError("GitHub returned an empty contribution calendar")

    start_day = min(date.fromisoformat(day["date"]) for day in days)
    end_day = max(date.fromisoformat(day["date"]) for day in days)
    commit_counts = fetch_commit_counts(start_day, end_day)

    graph = render_svg(calendar, commit_counts)
    GRAPH_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_OUTPUT.write_text(graph, encoding="utf-8")

    commits_page = render_commits_markdown(calendar, commit_counts)
    COMMITS_OUTPUT.write_text(commits_page, encoding="utf-8")

    print(
        f"Wrote {GRAPH_OUTPUT} and {COMMITS_OUTPUT} "
        f"({sum(commit_counts.values()):,} commits; "
        f"{calendar['totalContributions']:,} contributions)"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
