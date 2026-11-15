"""Regression tests for the review findings: large input speed, usage errors in both languages, help text, HTML check."""
from __future__ import annotations

import hashlib
import json
import os
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
    assert "\nreport: crawlpolicy-report.fixed.json\n" in again.out
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


# ---------------------------------------------------------------------------------------------------------------
# 0.1.3 output guard: no output path may land on an input file or on the other output, the error names the option
# that has to change, and the files: line says when an existing output was replaced.
# ---------------------------------------------------------------------------------------------------------------
BOTS_JSON = '[{"token": "GPTBot", "role": "training"}, {"token": "Googlebot", "role": "search"}]\n'


def _digest(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()}


GUARD_CASES = {
    # --fixed-out over every kind of input (qa-fresh F-1: --urls and --bots-file were not checked)
    "fixed-out-on-urls": (["check", "robots.txt", "--intent", "training=deny", "--urls", "urls.txt",
                           "--fixed-out", "urls.txt"],
                          "error: --fixed-out urls.txt would overwrite the --urls file — choose another path"),
    "fixed-out-on-bots-file": (["check", "robots.txt", "--intent", "search=deny", "--bots-file", "bots.json",
                                "--fixed-out", "bots.json"],
                               "error: --fixed-out bots.json would overwrite the --bots-file file — choose another path"),
    "fixed-out-on-robots": (["check", "robots.txt", "--intent", "training=deny", "--fixed-out", "./robots.txt"],
                            "error: --fixed-out ./robots.txt would overwrite the input file — choose another path"),
    # --out over every kind of input, in check and diff
    "out-on-urls": (["check", "robots.txt", "--urls", "urls.txt", "--out", "urls.txt"],
                    "error: --out urls.txt would overwrite the --urls file — choose another path"),
    "out-on-bots-file": (["check", "robots.txt", "--bots-file", "bots.json", "--out", "bots.json"],
                         "error: --out bots.json would overwrite the --bots-file file — choose another path"),
    "diff-out-on-urls": (["diff", "robots.old.txt", "robots.txt", "--urls", "urls.txt", "--out", "urls.txt"],
                         "error: --out urls.txt would overwrite the --urls file — choose another path"),
    "diff-out-on-new": (["diff", "robots.old.txt", "robots.txt", "--out", "robots.txt"],
                        "error: --out robots.txt would overwrite the input file — choose another path"),
    # report and fixed copy on one path: blame the option the user typed (qa-fresh F-2, F-3)
    "out-on-fixed-copy-without-fix": (["check", "robots.txt", "--bots", "GPTBot", "--url", "/", "--out",
                                       "robots.fixed.txt"],
                                      "error: --out robots.fixed.txt would overwrite the fixed copy (--fixed-out) — "
                                      "choose another path"),
    "fixed-out-on-report": (["check", "robots.txt", "--intent", "training=deny", "--fixed-out",
                             "crawlpolicy-report.json"],
                            "error: --fixed-out crawlpolicy-report.json would overwrite the report (--out) — "
                            "choose another path"),
    "fixed-out-on-explicit-report": (["check", "robots.txt", "--bots", "GPTBot", "--url", "/", "--out", "r.json",
                                      "--fixed-out", "r.json"],
                                     "error: --fixed-out r.json would overwrite the report (--out) — "
                                     "choose another path"),
    # an explicit --out never replaces a file that is not a crawlpolicy report
    "out-on-user-notes": (["check", "robots.txt", "--bots", "GPTBot", "--url", "/", "--out", "notes.md"],
                          "error: --out notes.md already exists and is not a crawlpolicy report — choose another path"),
}


@pytest.fixture
def guarded(work):
    (work / "bots.json").write_text(BOTS_JSON, encoding="utf-8")
    (work / "notes.md").write_text("my own notes\n", encoding="utf-8")
    return work


@pytest.mark.parametrize("name", list(GUARD_CASES))
def test_every_output_is_checked_against_every_input(cli, guarded, name):
    argv, expect = GUARD_CASES[name]
    before = _digest(guarded)
    res = cli(*argv)
    assert res.code == 2 and res.out == "", res.out
    assert res.err == expect + "\n"
    assert _digest(guarded) == before                           # 거부된 실행은 아무 파일도 쓰거나 바꾸지 않는다


def test_guard_errors_are_translated(cli, guarded):
    res = cli("check", "robots.txt", "--intent", "training=deny", "--urls", "urls.txt", "--fixed-out", "urls.txt",
              "--lang", "ko")
    assert res.code == 2
    assert res.err == "오류: --fixed-out urls.txt 는 --urls 파일 을(를) 덮어씁니다 — 다른 경로를 고르세요\n"


