"""AC-3 / AC-7 (0.1.4): the verdict lines always reach stdout.

- Small runs print one line per cell with the winning rule text instead of the matrix.
- `--out /dev/null` keeps no report and still prints everything.
- A current folder that cannot be written gets a one-line warning; nothing is written, every verdict is printed and the
  exit code follows the verdicts. An explicit --out that cannot be written stays an input error (tests/test_regressions).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

import pytest

import crawlpolicy as cp
from conftest import DEMO_INTENT

LINE = re.compile(r"^  (?P<bot>[A-Za-z_-]+) (?P<url>\S+) => (?P<v>allow|deny) @L(?P<n>\d+)  \((?P<rule>[^)]*)\)(?P<bang> !)?$")


def _digest(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()}


def _verdict_lines(out: str) -> list[re.Match]:
    return [m for m in (LINE.match(ln) for ln in out.splitlines()) if m]


@pytest.mark.parametrize("argv,cells", [
    (["check", "robots.txt", "--bots", "GPTBot"], 5),                                          # one bot, auto URLs
    (["check", "robots.docs.txt", "--bots", "DocsHarvester/2.1", "--urls", "urls.txt"], 6),    # one bot, a URL file
    (["check", "robots.txt", "--bots", "GPTBot,ClaudeBot", "--url", "/", "--url", "/admin/x"], 4),
    (["check", "robots.txt", "--bots", "GPTBot,Googlebot"], 10),                               # <= 12 cells, auto URLs
    (["check", "robots.txt", "--url", "/", "--url", "/admin/", "--url", "/blog/", "--url", "/blog/public/"], 60),
])
def test_small_runs_print_verdict_lines_with_rule(cli, work, report, argv, cells):
    res = cli(*argv)
    assert res.code == 0, res.err
    lines = _verdict_lines(res.out)
    assert len(lines) == cells
    assert not res.out.startswith("bot ")                                       # no matrix header
    by = {(c["bot"], c["url"]): c for c in report(work)["cells"]}
    for m in lines:
        c = by[(m["bot"], m["url"])]
        assert m["v"] == ("allow" if c["allowed"] else "deny") and int(m["n"]) == (c["rule_line"] or 0)
        assert m["rule"] == (c["rule"] or "no matching rule")                    # the winning rule text as reported
        assert c["verdict"] in m.group(0)                                         # the report's verdict string verbatim


def test_verdict_lines_mark_intent_violations(cli, work):
    res = cli("check", "robots.txt", "--bots", "Google-Extended", "--intent", "control=deny")
    assert res.code == 1
    assert "  Google-Extended /admin/ => allow @L0  (no matching rule) !" in res.out.splitlines()
    assert "  Google-Extended /blog/ => deny @L10  (Disallow: /blog/)" in res.out.splitlines()


def test_rfc_decisive_cells_print_their_rule(cli, tmp_path, monkeypatch):
    """The Allow tie and the $ anchor read off the printed line (plan section 4.4)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "robots.txt").write_text("User-agent: *\nDisallow: /a\nAllow: /a/b\nDisallow: /a/b/*.csv$\n"
                                         "Allow: /a/c\nDisallow: /a/c\n", encoding="utf-8")
    res = cli("check", "--bots", "GPTBot", "--url", "/a/c/1", "--url", "/a/b/t.csv", "--url", "/a/b/t.csv?x=1")
    assert "  GPTBot /a/c/1 => allow @L5  (Allow: /a/c)" in res.out
    assert "  GPTBot /a/b/t.csv => deny @L4  (Disallow: /a/b/*.csv$)" in res.out
    assert "  GPTBot /a/b/t.csv?x=1 => allow @L3  (Allow: /a/b)" in res.out


