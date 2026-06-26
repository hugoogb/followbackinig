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

def _username_and_timestamp(block) -> tuple[str, int | None]:
    """Pull (username, timestamp) from one IG relationship block.

    Instagram emits the same data in several shapes across export files:
      - followers:  string_list_data[].value holds the username
      - following:  string_list_data[] has only href + timestamp;
                    the username is the block-level "title"
      - pending:    no string_list_data at all; label_values holds a
                    {"label": "Username", "value": ...} pair, and the
                    timestamp is on the block itself
    This tries each location so all three parse correctly.
    """
    username = ""
    timestamp = block.get("timestamp")  # block-level (pending requests)

    for item in block.get("string_list_data") or []:
        value = (item.get("value") or "").strip()
        if value:
            username = value
        if item.get("timestamp"):
            timestamp = item["timestamp"]

    if not username:
        for pair in block.get("label_values") or []:
            if pair.get("label") == "Username":
                username = (pair.get("value") or "").strip()

    if not username:
        username = (block.get("title") or "").strip()

    return username, timestamp


def _extract_accounts(blocks) -> dict[str, dict]:
    """Pull accounts out of a list of IG relationship blocks.

    Returns a mapping username -> {"href": str, "timestamp": int | None}.
    The profile URL is always built as a clean web link from the username
    rather than reusing the export's href, which is sometimes a
    `.../_u/<user>` app-deeplink that doesn't open cleanly in a browser.
    """
    accounts: dict[str, dict] = {}
    for block in blocks:
        username, timestamp = _username_and_timestamp(block)
        if not username:
            continue
        accounts[username] = {
            "href": f"https://instagram.com/{username}",
            "timestamp": timestamp,
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


def load_pending_requests(folder: Path) -> dict[str, dict]:
    """Load follow requests you've SENT that are still pending.

    Reads pending_follow_requests.json (private accounts you asked to follow
    that haven't accepted yet). This file is optional — accounts with no
    pending requests won't have it — so a missing file returns {} rather than
    erroring.
    """
    path = folder / "pending_follow_requests.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    # pending_follow_requests.json is a dict keyed by
    # "relationships_follow_requests_sent"; tolerate a bare list too.
    blocks = data if isinstance(data, list) else data.get("relationships_follow_requests_sent", [])
    return _extract_accounts(blocks)


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
<title>{title}</title>
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
<h1>{title}</h1>
<p class="meta">{intro} <span class="hint">(Click a column header to sort.)</span></p>
<table id="t">
<thead>
<tr><th data-k="done">✓</th><th data-k="username">Username</th><th data-k="date">{date_label}</th></tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
<script>
const tbody = document.querySelector('#t tbody');
let asc = true, lastKey = 'date';
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


def _render_action_list(title: str, intro: str, records: list[dict],
                        date_label: str = "Followed on") -> str:
    """Render a sortable, checkbox action list for any set of account records."""
    rows = []
    for acc in records:
        user = html.escape(acc["username"])
        url = html.escape(acc["profile_url"], quote=True)
        # 'unknown' sorts after real dates as a data attribute too (~ > digits).
        sort_date = acc["followed_on"] if acc["followed_on"] != "unknown" else "~"
        rows.append(
            f'<tr data-username="{user}" data-date="{html.escape(sort_date)}" data-done="0">'
            f'<td><input type="checkbox"></td>'
            f'<td><a href="{url}" target="_blank" rel="noopener">{user}</a></td>'
            f'<td>{html.escape(acc["followed_on"])}</td></tr>'
        )
    return _HTML_TEMPLATE.format(
        title=html.escape(title), intro=html.escape(intro),
        date_label=html.escape(date_label), rows="\n".join(rows),
    )


def _write_list(records: list[dict], html_path: Path, csv_path: Path, txt_path: Path,
                title: str, intro: str, date_label: str) -> list[Path]:
    """Write an HTML action list plus CSV and TXT for a set of account records."""
    date_field = date_label.lower().replace(" ", "_")  # "Followed on" -> "followed_on"

    html_path.write_text(
        _render_action_list(title, intro, records, date_label), encoding="utf-8"
    )
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["username", "profile_url", date_field])
        for acc in records:
            writer.writerow([acc["username"], acc["profile_url"], acc["followed_on"]])
    txt_path.write_text(
        "\n".join(acc["username"] for acc in records), encoding="utf-8"
    )
    return [html_path, csv_path, txt_path]


def write_outputs(candidates: list[dict], pending: list[dict], out_dir: Path) -> list[Path]:
    """Write the unfollow action list and, if any, the pending-requests list.

    Files are written into out_dir (the directory the script is run from),
    not the export folder, so running the tool doesn't litter your export.
    Returns the paths written.
    """
    written = _write_list(
        candidates,
        out_dir / "cleanup.html",
        out_dir / "not_following_back.csv",
        out_dir / "not_following_back.txt",
        title="Not following you back",
        intro=f"{len(candidates)} accounts you follow who don't follow you back. "
              "Tick the box once you've unfollowed someone in the app.",
        date_label="Followed on",
    )
    if pending:
        written += _write_list(
            pending,
            out_dir / "pending_requests.html",
            out_dir / "pending_requests.csv",
            out_dir / "pending_requests.txt",
            title="Pending sent follow requests",
            intro=f"{len(pending)} follow requests you've sent that haven't been "
                  "accepted yet. Open a profile to cancel the request if you want; "
                  "tick the box to mark it handled.",
            date_label="Requested on",
        )
    return written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    following, followers = load_accounts(folder)
    diff = compute_diff(following, followers)
    candidates = diff.not_following_back
    pending_accounts = load_pending_requests(folder)
    pending = _sorted_records(set(pending_accounts), pending_accounts)

    print(f"Following:             {len(following)}")
    print(f"Followers:             {len(followers)}")
    print(f"Mutuals:               {diff.mutuals}")
    print(f"Don't follow you back: {len(candidates)}")
    print(f"You don't follow back: {len(diff.fans_you_dont_follow_back)}")
    print(f"Pending sent requests: {len(pending)}")
    print()

    print("=== Not following you back (candidates to unfollow) ===")
    for acc in candidates:
        print(f"  {acc['username']:<30} followed {acc['followed_on']}  {acc['profile_url']}")

    if pending:
        print("\n=== Pending follow requests you've sent (awaiting acceptance) ===")
        for acc in pending:
            print(f"  {acc['username']:<30} requested {acc['followed_on']}  {acc['profile_url']}")

    written = write_outputs(candidates, pending, Path.cwd())
    print(f"\nSaved {len(candidates)} unfollow candidates"
          + (f" and {len(pending)} pending requests" if pending else "")
          + ". Open the action list(s):")
    for path in written:
        print(f"  {path}")


if __name__ == "__main__":
    main()
