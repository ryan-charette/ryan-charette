#!/usr/bin/env python3
"""Render honest, self-contained profile graphics using only Python's stdlib.

Online: python scripts/update_stats.py
Offline: python scripts/update_stats.py --snapshot-dir /path/to/snapshots

Snapshots must contain user.json, repos.json (all public repos), and
contributions.html from https://github.com/users/ryan-charette/contributions.
GITHUB_TOKEN is optional and is sent only to api.github.com.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen

USERNAME = "ryan-charette"
BACKGROUND = "#0b1020"
INK = "#f2eee6"
MUTED = "#a0adc2"
CYAN = "#79e6db"
GOLD = "#ebc27d"
PALETTE = ["#1b273c", "#24545b", "#337b79", "#4ab2a7", CYAN]
LANGUAGE_COLORS = [CYAN, GOLD, "#a9a0ec", "#83ade8", "#c895bc", "#d5df93", "#6c7c97"]


class CalendarParser(HTMLParser):
    """Bind exact tooltip counts to cells by ID; never estimate from color."""

    def __init__(self):
        super().__init__()
        self.cells = {}
        self.tooltips = {}
        self.active_tooltip = None
        self.parts = []
        self.description = []
        self.in_description = False
        self.start = None
        self.end = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("data-from") and attrs.get("data-to"):
            if self.start is not None:
                raise ValueError("Multiple contribution calendars found")
            self.start = date.fromisoformat(attrs["data-from"][:10])
            self.end = date.fromisoformat(attrs["data-to"][:10])
        if "ContributionCalendar-day" in attrs.get("class", "").split():
            if not attrs.get("data-date"):
                return  # Future empty calendar cells have no date.
            identity = attrs.get("id")
            if not identity or identity in self.cells:
                raise ValueError("Missing or duplicate contribution cell ID")
            level = int(attrs["data-level"])
            if level not in range(5):
                raise ValueError("Unknown calendar intensity level")
            self.cells[identity] = (date.fromisoformat(attrs["data-date"]), level)
        if tag == "tool-tip":
            self.active_tooltip = attrs.get("for")
            self.parts = []
        if tag == "h2" and attrs.get("id") == "js-contribution-activity-description":
            self.in_description = True

    def handle_data(self, data):
        if self.active_tooltip:
            self.parts.append(data)
        if self.in_description:
            self.description.append(data)

    def handle_endtag(self, tag):
        if tag == "tool-tip" and self.active_tooltip:
            if self.active_tooltip in self.tooltips:
                raise ValueError("Duplicate calendar tooltip")
            self.tooltips[self.active_tooltip] = " ".join("".join(self.parts).split())
            self.active_tooltip = None
        if tag == "h2":
            self.in_description = False


def parse_calendar(html):
    parser = CalendarParser()
    parser.feed(html)
    parser.close()
    if parser.start is None or parser.end is None or not parser.cells:
        raise ValueError("GitHub calendar format unknown: missing dated cells or range")
    span = (parser.end - parser.start).days + 1
    if not 365 <= span <= 372:
        raise ValueError(f"Expected a complete annual calendar; found {span} days")
    days = []
    for identity, (day, level) in parser.cells.items():
        tooltip = parser.tooltips.get(identity, "")
        match = re.fullmatch(
            r"(No|[\d,]+) contributions? on ([A-Za-z]+) (\d+)(?:st|nd|rd|th)\.", tooltip
        )
        if not match:
            raise ValueError(f"Missing or unknown tooltip format for {day}")
        count = 0 if match[1] == "No" else int(match[1].replace(",", ""))
        if match[2] != day.strftime("%B") or int(match[3]) != day.day:
            raise ValueError(f"Tooltip date does not match calendar cell {day}")
        if (level == 0) != (count == 0):
            raise ValueError(f"Tooltip count contradicts calendar intensity for {day}")
        days.append({"date": day.isoformat(), "count": count, "level": level})
    days.sort(key=lambda item: item["date"])
    expected = [(parser.start + timedelta(days=offset)).isoformat() for offset in range(span)]
    if [item["date"] for item in days] != expected:
        raise ValueError("Calendar has missing, duplicate, or out-of-range dates")
    heading = " ".join("".join(parser.description).split())
    match = re.fullmatch(r"([\d,]+) contributions? in the last year", heading)
    if not match:
        raise ValueError("GitHub contribution heading format unknown")
    heading_total = int(match[1].replace(",", ""))
    # GitHub may pad the first week before the period counted by its heading.
    # Validate against the displayed sum or the rolling-year portion, and always
    # report our exact displayed date range and its exact sum.
    total = sum(item["count"] for item in days)
    valid_totals = {total}
    for rolling_length in (365, 366):
        rolling_start = parser.end - timedelta(days=rolling_length - 1)
        valid_totals.add(sum(item["count"] for item in days if date.fromisoformat(item["date"]) >= rolling_start))
    if heading_total not in valid_totals:
        raise ValueError("Tooltip totals do not reconcile with GitHub's annual heading")
    return days, heading_total


def request(url, is_api=False):
    headers = {"User-Agent": "ryan-charette-profile-stats", "Accept-Language": "en-US,en;q=0.9"}
    if is_api:
        headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return response.read().decode("utf-8")


def source_data(snapshot_dir):
    if snapshot_dir:
        root = Path(snapshot_dir)
        return (
            json.loads((root / "user.json").read_text(encoding="utf-8-sig")),
            json.loads((root / "repos.json").read_text(encoding="utf-8-sig")),
            (root / "contributions.html").read_text(encoding="utf-8-sig"),
        )
    user = json.loads(request(f"https://api.github.com/users/{USERNAME}", is_api=True))
    repos = []
    for page in range(1, 101):
        batch = json.loads(request(f"https://api.github.com/users/{USERNAME}/repos?type=owner&per_page=100&page={page}", is_api=True))
        if not isinstance(batch, list):
            raise ValueError("Repository API did not return a list")
        repos.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise ValueError("Repository pagination exceeded safety limit")
    calendar = request(f"https://github.com/users/{USERNAME}/contributions")
    return user, repos, calendar


def summarize(user, repos, html):
    if user.get("login", "").lower() != USERNAME or not isinstance(repos, list):
        raise ValueError("Unexpected user or repository response")
    if len(repos) != user.get("public_repos"):
        raise ValueError("Repository list incomplete or changed during fetch; retry")
    identities = set()
    for repo in repos:
        if repo.get("private") is not False or repo.get("owner", {}).get("login", "").lower() != USERNAME:
            raise ValueError("Expected only public repositories owned by profile user")
        if not isinstance(repo.get("id"), int) or repo.get("id") in identities or not isinstance(repo.get("fork"), bool) or "language" not in repo:
            raise ValueError("Duplicate or incomplete repository record")
        identities.add(repo["id"])
        if repo.get("language") is not None and not isinstance(repo["language"], str):
            raise ValueError("Invalid primary repository language")
    original = [repo for repo in repos if not repo["fork"]]
    languages = Counter(repo["language"] for repo in original if repo["language"])
    days, heading_total = parse_calendar(html)
    longest_streak = current_streak = 0
    for day in days:
        current_streak = current_streak + 1 if day["count"] else 0
        longest_streak = max(longest_streak, current_streak)
    active_weeks = {
        (date.fromisoformat(day["date"]) - timedelta(days=(date.fromisoformat(day["date"]).weekday() + 1) % 7)).isoformat()
        for day in days if day["count"]
    }
    return {
        "username": USERNAME,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "GitHub public REST API and unauthenticated public profile contribution calendar",
        "public_repositories": len(repos),
        "original_repositories": len(original),
        "forked_repositories": len(repos) - len(original),
        "primary_language_count": len(languages),
        "repositories_by_primary_language": dict(sorted(languages.items(), key=lambda item: (-item[1], item[0]))),
        "original_repositories_without_detected_language": sum(repo["language"] is None for repo in original),
        "calendar": {
            "start": days[0]["date"], "end": days[-1]["date"], "displayed_days": len(days),
            "contributions_displayed": sum(day["count"] for day in days),
            "github_annual_heading_total": heading_total,
            "active_days": sum(day["count"] > 0 for day in days),
            "active_weeks": len(active_weeks),
            "best_daily_count": max(day["count"] for day in days),
            "longest_streak_days_in_displayed_period": longest_streak,
            "days": days,
        },
        "methodology": [
            "Original repositories means owned public repositories excluding forks; includes archived repositories and this profile repository.",
            "Languages count GitHub's primary language per original repository, not code bytes, proficiency, or a ranking of mathematical interests.",
            "Contribution counts come from exact tooltip values in the unauthenticated public profile calendar; anonymous private activity may be included by GitHub.",
            "Calendar metrics use the full displayed date range, including first-week padding. Contributions are not synonymous with commits.",
            "Active weeks start on Sunday; a streak is consecutive dates with nonzero contributions within the displayed period.",
        ],
    }


def text(x, y, value, size=14, color=INK, extra=""):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" {extra}>{escape(str(value))}</text>'


def canvas(height, title, description):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}" role="img" aria-labelledby="title description">',
        f'<title id="title">{escape(title)}</title><desc id="description">{escape(description)}</desc>',
        '<defs><linearGradient id="edge" x1="0" x2="1"><stop stop-color="#79e6db"/><stop offset="1" stop-color="#ebc27d"/></linearGradient></defs>',
        f'<rect x="1" y="1" width="998" height="{height - 2}" rx="18" fill="{BACKGROUND}" stroke="#26334a"/>',
        '<path d="M 24 1 H 976" stroke="url(#edge)" stroke-width="2" opacity=".8"/>',
        '<g font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Arial, sans-serif">',
    ]


def stats_svg(stats):
    cal = stats["calendar"]
    date_label = stats["generated_at"][:10]
    entries = list(stats["repositories_by_primary_language"].items())
    if stats["original_repositories_without_detected_language"]:
        entries.append(("No language detected", stats["original_repositories_without_detected_language"]))
    extra_height = max(0, (len(entries) + 3) // 4 - 2) * 24
    out = canvas(448 + extra_height, "Ryan Charette — GitHub in numbers", "Public repository metrics and exact contribution-calendar counts. See stats.json for methodology and date range.")
    out += [text(32, 36, "GITHUB / IN NUMBERS", 13, CYAN, 'letter-spacing="2" font-weight="700"'),
            text(968, 36, f"UPDATED {date_label} UTC", 11, MUTED, 'text-anchor="end" letter-spacing="1"')]
    metrics = [
        (stats["original_repositories"], "ORIGINAL REPOSITORIES", "Owned public repos, forks excluded"),
        (stats["public_repositories"], "PUBLIC REPOSITORIES", f'{stats["forked_repositories"]} forks included'),
        (stats["primary_language_count"], "PRIMARY LANGUAGES", "Across original repositories"),
        (f'{cal["contributions_displayed"]:,}', "CONTRIBUTIONS DISPLAYED", "Exact calendar tooltip counts"),
        (cal["active_days"], "ACTIVE DAYS", "Days with at least one contribution"),
        (cal["active_weeks"], "ACTIVE WEEKS", "Sunday–Saturday calendar weeks"),
        (cal["best_daily_count"], "BEST DAILY COUNT", "Contributions on the busiest day"),
        (f'{cal["longest_streak_days_in_displayed_period"]} days', "LONGEST STREAK", "Within the displayed period"),
    ]
    for index, (value, label, detail) in enumerate(metrics):
        x = 32 + (index % 4) * 241
        y = 96 + (index // 4) * 106
        out += [text(x, y, value, 36, INK, 'font-weight="650"'), text(x, y + 24, label, 10, CYAN if index < 4 else GOLD, 'letter-spacing=".8" font-weight="700"'), text(x, y + 43, detail, 10, MUTED)]
    out += ['<path d="M32 274 H968" stroke="#26334a"/>', text(32, 302, "REPOSITORIES BY PRIMARY LANGUAGE", 11, MUTED, 'letter-spacing="1" font-weight="700"')]
    x = 32.0
    for index, (language, count) in enumerate(entries):
        width = 936 * count / max(stats["original_repositories"], 1)
        color = LANGUAGE_COLORS[index % len(LANGUAGE_COLORS)]
        out.append(f'<rect x="{x:.2f}" y="319" width="{max(width - 3, 1):.2f}" height="10" rx="3" fill="{color}"><title>{escape(language)}: {count} repositories</title></rect>')
        x += width
    for index, (language, count) in enumerate(entries):
        row, col = divmod(index, 4)
        x, y = 32 + col * 241, 354 + row * 24
        color = LANGUAGE_COLORS[index % len(LANGUAGE_COLORS)]
        out += [f'<circle cx="{x + 4}" cy="{y - 4}" r="4" fill="{color}"/>', text(x + 16, y, f"{language}  {count}", 12, INK)]
    out += [text(32, 412 + extra_height, f'Calendar: {cal["start"]} → {cal["end"]} · Language values are repository counts, not code percentages.', 10, MUTED),
            text(32, 430 + extra_height, "GitHub public API + public profile calendar. Calendar may include anonymous private activity. Details: assets/stats.json", 10, MUTED), '</g></svg>']
    return "\n".join(out) + "\n"


def activity_svg(stats):
    cal = stats["calendar"]
    out = canvas(245, "Ryan Charette — contribution activity", f'{cal["contributions_displayed"]} contributions displayed from {cal["start"]} to {cal["end"]}; {cal["active_days"]} active days. Exact daily counts are available in stats.json.')
    out += [text(32, 36, "CONSISTENCY / THE CONTRIBUTION MAP", 13, CYAN, 'letter-spacing="1.6" font-weight="700"'), text(968, 36, f'{cal["contributions_displayed"]:,} contributions · {cal["active_days"]} active days', 14, INK, 'text-anchor="end"'), text(32, 58, f'{cal["start"]} → {cal["end"]}', 11, MUTED)]
    start = date.fromisoformat(cal["start"])
    sunday = start - timedelta(days=(start.weekday() + 1) % 7)
    previous_label_column = -5
    for item in cal["days"]:
        day = date.fromisoformat(item["date"])
        column = (day - sunday).days // 7
        row = (day.weekday() + 1) % 7
        x, y = 110 + column * 16, 94 + row * 16
        if day.day == 1 and column - previous_label_column >= 3:
            out.append(text(x, 81, day.strftime("%b"), 10, MUTED))
            previous_label_column = column
        out.append(f'<rect x="{x}" y="{y}" width="12" height="12" rx="3" fill="{PALETTE[item["level"]]}"><title>{item["date"]}: {item["count"]} contributions</title></rect>')
    for row, label in ((1, "MON"), (3, "WED"), (5, "FRI")):
        out.append(text(65, 103 + row * 16, label, 9, MUTED, 'text-anchor="end" letter-spacing="1"'))
    out += [text(32, 226, "Exact GitHub tooltip counts · Public profile calendar may include anonymous private activity", 10, MUTED), text(809, 226, "LESS", 9, MUTED)]
    for level, color in enumerate(PALETTE):
        out.append(f'<rect x="{844 + level * 16}" y="216" width="11" height="11" rx="2" fill="{color}"/>')
    out += [text(934, 226, "MORE", 9, MUTED), '</g></svg>']
    return "\n".join(out) + "\n"


def write_outputs(assets, rendered):
    """Stage all outputs before replacing any; individual replacements are atomic."""
    assets.mkdir(parents=True, exist_ok=True)
    staged = []
    try:
        for name, content in rendered.items():
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=assets, prefix=f".{name}.", delete=False) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                staged.append((Path(handle.name), assets / name))
        for temporary, destination in staged:
            os.replace(temporary, destination)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", help="Read previously captured public source data instead of fetching")
    args = parser.parse_args()
    try:
        stats = summarize(*source_data(args.snapshot_dir))
        # Build and validate all data before touching any existing output.
        rendered = {"github-stats.svg": stats_svg(stats), "activity.svg": activity_svg(stats), "stats.json": json.dumps(stats, indent=2, ensure_ascii=False) + "\n"}
        write_outputs(Path(__file__).resolve().parent.parent / "assets", rendered)
    except Exception as error:
        raise SystemExit(f"Profile statistics refresh failed: {error}") from None
    print(f'Updated {stats["original_repositories"]} original repositories and {stats["calendar"]["contributions_displayed"]:,} displayed contributions ({stats["calendar"]["start"]} to {stats["calendar"]["end"]}).')


if __name__ == "__main__":
    main()
