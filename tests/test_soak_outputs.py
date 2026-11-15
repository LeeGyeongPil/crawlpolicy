"""soak 0.1.1 regressions: the answer was right but the report was missing, or the ambiguity was never seen.

1. Every failing "report does not exist" run had a prompt ending in "Do not edit any file". The script wrote its
   report into the user's folder and the model removed or redirected it to honour that request, so the evidence
   was gone. The script now says on its last line that no input file changed and that the new files are output to
   keep; SKILL.md says the same and that --out is not to be passed.
2. task-error-ambiguous: the model passed the bare name `robots.txt` (as the SKILL.md example did), so the script
   silently judged ./robots.txt and never reported that public/robots.txt exists too.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import crawlpolicy as cp
from conftest import DEMO_INTENT

REPO = Path(__file__).resolve().parent.parent
EVALS = REPO / "evals"
SCRIPT = REPO / "skills" / "crawlpolicy" / "scripts" / "crawlpolicy.py"
SKILL = REPO / "skills" / "crawlpolicy" / "SKILL.md"


def _digest(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------------------------------------------
# 1. the last line says the inputs are untouched and which new files to keep
# ---------------------------------------------------------------------------------------------------------------
def test_check_without_fix_names_only_the_report(cli, work):
    before = _digest(work)
    res = cli("check", "robots.txt", "--bots", "GPTBot", "--url", "/")
    last = res.out.rstrip("\n").split("\n")[-1]
    assert last == ("files: no input file was changed; this run only added crawlpolicy-report.json as its record "
                    "— these are new output files, not edits: leave them in place")
    after = _digest(work)
    assert set(after) - set(before) == {"crawlpolicy-report.json"}
    assert all(after[k] == v for k, v in before.items())      # 입력 파일 바이트는 그대로


def test_check_with_fix_names_report_and_fixed_copy(cli, work):
    before = _digest(work)
    res = cli("check", "robots.txt", "--intent", DEMO_INTENT)
    assert res.code == 1
    last = res.out.rstrip("\n").split("\n")[-1]
    assert "crawlpolicy-report.json, robots.fixed.txt" in last and last.startswith("files: no input file was changed")
    after = _digest(work)
    assert set(after) - set(before) == {"crawlpolicy-report.json", "robots.fixed.txt"}
    assert all(after[k] == v for k, v in before.items())


def test_diff_names_only_the_report(cli, work):
    before = _digest(work)
    res = cli("--lang", "ko", "diff", "robots.old.txt", "robots.txt")
    last = res.out.rstrip("\n").split("\n")[-1]
    assert last.startswith("파일: 입력 파일은 하나도 바꾸지 않았고") and "crawlpolicy-report.json" in last
    assert all(_digest(work)[k] == v for k, v in before.items())


@pytest.mark.parametrize("lang,gained,lost", [("en", "(gained access)", "(lost access)"),
                                              ("ko", "(접근 허용으로 바뀜)", "(접근 차단으로 바뀜)")])
def test_diff_lines_say_gained_or_lost(cli, work, lang, gained, lost):
    res = cli("--lang", lang, "diff", "robots.old.txt", "robots.txt")
    assert "  Google-Extended /admin/users: deny @L9 -> allow @L0  " + gained in res.out
    assert "  CCBot /: allow @L0 -> deny @L7  " + lost in res.out


@pytest.mark.parametrize("argv", [
    ["check", "robots.txt", "--bots", "GPTBot", "--out", "robots.txt"],
    ["check", "robots.txt", "--intent", DEMO_INTENT, "--out", "robots.fixed.txt"],
    ["diff", "robots.old.txt", "robots.txt", "--out", "robots.old.txt"],
])
def test_out_never_overwrites_an_input_or_the_fixed_copy(cli, work, argv):
    before = _digest(work)
    res = cli(*argv)
    assert res.code == 2 and res.out == "" and res.err.count("\n") == 1
    assert res.err.startswith("error: --out ") and "choose another path" in res.err
    assert _digest(work) == before                              # 아무것도 쓰지 않았다


def test_outputs_message_exists_in_both_languages():
    for lang in cp.MESSAGES:
        assert "{files}" in cp.MESSAGES[lang]["outputs_kept"], lang
        assert "{path}" in cp.MESSAGES[lang]["err_out_clash"], lang


# ---------------------------------------------------------------------------------------------------------------
# 2. a bare `robots.txt` with a second candidate stops like a left-out path
# ---------------------------------------------------------------------------------------------------------------
@pytest.fixture
def two_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "public").mkdir()
    (tmp_path / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (tmp_path / "public" / "robots.txt").write_text("User-agent: GPTBot\nDisallow: /docs/\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("argv", [["check"], ["check", "robots.txt"]])
def test_bare_name_with_two_candidates_exits3(cli, two_files, argv):
    res = cli(*argv, "--bots", "GPTBot", "--url", "/docs/")
    assert res.code == 3 and res.out == ""
    assert res.err == ("error: found 2 robots.txt files: ./robots.txt, public/robots.txt — pass one path, "
                       "e.g. ./robots.txt\n")
    assert not (two_files / "crawlpolicy-report.json").exists()


@pytest.mark.parametrize("given,verdict", [("./robots.txt", "allow @L2"), ("public/robots.txt", "deny @L2")])
def test_an_explicit_path_is_used_as_given(cli, two_files, given, verdict):
    res = cli("check", given, "--bots", "GPTBot", "--url", "/docs/")
    assert res.code == 0 and f"GPTBot /docs/ => {verdict}" in res.out


def test_bare_name_alone_is_still_used(cli, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "robots.txt").write_text("User-agent: *\nDisallow: /x\n", encoding="utf-8")
    res = cli("check", "robots.txt", "--bots", "GPTBot", "--url", "/x")
    assert res.code == 0 and "GPTBot /x => deny @L2" in res.out


def test_ambiguous_eval_grader_hits_even_when_the_model_names_robots_txt(tmp_path):
    """Replays the opus run: `check robots.txt` in the task-error-ambiguous fixture must show the error line."""
    work = tmp_path / "task-error-ambiguous"
    shutil.copytree(EVALS / "fixtures" / "task-error-ambiguous", work)
    res = subprocess.run([sys.executable, str(SCRIPT), "check", "robots.txt", "--bots", "GPTBot", "--url", "/docs/",
                          "--lang", "en"], cwd=work, capture_output=True, text=True, env={"LANG": "C", "PATH": "/usr/bin:/bin"})
    assert res.returncode == 3
    grader = (EVALS / "task-error-ambiguous" / "graders" / "error-line.md").read_text(encoding="utf-8")
    pattern = re.search(r"^pattern: '(.*)'$", grader, re.M).group(1)
    assert re.search(pattern, res.stderr)


# ---------------------------------------------------------------------------------------------------------------
# SKILL.md says the same thing the script prints
# ---------------------------------------------------------------------------------------------------------------
def test_skill_md_check_example_names_no_file_and_keeps_outputs():
    body = SKILL.read_text(encoding="utf-8").split("\n---\n", 1)[1]
    check = next(ln for ln in body.splitlines() if ln.startswith("python3 <skill-dir>/scripts/crawlpolicy.py check"))
    assert check.split()[3] == "--intent"                      # 파일 인자 없이 자동 탐지
    assert "do not edit any file" in body.lower()
    assert "do not delete, move or redirect them and do not pass `--out`" in body
    assert "never changes the user's files" in body
