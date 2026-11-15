"""AC-9: the eval set has the planned shape and its graders check what only the script prints.

0.1.4 (plan v3): every soak failure of 0.1.1-0.1.3 was a `target: file` grader on the report in a task whose prompt says
"Do not edit any file" — the answer graders always passed. Those tasks now grade the script's stdout lines in the
trace (verdict lines with the winning rule line, diff lines, the cell count). Only task-inherit-gap, which asks for a
corrected file, keeps file graders. Grader regexes must match the real script output for their fixture and must not
match any shipped text (SKILL.md, READMEs, CHANGELOG, samples, docs, prompts, fixtures), so reading those cannot pass.
"""
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
NO_EDIT = "Do not edit any file"
FILE_TARGET = "{source: file, path: crawlpolicy-report.json}"

# The command an agent following SKILL.md runs for each task (the file argument is left out, as SKILL.md says).
TASK_RUNS = {
    "task-longest-match": ["check", "--bots", "GPTBot", "--url", "/shop/public/a.html", "--url", "/shop/public/data.json",
                           "--url", "/shop/public/data.json?v=2", "--url", "/shop/sale/x"],
    "task-token-exact": ["check", "--bots", "Applebot,Applebot-Extended", "--url", "/", "--url",
                         "/private/press/kit.html"],
    "task-collector": ["check", "--bots", "DocsHarvester/2.1", "--urls", "urls.txt"],
    "task-diff": ["diff", "robots.old.txt", "robots.txt"],
}
REDESIGNED = sorted(TASK_RUNS)


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


def _graders(case: str) -> list[tuple[str, dict]]:
    return [(g.stem, _front(g)[0]) for g in sorted((EVALS / case / "graders").glob("*.md")) if g.stem != "script-ran"]


def _flags(fm: dict) -> int:
    return re.I if "i" in fm.get("flags", "") else 0


def _run(folder: Path, *args: str):
    return subprocess.run([sys.executable, str(SCRIPT), *args, "--lang", "en"], cwd=folder, capture_output=True, text=True,
                          env={"LANG": "C", "PATH": "/usr/bin:/bin"})


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


def test_no_file_graders_when_prompt_forbids_edits():
    """A prompt that says "Do not edit any file" is never graded on a file the agent may legitimately not keep."""
    forbidding = [c.name for c in _cases("task-") if NO_EDIT in _front(c / "prompt.md")[1]]
    assert sorted(forbidding) == sorted(REDESIGNED + ["task-error-ambiguous", "task-error-html"])
    for case in forbidding:
        for name, fm in _graders(case):
            assert fm.get("target") in (None, "trace"), (case, name)
    keep = _graders("task-inherit-gap")
    assert NO_EDIT not in _front(EVALS / "task-inherit-gap" / "prompt.md")[1]
    assert [n for n, fm in keep if fm.get("target") == FILE_TARGET]       # the only task that asks for a file


def test_grader_kinds_per_task():
    for case in [c.name for c in _cases("task-")]:
        graders = _graders(case)
        for name, fm in graders:
            assert fm["type"] == "regex", (case, name)
            if name.startswith("answer-"):
                assert "target" not in fm, (case, name)       # answer graders judge the final reply, both arms
            else:
                assert fm["target"] in ("trace", FILE_TARGET), (case, name)
                assert len(fm["pattern"]) >= 12, (case, name)
        if case in REDESIGNED:
            trace = [n for n, fm in graders if fm.get("target") == "trace"]
            assert len(trace) >= 3, case
            assert any(n.startswith("answer-pasted-") for n, _ in graders), case     # the pasted printed line
            assert any(n.startswith("answer-") and not n.startswith("answer-pasted-") for n, _ in graders), case
            # every trace grader names an exact deciding line, a diff line or the exact cell count
            for n, fm in graders:
                if fm.get("target") == "trace":
                    assert re.search(r"@L\d|cells changed|cells: \d", fm["pattern"]), (case, n)


@pytest.mark.parametrize("name", REDESIGNED)
def test_trace_graders_match_real_stdout(tmp_path, name):
    """Run the script on the fixture: each trace grader and each pasted-line grader hits stdout+stderr."""
    work = tmp_path / name
    shutil.copytree(EVALS / "fixtures" / name, work)
    res = _run(work, *TASK_RUNS[name])
    trace = res.stdout + res.stderr
    assert res.returncode in (0, 1), trace
    for g, fm in _graders(name):
        if fm.get("target") == "trace" or g.startswith("answer-pasted-"):
            assert re.search(fm["pattern"], trace, _flags(fm)), (name, g, trace)


