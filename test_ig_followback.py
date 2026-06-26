#!/usr/bin/env python3
"""Tests for ig_followback (offline Instagram follow-back cleanup util)."""

import json
import tempfile
import unittest
from pathlib import Path

import ig_followback as ig


def _block(username, href="", timestamp=None):
    """Build a single IG relationship block as it appears in the export."""
    item = {"value": username, "href": href or f"https://instagram.com/{username}"}
    if timestamp is not None:
        item["timestamp"] = timestamp
    return {"string_list_data": [item]}


class FormatDateTests(unittest.TestCase):
    def test_formats_epoch_as_iso_date(self):
        # 2021-01-01 00:00:00 UTC
        self.assertEqual(ig.format_date(1609459200), "2021-01-01")

    def test_missing_timestamp_is_unknown(self):
        self.assertEqual(ig.format_date(None), "unknown")

    def test_zero_timestamp_is_unknown(self):
        # IG sometimes emits 0 for an absent timestamp
        self.assertEqual(ig.format_date(0), "unknown")


class ComputeDiffTests(unittest.TestCase):
    def setUp(self):
        self.following = {
            "alice": {"href": "https://instagram.com/alice", "timestamp": 200},
            "bob": {"href": "https://instagram.com/bob", "timestamp": 100},
            "carol": {"href": "https://instagram.com/carol", "timestamp": None},
        }
        self.followers = {
            "alice": {"href": "https://instagram.com/alice", "timestamp": 999},
            "dave": {"href": "https://instagram.com/dave", "timestamp": 999},
        }

    def test_not_following_back_excludes_mutuals(self):
        diff = ig.compute_diff(self.following, self.followers)
        names = [acc["username"] for acc in diff.not_following_back]
        self.assertEqual(set(names), {"bob", "carol"})

    def test_not_following_back_sorted_oldest_first_unknown_last(self):
        diff = ig.compute_diff(self.following, self.followers)
        names = [acc["username"] for acc in diff.not_following_back]
        # bob (ts 100) before carol (unknown -> last)
        self.assertEqual(names, ["bob", "carol"])

    def test_candidate_carries_profile_url_and_followed_on(self):
        diff = ig.compute_diff(self.following, self.followers)
        bob = next(a for a in diff.not_following_back if a["username"] == "bob")
        self.assertEqual(bob["profile_url"], "https://instagram.com/bob")
        self.assertEqual(bob["followed_on"], ig.format_date(100))

    def test_fans_you_dont_follow_back(self):
        diff = ig.compute_diff(self.following, self.followers)
        names = [acc["username"] for acc in diff.fans_you_dont_follow_back]
        self.assertEqual(names, ["dave"])

    def test_mutuals_count(self):
        diff = ig.compute_diff(self.following, self.followers)
        self.assertEqual(diff.mutuals, 1)


class LoadAccountsTests(unittest.TestCase):
    def _write(self, folder, name, data):
        (folder / name).write_text(json.dumps(data), encoding="utf-8")

    def test_loads_following_dict_shape_and_followers_multifile(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self._write(folder, "following.json", {
                "relationships_following": [
                    _block("alice", timestamp=100),
                    _block("bob", timestamp=200),
                ]
            })
            # followers split across multiple files, top-level list shape
            self._write(folder, "followers_1.json", [_block("alice", timestamp=300)])
            self._write(folder, "followers_2.json", [_block("eve", timestamp=400)])

            following, followers = ig.load_accounts(folder)

            self.assertEqual(set(following), {"alice", "bob"})
            self.assertEqual(following["bob"]["timestamp"], 200)
            self.assertEqual(set(followers), {"alice", "eve"})

    def test_loads_older_single_file_followers(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self._write(folder, "following.json", {
                "relationships_following": [_block("alice")]
            })
            self._write(folder, "followers.json", [_block("zoe")])

            _following, followers = ig.load_accounts(folder)
            self.assertEqual(set(followers), {"zoe"})

    def test_skips_entries_without_value(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self._write(folder, "following.json", {
                "relationships_following": [
                    {"string_list_data": [{"value": "", "href": "x"}]},
                    _block("real"),
                ]
            })
            self._write(folder, "followers.json", [])
            following, _followers = ig.load_accounts(folder)
            self.assertEqual(set(following), {"real"})


class PendingRequestsTests(unittest.TestCase):
    def _write(self, folder, name, data):
        (folder / name).write_text(json.dumps(data), encoding="utf-8")

    def test_loads_pending_requests_dict_shape(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self._write(folder, "pending_follow_requests.json", {
                "relationships_follow_requests_sent": [
                    _block("private_one", timestamp=100),
                    _block("private_two", timestamp=200),
                ]
            })
            pending = ig.load_pending_requests(folder)
            self.assertEqual(set(pending), {"private_one", "private_two"})
            self.assertEqual(pending["private_two"]["timestamp"], 200)

    def test_loads_pending_requests_list_shape(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self._write(folder, "pending_follow_requests.json",
                        [_block("private_one", timestamp=100)])
            pending = ig.load_pending_requests(folder)
            self.assertEqual(set(pending), {"private_one"})

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            # No pending_follow_requests.json present — must not crash.
            self.assertEqual(ig.load_pending_requests(Path(d)), {})


class RenderActionListTests(unittest.TestCase):
    def test_includes_title_and_usernames(self):
        records = [ig._to_record("ghosty",
                   {"href": "https://instagram.com/ghosty", "timestamp": 100})]
        out = ig._render_action_list("My Title", "some intro", records)
        self.assertIn("My Title", out)
        self.assertIn("some intro", out)
        self.assertIn("ghosty", out)


if __name__ == "__main__":
    unittest.main()
