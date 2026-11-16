"""AC-2 and AC-3: one run does matrix + intent + fix + re-verification; the intent grammar and report fields."""
from __future__ import annotations

import hashlib
import json
import re

import crawlpolicy as cp
from conftest import DEMO_INTENT

KEYS = ["tool", "version", "rfc", "mode", "source", "old", "new", "groups", "ignored_lines", "warnings", "bots", "urls",
        "urls_source", "cells", "summary", "intent", "fix", "changed", "exit_code"]


def test_one_run_fix_is_reverified(cli, work, report):
    res = cli("check", "robots.txt", "--intent", DEMO_INTENT)
    assert res.code == 1
    assert "re-verified: 0 violations, 0 changed cells" in res.out
    rep = report(work)
    assert rep["fix"]["verified"] is True
    assert rep["fix"]["violations_after"] == 0 and rep["fix"]["regressions"] == 0
    verdicts = [c["verdict"] for c in rep["cells"] if not c["ok"]]
    assert "Google-Extended /admin/users => allow @L0" in verdicts
    assert "OAI-SearchBot /admin/ => allow @L0" in verdicts
    fixed = (work / "robots.fixed.txt").read_text(encoding="utf-8")
    assert hashlib.sha256(fixed.encode("utf-8")).hexdigest() == rep["fix"]["sha256"]
    again = cli("check", "robots.fixed.txt", "--intent", DEMO_INTENT)
    assert again.code == 0, again.out
    assert "Intent holds" in again.out


def test_fix_keeps_every_other_cell(cli, work, report):
    cli("check", "robots.txt", "--intent", DEMO_INTENT)
    old = {(c["bot"], c["url"]): c for c in report(work)["cells"]}
    groups, _ = cp.parse_robots((work / "robots.fixed.txt").read_text(encoding="utf-8"))
    for (bot, url), cell in old.items():
        if cell["ok"]:
            assert cp.decide(groups, bot, url)["allowed"] == cell["allowed"], (bot, url)


def test_star_fallback_bot_gets_a_group_that_copies_star_rules(cli, work):
    cli("check", "robots.txt", "--intent", DEMO_INTENT)
    fixed = (work / "robots.fixed.txt").read_text(encoding="utf-8")
    assert "User-agent: ClaudeBot\nUser-agent: Applebot-Extended\nUser-agent: Meta-ExternalAgent\nUser-agent: Amazonbot\nDisallow: /admin/\nDisallow: /\n" in fixed


def test_prefix_fix_when_a_longer_allow_wins(cli, work):
    """Q-1: the fix beats the whole opposing prefix, not just the sampled URL."""
    cli("check", "robots.txt", "--intent", "control=deny")
    fixed = (work / "robots.fixed.txt").read_text(encoding="utf-8")
    assert "Disallow: /blog/public/*" in fixed
    assert "Disallow: /blog/public/$" not in fixed
    for url in ("/blog/public/post-1", "/blog/public/a/b/c", "/blog/public/"):
        res = cli("check", "robots.fixed.txt", "--bots", "Google-Extended", "--url", url, "--intent", "control=deny")
        assert res.code == 0, (url, res.out)


def test_reverification_probes_children_of_rule_paths(cli, work):
    cli("check", "robots.txt", "--intent", "control=deny")
    report = json.loads((work / "crawlpolicy-report.json").read_text(encoding="utf-8"))
    assert "/blog/public/x" in report["fix"]["probe_urls"]
    assert report["fix"]["verified"] is True


def test_end_anchored_opposing_rule_gets_an_exact_pin(cli, tmp_path):
    robots = tmp_path / "robots.txt"
    robots.write_text("User-agent: GPTBot\nAllow: /a/b$\n", encoding="utf-8")
    res = cli("check", str(robots), "--bots", "GPTBot", "--url", "/a/b", "--intent", "GPTBot:/a/=deny",
              "--fixed-out", str(tmp_path / "fixed.txt"))
    assert res.code == 1
    assert "Disallow: /a/b$" in (tmp_path / "fixed.txt").read_text(encoding="utf-8")


def test_no_violation_means_no_fix_file(cli, work):
    res = cli("check", "robots.txt", "--intent", "GPTBot=deny")
    assert res.code == 0
    assert not (work / "robots.fixed.txt").exists()


def test_intent_grammar_last_clause_wins():
    bots = cp.resolve_bots("GPTBot,OAI-SearchBot,Googlebot,DocsHarvester/2.1", cp.load_bot_table(None))
    clauses = cp.parse_intent("ai=deny; search=allow; GPTBot:/public/=allow; all:/admin/=deny", bots)
    assert [(c["who"], c["path"], c["want"]) for c in clauses] == [
        ("ai", "/", "deny"), ("search", "/", "allow"), ("gptbot", "/public/", "allow"), ("all", "/admin/", "deny")]
    by = {b["token"]: b for b in bots}

    def want(bot, url):
        i = cp.want_for(by[bot], url, clauses)
        return None if i is None else clauses[i]["want"]

    assert want("GPTBot", "/x") == "deny"            # ai=deny
    assert want("GPTBot", "/public/a") == "allow"    # later, more specific clause
    assert want("GPTBot", "/admin/a") == "deny"      # all:/admin/ comes last
    assert want("OAI-SearchBot", "/x") == "allow"    # search=allow overrides ai=deny
    assert want("Googlebot", "/x") == "allow"        # role search (not ai)
    assert want("DocsHarvester", "/x") is None       # no clause covers it: not checked
    assert want("DocsHarvester", "/admin/") == "deny"


