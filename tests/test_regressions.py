"""Regression tests for the review findings: large input speed, usage errors in both languages, help text, HTML check."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import pytest

import crawlpolicy as cp
from conftest import DEMO_INTENT


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


# ---------------------------------------------------------------------------------------------------------------
# soak regression (task-inherit-gap): a second run on the fixed copy replaced the report for the user's file.
# ---------------------------------------------------------------------------------------------------------------
def _first_fix_run(cli, report):
    res = cli("check", "robots.txt", "--intent", DEMO_INTENT)
    assert res.code == 1
    assert "already re-checked by this run" in res.out       # the fix run itself says no second run is needed
    return report(Path("."))


def test_recheck_of_fixed_copy_keeps_the_original_report(cli, work, report):
    before = _first_fix_run(cli, report)
    again = cli("check", "robots.fixed.txt", "--intent", DEMO_INTENT)
    assert again.code == 0 and "Intent holds" in again.out
    assert "robots.fixed.txt is the fixed copy that crawlpolicy-report.json already re-verified" in again.out
    assert again.out.rstrip().endswith("report: crawlpolicy-report.fixed.json")
    assert report(work) == before                              # the evidence for robots.txt is untouched
    side = json.loads((work / "crawlpolicy-report.fixed.json").read_text(encoding="utf-8"))
    assert side["source"]["path"] == "robots.fixed.txt" and side["source"]["sha256"] == before["fix"]["sha256"]


def test_diff_against_fixed_copy_keeps_the_original_report(cli, work, report):
    before = _first_fix_run(cli, report)
    res = cli("--lang", "ko", "diff", "robots.txt", "robots.fixed.txt")
    assert res.code == 1
    assert "참고: robots.fixed.txt 는 이미 crawlpolicy-report.json 에서 재검증한 수정본입니다" in res.out
    assert report(work) == before
    assert json.loads((work / "crawlpolicy-report.fixed.json").read_text(encoding="utf-8"))["mode"] == "diff"


def test_explicit_out_is_always_honoured(cli, work, report):
    _first_fix_run(cli, report)
    res = cli("check", "robots.fixed.txt", "--intent", DEMO_INTENT, "--out", "crawlpolicy-report.json")
    assert "is kept" not in res.out
    assert report(work)["source"]["path"] == "robots.fixed.txt"
    assert not (work / "crawlpolicy-report.fixed.json").exists()


def test_edited_fixed_copy_gets_a_fresh_default_report(cli, work, report):
    """Once the user changes the fixed copy it is their new file, so the usual default report applies."""
    _first_fix_run(cli, report)
    fixed = work / "robots.fixed.txt"
    fixed.write_text(fixed.read_text(encoding="utf-8") + "\nUser-agent: ExtraBot\nDisallow: /x/\n", encoding="utf-8")
    res = cli("check", "robots.fixed.txt", "--intent", DEMO_INTENT)
    assert "is kept" not in res.out
    assert report(work)["source"]["path"] == "robots.fixed.txt"


@pytest.mark.parametrize("old", ["{not json", "[]", '{"mode": "diff", "fix": null}', '{"mode": "check", "fix": 3}'])
def test_unreadable_previous_report_is_simply_replaced(cli, work, report, old):
    (work / "crawlpolicy-report.json").write_text(old, encoding="utf-8")
    res = cli("check", "robots.txt", "--intent", DEMO_INTENT)
    assert res.code == 1 and "is kept" not in res.out
    assert report(work)["source"]["path"] == "robots.txt"


def test_fixed_copy_bytes_match_the_reported_sha256(cli, work, report):
    rep = _first_fix_run(cli, report)
    assert hashlib.sha256((work / "robots.fixed.txt").read_bytes()).hexdigest() == rep["fix"]["sha256"]


def test_kept_report_message_exists_in_both_languages():
    for lang in cp.MESSAGES:
        msg = cp.MESSAGES[lang]["kept_report"]
        for field in ("{path}", "{kept}", "{src}", "{out}"):
            assert field in msg, (lang, field)
