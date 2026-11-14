"""AC-9: the eval set has the planned shape and its task graders check values only the script can produce."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
EVALS = REPO / "evals"
SCRIPT = REPO / "skills" / "crawlpolicy" / "scripts" / "crawlpolicy.py"


def _front(path: Path) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", path.read_text(encoding="utf-8"), re.S)
    assert m, path
    fm = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        fm[k.strip()] = v[1:-1] if len(v) > 1 and v[0] == v[-1] == "'" else v
    return fm, m.group(2).strip()


def _cases(prefix: str) -> list[Path]:
    return sorted(p for p in EVALS.iterdir() if p.is_dir() and p.name.startswith(prefix))


def test_eval_set_composition():
    triggers = _cases("trigger-")
    near = _cases("notrigger-near-")
    far = [p for p in _cases("notrigger-") if not p.name.startswith("notrigger-near-")]
    tasks = _cases("task-")
    assert (len(triggers), len(near), len(far), len(tasks)) == (12, 6, 5, 7)
    assert len([t for t in tasks if t.name.startswith("task-error-")]) == 2
    korean = [t for t in triggers if re.search("[가-힣]", _front(t / "prompt.md")[1])]
    assert len(korean) == 3
    for case in triggers + near + far + tasks:
        fm, body = _front(case / "prompt.md")
        assert fm["max_turns"] == "6" and fm["name"] == case.name and body, case.name
    for case in triggers:
        body = _front(case / "prompt.md")[1]
        assert not re.search(r"crawlpolicy|\bskill\b|스킬", body, re.I), case.name
        fm, _ = _front(case / "graders" / "skill-fired.md")
        assert fm["tool"] == "Skill" and "crawlpolicy" in fm["input_match"] and fm["arm"] == "both"
    for case in near + far:
        fm, _ = _front(case / "graders" / "skill-not-fired.md")
        assert fm["tool"] == "Skill" and fm["max"] == "0" and fm["arm"] == "both"
    for case in tasks:
        fm, _ = _front(case / "graders" / "script-ran.md")
        assert fm["tool"] == "Bash" and fm["arm"] == "with-only" and "scripts/crawlpolicy" in fm["input_match"]
        assert (EVALS / "fixtures" / case.name).is_dir()


def test_task_graders_use_report_values():
    for case in _cases("task-"):
        graders = [p for p in (case / "graders").glob("*.md") if p.stem != "script-ran"]
        assert graders, case.name
        if not case.name.startswith("task-error-"):
            assert any(g.stem.startswith("answer-") for g in graders), case.name      # Q-6: the baseline can score on facts
        for g in graders:
            fm, _ = _front(g)
            assert fm["type"] == "regex" and len(fm["pattern"]) >= 12 or g.stem.startswith("answer-"), g
            if g.stem.startswith("answer-"):
                assert "target" not in fm, g          # answer graders judge the final reply, baseline arm included
                continue
            if case.name.startswith("task-error-"):
                assert fm["target"] == "trace", g
            else:
                assert fm["target"] == "{source: file, path: crawlpolicy-report.json}", g
        if not case.name.startswith("task-error-"):
            patterns = " ".join(_front(g)[0]["pattern"] for g in graders if not g.stem.startswith("answer-"))
            assert re.search(r'"sha256": "[0-9a-f]{64}"', patterns), case.name     # only the script can produce this
            assert "verdict" in patterns or "mode" in patterns or "token" in patterns


def _run(folder: Path, *args: str):
    return subprocess.run([sys.executable, str(SCRIPT), *args, "--lang", "en"], cwd=folder, capture_output=True, text=True,
                          env={"LANG": "C", "PATH": "/usr/bin:/bin"})


@pytest.mark.parametrize("name,args", [
    ("task-longest-match", ["check", "--bots", "GPTBot", "--url", "/shop/public/a.html"]),
    ("task-inherit-gap", ["check", "--intent", "training=deny; control=deny; search=allow; user=allow; all:/admin/=deny"]),
    ("task-token-exact", ["check", "--bots", "Applebot,Applebot-Extended", "--url", "/"]),
    ("task-collector", ["check", "--bots", "DocsHarvester/2.1", "--urls", "urls.txt"]),
    ("task-diff", ["diff", "robots.old.txt", "robots.txt"]),
])
def test_every_report_grader_matches_the_real_script_output(tmp_path, name, args):
    """Run the script on the fixture and confirm each grader regex hits the report it was written against."""
    work = tmp_path / name
    shutil.copytree(EVALS / "fixtures" / name, work)
    _run(work, *args)
    report = (work / "crawlpolicy-report.json").read_text(encoding="utf-8")
    for g in (EVALS / name / "graders").glob("*.md"):
        if g.stem == "script-ran" or g.stem.startswith("answer-"):
            continue
        pattern = _front(g)[0]["pattern"]
        assert re.search(pattern, report), (name, g.stem)
    sha = hashlib.sha256((work / ("robots.txt")).read_bytes()).hexdigest()
    assert sha in report


@pytest.mark.parametrize("follow_up", [
    ["check", "robots.fixed.txt", "--intent", "training=deny; control=deny; search=allow; user=allow; all:/admin/=deny"],
    ["check", "robots.fixed.txt", "--bots", "Google-Extended", "--url", "/admin/users"],
    ["diff", "robots.txt", "robots.fixed.txt"],
])
def test_inherit_gap_graders_survive_a_second_run_on_the_fixed_copy(tmp_path, follow_up):
    """soak regression: a model re-checked robots.fixed.txt after the fix run and the report for robots.txt was
    replaced, so gap-verdict and source-sha256 failed. The report for the user's file must survive that run."""
    work = tmp_path / "task-inherit-gap"
    shutil.copytree(EVALS / "fixtures" / "task-inherit-gap", work)
    first = _run(work, "check", "--intent", "training=deny; control=deny; search=allow; user=allow; all:/admin/=deny")
    assert first.returncode == 1
    second = _run(work, *follow_up)
    assert "crawlpolicy-report.fixed.json" in second.stdout and "is kept" in second.stdout
    report = (work / "crawlpolicy-report.json").read_text(encoding="utf-8")
    for g in (EVALS / "task-inherit-gap" / "graders").glob("*.md"):
        if g.stem == "script-ran" or g.stem.startswith("answer-"):
            continue
        assert re.search(_front(g)[0]["pattern"], report), g.stem
    assert (work / "crawlpolicy-report.fixed.json").is_file()