def test_out_on_an_existing_fixed_copy_is_refused_even_without_a_fix(cli, work, report):
    """qa-fresh F-2: a later run with no violation must not turn the earlier fixed copy into a JSON report."""
    _first_fix_run(cli, report)
    fixed = (work / "robots.fixed.txt").read_bytes()
    res = cli("check", "robots.txt", "--bots", "GPTBot", "--url", "/", "--out", "robots.fixed.txt")
    assert res.code == 2 and res.err.startswith("error: --out robots.fixed.txt would overwrite the fixed copy")
    assert (work / "robots.fixed.txt").read_bytes() == fixed
    res = cli("diff", "robots.old.txt", "robots.txt", "--out", "robots.fixed.txt")
    assert res.code == 2 and res.err.startswith("error: --out robots.fixed.txt already exists and is not")
    assert (work / "robots.fixed.txt").read_bytes() == fixed


def test_fixed_out_never_replaces_an_earlier_report(cli, work, report):
    before = _first_fix_run(cli, report)
    res = cli("check", "robots.txt", "--intent", "training=deny", "--out", "second.json",
              "--fixed-out", "crawlpolicy-report.json")
    assert res.code == 2
    assert res.err == ("error: --fixed-out crawlpolicy-report.json would overwrite an earlier crawlpolicy report — "
                       "choose another path\n")
    assert report(work) == before


def test_hard_link_to_an_input_is_the_same_file(cli, work):
    """Another name for the same file (hard link, or other letter case on a case-insensitive disk) is still an input."""
    try:
        os.link(work / "urls.txt", work / "alias.txt")
    except OSError:
        pytest.skip("hard links are not available here")
    before = _digest(work)
    res = cli("check", "robots.txt", "--intent", "training=deny", "--urls", "urls.txt", "--fixed-out", "alias.txt")
    assert res.code == 2 and "would overwrite the --urls file" in res.err
    assert _digest(work) == before


def test_explicit_out_may_replace_its_own_earlier_report(cli, work):
    assert cli("check", "robots.txt", "--bots", "GPTBot", "--url", "/", "--out", "mine.json").code == 0
    res = cli("check", "robots.txt", "--bots", "GPTBot", "--url", "/x", "--out", "mine.json")
    assert res.code == 0
    assert json.loads((work / "mine.json").read_text(encoding="utf-8"))["urls"] == ["/x"]
    assert res.out.rstrip("\n").split("\n")[-1].endswith("(mine.json already existed and was replaced) — these are "
                                                           "output files, not edits: leave them in place")


def test_unwritable_report_leaves_no_fixed_copy_behind(cli, work):
    before = _digest(work)
    res = cli("check", "robots.txt", "--intent", "training=deny", "--out", "no/such/dir/r.json")
    assert res.code == 2 and res.err.startswith("error: cannot write no/such/dir/r.json")
    assert _digest(work) == before                              # 보고서를 못 쓰면 수정본도 쓰지 않는다


# qa.json Q-16: the files: line said "only added" while it replaced an existing robots.fixed.txt or report
def test_files_line_names_replaced_outputs(cli, work):
    (work / "robots.fixed.txt").write_text("my own notes\n", encoding="utf-8")
    res = cli("check", "robots.txt", "--intent", "training=deny")
    assert res.code == 1
    last = res.out.rstrip("\n").split("\n")[-1]
    assert last == ("files: no input file was changed; this run wrote crawlpolicy-report.json, robots.fixed.txt as "
                    "its record (robots.fixed.txt already existed and was replaced) — these are output files, not "
                    "edits: leave them in place")
    res = cli("--lang", "ko", "check", "robots.txt", "--intent", "training=deny")
    last = res.out.rstrip("\n").split("\n")[-1]
    assert last == ("파일: 입력 파일은 하나도 바꾸지 않았고, 이번 실행의 기록으로 crawlpolicy-report.json, robots.fixed.txt "
                    "를 썼습니다 (이미 있던 crawlpolicy-report.json, robots.fixed.txt 는 새 내용으로 바꿈) — 편집이 아니라 "
                    "출력 파일이니 그대로 두세요")


def test_diff_files_line_names_a_replaced_report(cli, work):
    first = cli("diff", "robots.old.txt", "robots.txt")
    assert "only added crawlpolicy-report.json" in first.out
    again = cli("diff", "robots.old.txt", "robots.txt")
    assert "(crawlpolicy-report.json already existed and was replaced)" in again.out


def test_files_line_messages_exist_in_both_languages():
    for lang in cp.MESSAGES:
        assert "{files}" in cp.MESSAGES[lang]["outputs_replaced"] and "{replaced}" in cp.MESSAGES[lang]["outputs_replaced"]
        for field in ("{opt}", "{path}", "{what}"):
            assert field in cp.MESSAGES[lang]["err_out_clash"], (lang, field)
