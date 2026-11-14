"""Shared helpers: run the CLI in-process, work in a private copy of samples/, keep the locale out of the tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import crawlpolicy as cp

REPO = Path(__file__).resolve().parent.parent
SAMPLES = REPO / "samples"
DEMO_INTENT = "search=allow; user=allow; training=deny; control=deny; all:/admin/=deny"


@pytest.fixture(autouse=True)
def _english_by_default(monkeypatch):
    """Tests must not depend on the developer's locale."""
    for name in ("CRAWLPOLICY_LANG", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "C")


class Result:
    def __init__(self, code: int, out: str, err: str):
        self.code, self.out, self.err = code, out, err


@pytest.fixture
def cli(capsys):
    """cli('check', 'robots.txt', ...) runs crawlpolicy.main and returns Result(code, out, err)."""

    def run(*argv: str) -> Result:
        code = cp.main(list(argv))
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return run


@pytest.fixture
def work(tmp_path, monkeypatch) -> Path:
    """A private working folder holding a copy of samples/; the CLI writes its report and fixed copy here."""
    dest = tmp_path / "work"
    shutil.copytree(SAMPLES, dest)
    monkeypatch.chdir(dest)
    return dest


@pytest.fixture
def report():
    def load(folder: Path) -> dict:
        return json.loads((folder / "crawlpolicy-report.json").read_text(encoding="utf-8"))

    return load