@pytest.mark.parametrize("name,code,expect", [
    ("task-error-html", 2, r"not a robots\.txt file: \S*robots\.txt \(looks like HTML\)"),
    ("task-error-ambiguous", 3, r"found 2 robots\.txt files: \./robots\.txt, public/robots\.txt"),
])
def test_error_graders_match_the_error_line(tmp_path, name, code, expect):
    work = tmp_path / name
    shutil.copytree(EVALS / "fixtures" / name, work)
    res = _run(work, "check", "--bots", "GPTBot", "--url", "/docs/")
    assert res.returncode == code
    pattern = _front(next(g for g in (EVALS / name / "graders").glob("*.md") if g.stem != "script-ran"))[0]["pattern"]
    assert re.search(pattern, res.stderr) and re.search(expect, res.stderr)


def test_fixtures_have_no_dotfiles():
    for f in (EVALS / "fixtures").rglob("*"):
        assert not any(part.startswith(".") for part in f.relative_to(EVALS / "fixtures").parts), f


ANSWERS = {
    "task-inherit-gap": ("Google-Extended can still fetch /admin/users because its own group has no /admin/ rule.",
                         "Everything looks fine, no changes needed."),
    "task-token-exact": ("Applebot is allowed; Applebot-Extended is denied by line 5.", "Both are allowed."),
    "task-longest-match": ("Allowed, decided by line 3 (the longer Allow rule).", "Blocked by the Disallow rule."),
    "task-collector": ("It may not fetch /café/menu or the PDF; the other URLs are fine.", "All six URLs are fine."),
    "task-diff": ("Google-Extended gained access to /admin/users; CCBot lost the home page.", "Nothing changed."),
}


@pytest.mark.parametrize("name", sorted(ANSWERS))
def test_answer_graders_judge_facts_in_the_reply(name):
    """Q-6: the final reply is graded for every task, so the baseline arm is scored on substance, not on the report."""
    good, bad = ANSWERS[name]
    graders = [g for g in (EVALS / name / "graders").glob("answer-*.md")]
    assert graders
    for g in graders:
        fm, _ = _front(g)
        flags = re.I if "i" in fm.get("flags", "") else 0
        assert re.search(fm["pattern"], good, flags), (name, g.stem)
        assert not re.search(fm["pattern"], bad, flags), (name, g.stem)
