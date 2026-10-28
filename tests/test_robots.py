"""robots.txt parsing and matching tests."""

from __future__ import annotations

import unittest

from lairwatch.robots import is_allowed, parse_robots

ROBOTS = """
User-agent: *
Disallow: /search
Disallow: /*/search
Disallow: /orders$
Disallow: /*.json$
Allow: /search/about
Allow: /us
Sitemap: https://magic.wizards.com/en/sitemap.xml

User-agent: otherbot
Disallow: /
"""


class RobotsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.allow, self.disallow, self.sitemaps = parse_robots(
            ROBOTS, "secret-lair-watch/0.1"
        )

    def test_sitemap_lines_are_collected(self) -> None:
        self.assertIn("https://magic.wizards.com/en/sitemap.xml", self.sitemaps)

    def test_plain_paths_allowed(self) -> None:
        self.assertTrue(is_allowed("/us", self.allow, self.disallow))

    def test_disallowed_prefix(self) -> None:
        self.assertFalse(is_allowed("/search", self.allow, self.disallow))

    def test_longer_allow_wins_over_shorter_disallow(self) -> None:
        self.assertTrue(is_allowed("/search/about", self.allow, self.disallow))

    def test_wildcard_rule(self) -> None:
        self.assertFalse(is_allowed("/us/foo/search", self.allow, self.disallow))

    def test_end_anchor_rule(self) -> None:
        self.assertFalse(is_allowed("/orders", self.allow, self.disallow))
        self.assertTrue(is_allowed("/orders/123", self.allow, self.disallow))

    def test_json_suffix_rule(self) -> None:
        self.assertFalse(is_allowed("/products.json", self.allow, self.disallow))
        self.assertTrue(is_allowed("/products.json/extra", self.allow, self.disallow))

    def test_other_user_agent_group_is_ignored(self) -> None:
        allow, disallow, _ = parse_robots(ROBOTS, "some-other-agent/2.0")
        self.assertTrue(is_allowed("/us", allow, disallow))

    def test_empty_rules_allow_everything(self) -> None:
        allow, disallow, _ = parse_robots("User-agent: *\n", "x/1")
        self.assertTrue(is_allowed("/anything", allow, disallow))

    def test_comments_are_ignored(self) -> None:
        allow, disallow, _ = parse_robots(
            "User-agent: *  # everyone\nDisallow: /private  # no\n", "x/1"
        )
        self.assertFalse(is_allowed("/private/page", allow, disallow))


if __name__ == "__main__":
    unittest.main()
