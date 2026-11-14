"""AC-1: the evaluator matches the 24 hand-checked RFC 9309 cases, including the winning rule line."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

import crawlpolicy as cp

CASES = json.loads((Path(__file__).parent / "fixtures" / "rfc_cases.json").read_text(encoding="utf-8"))
SCRIPT = Path(cp.__file__)


def test_case_count():
    assert len(CASES) == 24


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_probe_cases_match_rfc9309(case):
    groups, _ = cp.parse_robots(case["robots"])
    token = cp.normalize_bot(case["agent"])
    got = cp.decide(groups, token, case["url"])
    assert got["allowed"] is case["allowed"], case["id"]
    assert (got["rule_line"] or 0) == case["rule_line"], case["id"]


def test_stdlib_only_imports():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert mods, "no imports found"
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


def test_product_token_is_exact_not_substring():
    robots = "User-agent: Applebot\nDisallow: /\n\nUser-agent: Google\nDisallow: /\n"
    groups, _ = cp.parse_robots(robots)
    assert cp.decide(groups, "Applebot", "/x")["allowed"] is False
    assert cp.decide(groups, "Applebot-Extended", "/x")["allowed"] is True
    assert cp.decide(groups, "Google-Extended", "/x")["allowed"] is True
    assert cp.decide(groups, "Google", "/x")["allowed"] is False


def test_specific_group_does_not_inherit_star():
    groups, _ = cp.parse_robots("User-agent: *\nDisallow: /admin/\n\nUser-agent: GPTBot\nDisallow: /tmp/\n")
    assert cp.decide(groups, "GPTBot", "/admin/x") == {"allowed": True, "rule_line": None, "rule": None, "group": "own"}
    assert cp.decide(groups, "ClaudeBot", "/admin/x")["allowed"] is False


def test_robots_txt_itself_is_always_allowed():
    groups, _ = cp.parse_robots("User-agent: *\nDisallow: /\n")
    assert cp.decide(groups, "GPTBot", "https://example.com/robots.txt")["allowed"] is True


def test_crlf_and_nonstandard_lines():
    text = "User-agent: GPTBot\r\nCrawl-delay: 5\r\nDisallow: /a\r\nSitemap: https://e.com/s.xml\r\nDisallow: /b\r\n"
    groups, ignored = cp.parse_robots(text)
    assert [r["path"] for r in groups[0]["rules"]] == ["/a", "/b"]
    assert [i["line"] for i in ignored] == [2] and ignored[0]["reason"] == "nonstandard"


def test_rule_before_user_agent_is_reported():
    _, ignored = cp.parse_robots("Disallow: /\nUser-agent: *\nAllow: /\n")
    assert ignored[0]["reason"] == "rule-before-user-agent"


def test_normalize_bot_names():
    assert cp.normalize_bot("DocsHarvester/2.1") == "DocsHarvester"
    assert cp.normalize_bot("ClaudeBot/1.0") == "ClaudeBot"
    assert cp.normalize_bot("Mozilla/5.0 (compatible; GPTBot/1.2; +https://x)") == "GPTBot"
    assert cp.normalize_bot("123") is None


def test_large_file_is_truncated_with_warning(tmp_path, monkeypatch, cli):
    big = tmp_path / "robots.txt"
    big.write_text("User-agent: *\nDisallow: /a\n" + "# pad\n" * 100_000 + "Disallow: /late\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    res = cli("check", str(big), "--bots", "GPTBot", "--url", "/late")
    assert "500 KiB" in res.err
    assert "GPTBot /late => allow @L0" in res.out
