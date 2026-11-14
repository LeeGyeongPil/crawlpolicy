"""Regression tests for the review findings: large input speed, usage errors in both languages, help text, HTML check."""
from __future__ import annotations

import re
import time

import pytest

import crawlpolicy as cp


def test_large_robots_txt_finishes_in_seconds(cli, tmp_path):
    """Q-2: a ~500 KiB file of 40k simple rules is judged, fixed and reported quickly; the screen patch is cut."""
    rules = "".join(f"Disallow: /p{i}/\n" for i in range(40000))
    (tmp_path / "robots.txt").write_text("User-agent: *\n" + rules, encoding="utf-8")
    assert (tmp_path / "robots.txt").stat().st_size > 400 * 1024
    start = time.monotonic()
    res = cli("check", str(tmp_path / "robots.txt"), "--intent", "training=deny; all:/admin/=deny",
              "--out", str(tmp_path / "r.json"), "--fixed-out", str(tmp_path / "fixed.txt"))
    assert time.monotonic() - start < 20
    assert res.code == 1
    assert res.out.count("\n") < 200                      # the patch is cut, the matrix has a few dozen lines
    assert re.search(r"\.\.\. \d+ more patch lines — see .*fixed\.txt", res.out)


def test_fix_over_500_kib_is_not_verified(cli, tmp_path):
    """Q-2: a fixed copy that crawlers could not read to the end is reported as not verified, with a warning."""
    rules = "".join(f"Disallow: /p{i}/\n" for i in range(32000))
    (tmp_path / "robots.txt").write_text("User-agent: *\n" + rules, encoding="utf-8")
    res = cli("check", str(tmp_path / "robots.txt"), "--bots", "GPTBot", "--intent", "GPTBot=deny",
              "--out", str(tmp_path / "r.json"), "--fixed-out", str(tmp_path / "fixed.txt"))
    assert res.code == 1
    assert "could not be fully verified" in res.out
    assert "larger than 500 KiB" in res.err


def test_rule_matchers_are_built_once_per_rule():
    groups, _ = cp.parse_robots("User-agent: *\nDisallow: /a*b$\nAllow: /c/\n")
    rules = groups[0]["rules"]
    assert all(callable(r["_m"]) for r in rules)
    assert rules[0]["_m"]("/aXXb") and not rules[0]["_m"]("/aXXbc")
    assert rules[1]["_m"]("/c/d") and not rules[1]["_m"]("/c")


@pytest.mark.parametrize("argv,bad", [
    (["check", "samples/robots.txt", "--nope"], "unrecognized"),
    (["chek"], "invalid choice"),
    (["check", "samples/robots.txt", "--intent"], "expected one argument"),
    (["diff", "samples/robots.txt"], "required"),
])
def test_korean_usage_errors_have_no_english_body(cli, work, argv, bad):
    """Q-3: after '오류:' there are no English words, and the message points to --help."""
    res = cli("--lang", "ko", *argv)
    assert res.code == 2 and res.err.count("\n") == 1
    body = res.err.removeprefix("오류: ")
    assert bad not in body
    flags_removed = re.sub(r"--[a-z-]+|samples/\S+|\b(check|diff|OLD|NEW|ROBOTS)\b|'[^']*'|\{[^}]*\}|,", "", body)
    assert not re.search(r"[A-Za-z]{3,}", flags_removed), res.err
    assert "--help" in body or "check, diff" in body      # a hint: where to look, or the valid choices


def test_english_usage_errors_say_what_to_do(cli, work):
    res = cli("chek")
    assert res.code == 2
    assert "'chek' is not valid here" in res.err and "check, diff" in res.err


@pytest.mark.parametrize("lang,prefix", [("en", "usage:"), ("ko", "사용법:")])
def test_subcommand_help_has_one_usage_prefix(cli, lang, prefix):
    """Q-4: 'usage: usage:' must not appear."""
    res = cli("--lang", lang, "check", "--help")
    assert res.code == 0
    assert res.out.count(prefix) == 1


def test_comment_mentioning_html_is_not_html(cli, tmp_path):
    """Q-5: only content that looks like a web page is rejected."""
    (tmp_path / "robots.txt").write_text("# see <html> docs\nUser-agent: *\nDisallow: /x\n", encoding="utf-8")
    res = cli("check", str(tmp_path / "robots.txt"), "--bots", "GPTBot", "--url", "/x/y", "--out", str(tmp_path / "r.json"))
    assert res.code == 0 and "deny" in res.out
    (tmp_path / "page.txt").write_text("\n  <!DOCTYPE html>\n<html><body>No</body></html>\n", encoding="utf-8")
    res = cli("check", str(tmp_path / "page.txt"), "--out", str(tmp_path / "r.json"))
    assert res.code == 2 and "looks like HTML" in res.err
    (tmp_path / "page2.txt").write_text("<?php ?>\n<p>hi</p><html lang=en>\n", encoding="utf-8")
    assert cli("check", str(tmp_path / "page2.txt"), "--out", str(tmp_path / "r.json")).code == 2
