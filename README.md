# followbackinig

Find the people you follow on Instagram who **don't follow you back**, fully
offline, so you can clean up your account. No login, no network, no API — it
reads the JSON files from Instagram's data export and produces a clickable
action list for fast manual unfollowing.

> Automated unfollowing is intentionally **not** included: it violates
> Instagram's Terms of Service and risks action against your account.

## Get your data

Instagram → **Settings → Your activity → Download your information** → request
an export with:

- **Scope:** *Followers and following*
- **Format:** *JSON*

Unzip it and locate the `connections/followers_and_following/` folder. It
contains `following.json` and one or more `followers_*.json` files.

## Usage

```bash
python3 ig_followback.py /path/to/connections/followers_and_following
```

Or drop `ig_followback.py` into that folder and run it with no arguments.

It prints a summary and writes three files into the export folder:

| File | What it is |
|------|-----------|
| `cleanup.html` | Action list — clickable profile links + checkboxes, sortable by follow date (oldest first, so stale follows surface) |
| `not_following_back.csv` | `username, profile_url, followed_on` for records/scripting |
| `not_following_back.txt` | Bare usernames |

Open `cleanup.html` in your browser, click each profile link to unfollow in the
Instagram app, and tick the box to mark it done.

## Output categories

- **Not following you back** — you follow them, they don't follow you (cleanup candidates)
- **You don't follow back** — they follow you, you don't follow them
- **Mutuals** — count of accounts following each other

## Privacy

Everything runs locally. Your export files and the generated lists are
git-ignored so your personal data never leaves your machine.

## Development

```bash
python3 -m unittest test_ig_followback -v
```

Pure stdlib — no dependencies to install.