@pytest.mark.parametrize("argv", [
    ["check", "robots.txt"],                                                      # 15 bots x 5 auto URLs = 75
    ["check", "robots.txt", "--intent", DEMO_INTENT],
    ["check", "robots.txt", "--bots", "GPTBot,ClaudeBot,CCBot"],                  # 15 cells, auto URLs
    ["check", "robots.txt", "--url", "/", "--url", "/a", "--url", "/b", "--url", "/c", "--url", "/d"],   # 75 given
])
def test_large_runs_keep_the_matrix(cli, work, argv):
    res = cli(*argv)
    first = res.out.splitlines()[0]
    assert first.startswith("bot ") and "=>" not in first
    assert not _verdict_lines(res.out)


def test_line_mode_thresholds():
    assert cp.use_verdict_lines(1, 500, False) and cp.use_verdict_lines(3, 12, False)
    assert not cp.use_verdict_lines(3, 13, False)
    assert cp.use_verdict_lines(15, 60, True) and not cp.use_verdict_lines(15, 61, True)


# ---------------------------------------------------------------------------------------------------------------
# --out /dev/null
# ---------------------------------------------------------------------------------------------------------------
def test_out_devnull_keeps_verdicts_and_writes_nothing(cli, work):
    before = _digest(work)
    res = cli("check", "robots.txt", "--bots", "GPTBot", "--url", "/", "--url", "/blog/", "--out", os.devnull)
    assert res.code == 0 and res.err == ""
    assert "  GPTBot / => deny @L7  (Disallow: /)" in res.out
    assert "report:" not in res.out
    assert res.out.rstrip("\n").split("\n")[-1] == (f"files: no input file was changed; no report was kept (--out "
                                                    f"{os.devnull}) — the lines above are the full result")
    assert _digest(work) == before
    d = cli("diff", "robots.old.txt", "robots.txt", "--out", os.devnull)
    assert d.code == 1 and "  CCBot /: allow @L0 -> deny @L7  (lost access)" in d.out
    assert d.out.rstrip("\n").split("\n")[-1].startswith("files: no input file was changed; no report was kept")
    assert _digest(work) == before
    ko = cli("--lang", "ko", "check", "robots.txt", "--bots", "GPTBot", "--out", os.devnull)
    assert ko.out.rstrip("\n").split("\n")[-1].startswith("파일: 입력 파일은 하나도 바꾸지 않았고 보고서는 남기지 않았습니다")


def test_out_devnull_with_a_fix_names_only_the_fixed_copy(cli, work):
    res = cli("check", "robots.txt", "--intent", DEMO_INTENT, "--out", os.devnull)
    assert res.code == 1 and "re-verified: 0 violations, 0 changed cells" in res.out
    assert "fixed copy: robots.fixed.txt" in res.out
    assert res.out.rstrip("\n").split("\n")[-1] == (f"files: no input file was changed; no report was kept (--out "
                                                    f"{os.devnull}) and this run only added the fixed copy "
                                                    "robots.fixed.txt — the lines above are the full result")
    assert not (work / "crawlpolicy-report.json").exists() and (work / "robots.fixed.txt").is_file()
    again = cli("check", "robots.txt", "--intent", DEMO_INTENT, "--out", os.devnull)
    assert "(robots.fixed.txt already existed and was replaced)" in again.out.splitlines()[-1]


# ---------------------------------------------------------------------------------------------------------------
# the current folder cannot be written
# ---------------------------------------------------------------------------------------------------------------
def _assert_full_result_without_files(res, folder: Path, before: dict) -> None:
    assert res.code == 1                                                    # the verdicts decide, not the write
    assert "  ! Google-Extended /admin/users => allow @L0  (wanted deny by 'all:/admin/=deny')" in res.out
    assert "re-verified: 0 violations, 0 changed cells" in res.out and "+Disallow: /admin/" in res.out
    assert "fixed copy:" not in res.out and "report:" not in res.out
    assert res.out.rstrip("\n").split("\n")[-1] == ("files: no input file was changed and nothing was written — the "
                                                    "lines above are the full result")
    assert res.err.count("\n") == 1
    assert re.match(r"warning: cannot write crawlpolicy-report\.json here \(.+\) — nothing was written; the verdicts "
                    r"above are complete\n$", res.err)
    assert _digest(folder) == before


