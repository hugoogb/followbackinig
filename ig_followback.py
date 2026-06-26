#!/usr/bin/env python3
"""
Instagram follow-back cleanup (personal, offline).

Reads the JSON files from your Instagram "Export your information" export
(scope: Followers and following, format: JSON) and helps you clean up who you
follow. It reports:
  - people you follow who DON'T follow you back  (cleanup candidates)
  - people who follow you that you DON'T follow back
  - mutuals count

and generates an action list to make manual unfollowing fast:
  - cleanup.html             clickable profile links + checkboxes, sortable by follow date
  - not_following_back.csv   username, profile_url, followed_on
  - not_following_back.txt   bare usernames

No login, no network, no API. Everything runs locally on the export files.
Automated unfollowing is intentionally NOT included: it violates Instagram's
Terms of Service and risks action against your account.

Usage:
    python ig_followback.py /path/to/connections/followers_and_following
or drop this script into that folder and just run:
    python ig_followback.py
"""

from __future__ import annotations

import csv
import html
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _extract_accounts(blocks) -> dict[str, dict]:
    """Pull accounts out of a list of IG relationship blocks.

    Returns a mapping username -> {"href": str, "timestamp": int | None},
    preserving the per-account data the export carries (unlike a bare set).
    """
    accounts: dict[str, dict] = {}
    for block in blocks:
        for item in block.get("string_list_data", []):
            value = (item.get("value") or "").strip()
            if not value:
                continue
            accounts[value] = {
                "href": (item.get("href") or "").strip()
                or f"https://instagram.com/{value}",
                "timestamp": item.get("timestamp"),
            }
    return accounts