def test_unchecked_cells_never_count_as_violations(cli, work, report):
    res = cli("check", "robots.txt", "--intent", "ClaudeBot:/secret/=deny")
    assert res.code == 1
    rep = report(work)
    assert {c["bot"] for c in rep["cells"] if not c["ok"]} == {"ClaudeBot"}
    assert all(c["intent"] is None for c in rep["cells"] if c["bot"] != "ClaudeBot")


def test_report_fields_and_verdict_format(cli, work, report):
    res = cli("check", "robots.txt", "--intent", DEMO_INTENT)
    assert res.code == 1
    rep = report(work)
    assert list(rep) == KEYS
    assert rep["tool"] == "crawlpolicy" and rep["rfc"] == "9309" and rep["mode"] == "check"
    raw = (work / "robots.txt").read_bytes()
    assert rep["source"] == {"path": "robots.txt", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "lines": 14}
    pattern = re.compile(r"^[A-Za-z_-]+ \S+ => (allow|deny) @L\d+$")
    assert all(pattern.match(c["verdict"]) for c in rep["cells"])
    assert list(rep["cells"][0]) == ["bot", "url", "allowed", "rule_line", "rule", "group", "intent", "ok", "verdict"]
    assert rep["summary"]["cells"] == len(rep["cells"]) == 17 * 5
    assert rep["summary"]["violations"] == 17
    assert rep["fix"]["added"] and rep["fix"]["patch"].startswith("--- robots.txt")
    assert rep["exit_code"] == 1


def test_bot_names_are_normalized_in_the_report(cli, work, report):
    res = cli("check", "robots.docs.txt", "--bots", "DocsHarvester/2.1", "--urls", "urls.txt")
    assert res.code == 0
    rep = report(work)
    assert rep["bots"] == [{"token": "DocsHarvester", "role": "other", "ai": False, "provider": None,
                            "group": "star", "note": None, "source": None}]
    assert rep["urls_source"] == "given"
    verdicts = {c["url"]: c["verdict"] for c in rep["cells"]}
    assert verdicts["/café/menu"] == "DocsHarvester /café/menu => deny @L4"      # %C3%A9 rule vs raw UTF-8 URL
    assert verdicts["/docs/guide.pdf"] == "DocsHarvester /docs/guide.pdf => deny @L5"
    assert verdicts["/docs/guide.html"] == "DocsHarvester /docs/guide.html => allow @L0"
    assert rep["summary"]["allowed"] == 3


def test_auto_sample_urls():
    groups, _ = cp.parse_robots("User-agent: *\nDisallow: /a/\nAllow: /b/*.html$\nDisallow: /a/\nDisallow: private\n")
    clauses = [{"path": "/admin/"}]
    assert cp.sample_urls(groups, clauses) == ["/", "/a/", "/b/x.html", "/admin/"]


def test_auto_urls_are_used_when_none_given(cli, work, report):
    cli("check", "robots.txt")
    rep = report(work)
    assert rep["urls_source"] == "auto"
    assert rep["urls"] == ["/", "/admin/", "/blog/", "/blog/public/", "/admin/users"]


def test_role_names_expand_in_bots(cli, work, report):
    cli("check", "robots.txt", "--bots", "training,Googlebot")
    assert [b["token"] for b in report(work)["bots"]] == ["GPTBot", "ClaudeBot", "CCBot", "Meta-ExternalAgent", "Amazonbot", "Googlebot"]


def test_bots_file_replaces_the_table(cli, work, report):
    (work / "bots.json").write_text('[{"token": "MyBot", "role": "training", "provider": "Me"}]', encoding="utf-8")
    cli("check", "robots.txt", "--bots-file", "bots.json", "--intent", "training=deny")
    rep = report(work)
    assert [b["token"] for b in rep["bots"]] == ["MyBot"]
    assert rep["bots"][0]["ai"] is True


def test_default_table_has_meta_and_amazon_as_training_bots(cli, work, report):
    """Meta-ExternalAgent and Amazonbot are built in with a source URL; training=deny checks them without --bots."""
    cli("check", "robots.txt", "--intent", "training=deny")
    rep = report(work)
    bots = {b["token"]: b for b in rep["bots"]}
    assert len(bots) == 17
    for token, provider in (("Meta-ExternalAgent", "Meta"), ("Amazonbot", "Amazon")):
        assert bots[token]["role"] == "training" and bots[token]["provider"] == provider
        assert bots[token]["source"].startswith("https://")
    bad = {c["bot"] for c in rep["cells"] if not c["ok"]}
    assert {"Meta-ExternalAgent", "Amazonbot"} <= bad
    assert rep["exit_code"] == 1
