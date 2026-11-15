"""AC-9: replay the ways the report went missing in the 0.1.1-0.1.3 soak runs and check the trace graders still pass.

The traces of the failed runs were deleted, so which path the agent took is unknown: `--out` somewhere else, `--out
/dev/null` (refused by 0.1.3), the script run from another folder with an absolute path, the report deleted right after
the run, or a folder the script could not write. In every case the verdict lines must reach stdout, because the trace
graders read the script's stdout and stderr from the trace — not a file.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from test_evals_layout import EVALS, TASK_RUNS, _flags, _graders

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "crawlpolicy" / "scripts" / "crawlpolicy.py"
MODES = ["plain", "out-elsewhere", "out-devnull", "other-folder-absolute", "report-deleted", "read-only-folder"]
FILE_ARGS = {"urls.txt", "robots.old.txt", "robots.txt"}       # fixture files a run names


def _trace(cwd: Path, argv: list[str]) -> tuple[int, str]:
    res = subprocess.run([sys.executable, str(SCRIPT), *argv, "--lang", "en"], cwd=cwd, capture_output=True, text=True,
                         env={"LANG": "C", "PATH": "/usr/bin:/bin"})
    return res.returncode, res.stdout + res.stderr


def _replay(tmp_path: Path, name: str, mode: str, argv: list[str]) -> tuple[int, str]:
    work = tmp_path / name
    shutil.copytree(EVALS / "fixtures" / name, work)
    if mode == "out-elsewhere":
        return _trace(work, argv + ["--out", str(tmp_path / "elsewhere.json")])
    if mode == "out-devnull":
        return _trace(work, argv + ["--out", os.devnull])
    if mode == "other-folder-absolute":
        other = tmp_path / "other"
        other.mkdir()
        absolute = [str(work / a) if a in FILE_ARGS else a for a in argv]
        if absolute[0] == "check" and not any(a.endswith("robots.txt") for a in absolute):
            absolute.insert(1, str(work / "robots.txt"))         # from another folder the file has to be named
        return _trace(other, absolute)
    if mode == "report-deleted":
        code, trace = _trace(work, argv)
        (work / "crawlpolicy-report.json").unlink()
        return code, trace
    if mode == "read-only-folder":
        work.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            return _trace(work, argv)
        finally:
            work.chmod(stat.S_IRWXU)
    return _trace(work, argv)


# The same tasks asked the way an agent may phrase the run: without --bots (the built-in table), or with full URLs.
VARIANTS = {name: [argv] for name, argv in TASK_RUNS.items()}
VARIANTS["task-longest-match"].append([a for a in TASK_RUNS["task-longest-match"] if a not in ("--bots", "GPTBot")])
VARIANTS["task-longest-match"].append([("https://shop.example" + a if a.startswith("/") else a)
                                       for a in TASK_RUNS["task-longest-match"]])
VARIANTS["task-token-exact"].append(["check", "--url", "/", "--url", "/private/press/kit.html"])


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", sorted(TASK_RUNS))
def test_trace_graders_survive_report_loss(tmp_path, name, mode):
    for i, argv in enumerate(VARIANTS[name]):
        code, trace = _replay(tmp_path / f"v{i}", name, mode, argv)
        assert code in (0, 1), (mode, argv, trace)                  # never refused: the verdict decides the code
        assert "Traceback" not in trace
        for g, fm in _graders(name):
            if fm.get("target") == "trace" or g.startswith("answer-pasted-"):
                assert re.search(fm["pattern"], trace, _flags(fm)), (name, mode, argv, g, trace)