def _load_following(folder: Path) -> dict[str, dict]:
    path = folder / "following.json"
    if not path.exists():
        sys.exit(f"Could not find {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    # following.json is a dict keyed by "relationships_following"
    blocks = data["relationships_following"] if isinstance(data, dict) else data
    return _extract_accounts(blocks)


def _load_followers(folder: Path) -> dict[str, dict]:
    # Instagram splits large follower lists: followers_1.json, followers_2.json, ...
    files = sorted(folder.glob("followers_*.json"))
    if not files and (folder / "followers.json").exists():
        files = [folder / "followers.json"]  # older single-file exports
    if not files:
        sys.exit(f"Could not find followers_*.json in {folder}")
    accounts: dict[str, dict] = {}
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        # followers files are usually a top-level list
        blocks = data if isinstance(data, list) else data.get("relationships_followers", [])
        accounts.update(_extract_accounts(blocks))
    return accounts


def load_accounts(folder: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Load (following, followers) from an export folder."""
    return _load_following(folder), _load_followers(folder)


# --------------------------------------------------------------------------- #
# Diffing
# --------------------------------------------------------------------------- #

@dataclass
class Diff:
    not_following_back: list[dict]        # you follow them, they don't follow you
    fans_you_dont_follow_back: list[dict]  # they follow you, you don't follow them
    mutuals: int


def format_date(timestamp) -> str:
    """Convert an IG epoch timestamp to YYYY-MM-DD, or 'unknown' if absent."""
    if not timestamp:  # None or 0 (IG uses 0 as a missing sentinel)
        return "unknown"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")


def _to_record(username: str, info: dict) -> dict:
    timestamp = info.get("timestamp")
    return {
        "username": username,
        "profile_url": info.get("href") or f"https://instagram.com/{username}",
        "timestamp": timestamp,
        "followed_on": format_date(timestamp),
    }


def _sorted_records(usernames, source: dict[str, dict]) -> list[dict]:
    records = [_to_record(name, source[name]) for name in usernames]
    # Oldest follow first; unknown (no/zero timestamp) sorts last; ties by name.
    records.sort(key=lambda r: (not r["timestamp"], r["timestamp"] or 0, r["username"]))
    return records


def compute_diff(following: dict[str, dict], followers: dict[str, dict]) -> Diff:
    following_names = set(following)
    follower_names = set(followers)
    return Diff(
        not_following_back=_sorted_records(following_names - follower_names, following),
        fans_you_dont_follow_back=_sorted_records(follower_names - following_names, followers),
        mutuals=len(following_names & follower_names),
    )


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Instagram cleanup — not following you back</title>
<style>
  body {{ font: 16px/1.5 -apple-system, system-ui, sans-serif; margin: 2rem auto; max-width: 640px; color: #222; }}
  h1 {{ font-size: 1.4rem; }}
  .meta {{ color: #666; margin-bottom: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .5rem .6rem; border-bottom: 1px solid #eee; }}
  th {{ cursor: pointer; user-select: none; background: #fafafa; position: sticky; top: 0; }}
  th:hover {{ background: #f0f0f0; }}
  tr.done {{ opacity: .4; text-decoration: line-through; }}
  a {{ color: #0a66c2; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .hint {{ color: #999; font-size: .85rem; }}
</style>
</head>
<body>
<h1>Not following you back</h1>
<p class="meta">{count} accounts you follow who don't follow you back.
Tick the box once you've unfollowed someone in the app. <span class="hint">(Click a column header to sort.)</span></p>
<table id="t">
<thead>
<tr><th data-k="done">✓</th><th data-k="username">Username</th><th data-k="followed_on">Followed on</th></tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
<script>
const tbody = document.querySelector('#t tbody');
let asc = true, lastKey = 'followed_on';
tbody.addEventListener('change', e => {{
  if (e.target.type === 'checkbox') e.target.closest('tr').classList.toggle('done', e.target.checked);
}});
document.querySelectorAll('#t th').forEach(th => th.addEventListener('click', () => {{
  const k = th.dataset.k;
  asc = k === lastKey ? !asc : true;
  lastKey = k;
  const rows = [...tbody.querySelectorAll('tr')];
  rows.sort((a, b) => {{
    const va = a.dataset[k] || '', vb = b.dataset[k] || '';
    return (va > vb ? 1 : va < vb ? -1 : 0) * (asc ? 1 : -1);
  }});
  rows.forEach(r => tbody.appendChild(r));
}}));
</script>
</body>
</html>
"""


def _render_html(candidates: list[dict]) -> str:
    rows = []
    for acc in candidates:
        user = html.escape(acc["username"])
        url = html.escape(acc["profile_url"], quote=True)
        # 'unknown' sorts after real dates as a data attribute too (~ > digits).
        sort_date = acc["followed_on"] if acc["followed_on"] != "unknown" else "~"
        rows.append(
            f'<tr data-username="{user}" data-followed_on="{html.escape(sort_date)}" data-done="0">'
            f'<td><input type="checkbox"></td>'
            f'<td><a href="{url}" target="_blank" rel="noopener">{user}</a></td>'
            f'<td>{html.escape(acc["followed_on"])}</td></tr>'
        )
    return _HTML_TEMPLATE.format(count=len(candidates), rows="\n".join(rows))


def write_outputs(candidates: list[dict], folder: Path) -> list[Path]:
    """Write cleanup.html, not_following_back.csv and .txt. Returns paths written."""
    html_path = folder / "cleanup.html"
    csv_path = folder / "not_following_back.csv"
    txt_path = folder / "not_following_back.txt"

    html_path.write_text(_render_html(candidates), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["username", "profile_url", "followed_on"])
        for acc in candidates:
            writer.writerow([acc["username"], acc["profile_url"], acc["followed_on"]])

    txt_path.write_text(
        "\n".join(acc["username"] for acc in candidates), encoding="utf-8"
    )
    return [html_path, csv_path, txt_path]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    following, followers = load_accounts(folder)
    diff = compute_diff(following, followers)
    candidates = diff.not_following_back

    print(f"Following:             {len(following)}")
    print(f"Followers:             {len(followers)}")
    print(f"Mutuals:               {diff.mutuals}")
    print(f"Don't follow you back: {len(candidates)}")
    print(f"You don't follow back: {len(diff.fans_you_dont_follow_back)}")
    print()

    print("=== Not following you back (candidates to unfollow) ===")
    for acc in candidates:
        print(f"  {acc['username']:<30} followed {acc['followed_on']}  {acc['profile_url']}")

    written = write_outputs(candidates, folder)
    print(f"\nSaved {len(candidates)} candidates. Open the action list:")
    for path in written:
        print(f"  {path}")


if __name__ == "__main__":
    main()