def test_unwritable_default_report_still_prints_verdicts(cli, work, monkeypatch):
    """Simulated (works everywhere, also as root): the default report path is refused by the file system."""
    real = cp.unwritable_reason
    monkeypatch.setattr(cp, "unwritable_reason", lambda p: "Read-only file system" if not os.path.isabs(p) else real(p))
    before = _digest(work)
    _assert_full_result_without_files(cli("check", "robots.txt", "--intent", DEMO_INTENT), work, before)
    d = cli("diff", "robots.old.txt", "robots.txt")
    assert d.code == 1 and "7 of 75 cells changed:" in d.out and d.err.startswith("warning: cannot write")
    assert d.out.rstrip("\n").split("\n")[-1].startswith("files: no input file was changed and nothing was written")
    small = cli("check", "robots.txt", "--bots", "GPTBot", "--url", "/blog/")
    assert small.code == 0 and "  GPTBot /blog/ => deny @L7  (Disallow: /)" in small.out
    ko = cli("--lang", "ko", "check", "robots.txt", "--bots", "GPTBot", "--url", "/blog/")
    assert ko.err.startswith("경고: 여기에 crawlpolicy-report.json 를 쓸 수 없습니다")
    assert _digest(work) == before


def test_unwritable_default_report_in_a_read_only_folder(cli, work):
    """The real case: a read-only current folder (skipped where the permission bits do not stop writes, e.g. root)."""
    before = _digest(work)
    work.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(work, os.W_OK):
            pytest.skip("permission bits do not stop writes here")
        res = cli("check", "robots.txt", "--intent", DEMO_INTENT)
    finally:
        work.chmod(stat.S_IRWXU)
    _assert_full_result_without_files(res, work, before)


def test_explicit_paths_that_cannot_be_written_stay_input_errors(cli, work):
    before = _digest(work)
    res = cli("check", "robots.txt", "--intent", DEMO_INTENT, "--fixed-out", "no/such/dir/f.txt")
    assert res.code == 2 and res.out == "" and res.err.startswith("error: cannot write no/such/dir/f.txt")
    assert _digest(work) == before


def test_a_late_write_failure_leaves_no_new_file(tmp_path):
    """write_outputs removes what this run created when a later write fails (a disk filling up mid-run)."""
    first = tmp_path / "a.json"
    blocked = cp.write_outputs([(str(first), "{}", False), (str(tmp_path / "missing" / "b.txt"), "x", False)], [])
    assert blocked and blocked[0].endswith("b.txt") and not first.exists()
    with pytest.raises(cp.CliError):
        cp.write_outputs([(str(first), "{}", True), (str(tmp_path / "missing" / "b.txt"), "x", True)], [])
    assert not first.exists()


# ---------------------------------------------------------------------------------------------------------------
# both languages
# ---------------------------------------------------------------------------------------------------------------
NEW_KEYS = ("rule_none", "outputs_no_report", "outputs_no_report_fixed", "outputs_no_report_fixed_replaced",
            "outputs_nothing", "warn_not_written")


def test_new_messages_exist_in_both_languages(cli, work):
    for lang in ("en", "ko"):
        for key in NEW_KEYS:
            assert cp.MESSAGES[lang][key].strip(), (lang, key)
    assert cp.MESSAGES["ko"]["rule_none"] == "해당 규칙 없음"
    ko = cli("--lang", "ko", "check", "robots.txt", "--bots", "GPTBot,OAI-SearchBot", "--url", "/blog/")
    assert "  OAI-SearchBot /blog/ => allow @L0  (해당 규칙 없음)" in ko.out
    assert "no matching rule" not in ko.out
    en = cli("--lang", "en", "check", "robots.txt", "--bots", "GPTBot,OAI-SearchBot", "--url", "/blog/")
    assert "  OAI-SearchBot /blog/ => allow @L0  (no matching rule)" in en.out
    assert json.loads((work / "crawlpolicy-report.json").read_text(encoding="utf-8"))["cells"][1]["rule"] is None