def _shipped_texts() -> dict[str, str]:
    texts = {}
    for p in [REPO / "skills" / "crawlpolicy" / "SKILL.md", REPO / "README.md", REPO / "README.ko.md", REPO / "CHANGELOG.md"]:
        texts[p.name] = p.read_text(encoding="utf-8")
    for root in (REPO / "samples", REPO / "docs", EVALS / "fixtures"):
        for p in sorted(root.rglob("*")):
            if p.is_file():
                try:
                    texts[str(p.relative_to(REPO))] = p.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue                                  # screenshots: no text to read
    for p in sorted(EVALS.glob("*/prompt.md")):
        texts[str(p.relative_to(REPO))] = _front(p)[1]
    return texts


def _leak_checked() -> list[tuple[str, str, dict]]:
    """Every trace grader, plus every answer grader of the redesigned tasks.

    task-inherit-gap's answer grader is kept from v2 on purpose (plan v3): its fixture is the README sample, so its
    plain-words fact (Google-Extended reaches /admin) is the README's lesson; it grades the final reply only."""
    out = []
    for case in [c.name for c in _cases("task-")]:
        for g, fm in _graders(case):
            if fm.get("target") == "trace" or (case in REDESIGNED and g.startswith("answer-")):
                out.append((case, g, fm))
    return out


@pytest.mark.parametrize("case,grader,fm", _leak_checked(), ids=lambda v: v if isinstance(v, str) else "")
def test_graders_do_not_match_shipped_texts(case, grader, fm):
    """Reading SKILL.md, the READMEs, CHANGELOG, samples, docs, a prompt or a fixture must not satisfy a grader."""
    for where, text in _shipped_texts().items():
        assert not re.search(fm["pattern"], text, _flags(fm)), (case, grader, where)


def test_every_report_grader_matches_the_real_script_output(tmp_path):
    """task-inherit-gap still grades the report it asks the agent to keep."""
    work = tmp_path / "task-inherit-gap"
    shutil.copytree(EVALS / "fixtures" / "task-inherit-gap", work)
    _run(work, "check", "--intent", "training=deny; control=deny; search=allow; user=allow; all:/admin/=deny")
    report = (work / "crawlpolicy-report.json").read_text(encoding="utf-8")
    for g, fm in _graders("task-inherit-gap"):
        if fm.get("target") == FILE_TARGET:
            assert re.search(fm["pattern"], report), g
    assert hashlib.sha256((work / "robots.txt").read_bytes()).hexdigest() in report


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
    for g, fm in _graders("task-inherit-gap"):
        if fm.get("target") == FILE_TARGET:
            assert re.search(fm["pattern"], report), g
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


ANSWERS = {   # (replies that state the fact in the usual shapes: sentence, table, bullets) / (replies that miss it)
    "task-inherit-gap": (["Google-Extended can still fetch /admin/users because its own group has no /admin/ rule."],
                         ["Everything looks fine, no changes needed."]),
    "task-token-exact": (["Applebot may fetch / and /private/press/kit.html; Applebot-Extended is denied by line 5.",
                          "| Bot | / |\n|---|---|\n| Applebot | allowed |\n| Applebot-Extended | **blocked** (line 5) |",
                          "**Applebot-Extended**\n- `/`: not allowed, `Disallow: /` on line 5"],
                         ["Both bots are allowed everywhere.", "Applebot-Extended is allowed on / and kit.html."]),
    "task-longest-match": (["/shop/public/a.html is allowed, decided by line 3 (the longer Allow rule).",
                            "| /shop/public/a.html | allowed | L3 `Allow: /shop/public` |"],
                           ["Blocked by the Disallow rule.", "/shop/public/a.html is blocked by line 2."]),
    "task-collector": (["It may not fetch /crêpes/list or the EPUB; the other URLs are fine.",
                        "- `/crêpes/list` — blocked (line 4, `Disallow: /cr%C3%AApes/`)"],
                       ["All seven URLs are fine."]),
    "task-diff": (["Applebot-Extended gained access to /checkout/; PerplexityBot lost the home page.",
                   "**Gained access**\n- Applebot-Extended:\n  - `/`\n  - `/checkout/`\n  - `/cart/`",
                   "| Applebot-Extended | /checkout/ | deny -> allow |"],
                  ["Nothing changed.", "PerplexityBot lost access to / and /recipes/."]),
}


@pytest.mark.parametrize("name", sorted(ANSWERS))
def test_answer_graders_judge_facts_in_the_reply(name):
    """The final reply is graded for every task, so the baseline arm is scored on substance, not on a file."""
    goods, bads = ANSWERS[name]
    graders = [(g, fm) for g, fm in _graders(name) if g.startswith("answer-") and not g.startswith("answer-pasted-")]
    assert graders
    for g, fm in graders:
        for good in goods:
            assert re.search(fm["pattern"], good, _flags(fm)), (name, g, good)
        for bad in bads:
            assert not re.search(fm["pattern"], bad, _flags(fm)), (name, g, bad)
