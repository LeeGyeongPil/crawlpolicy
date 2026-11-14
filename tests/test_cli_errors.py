"""AC-4 and AC-5: auto-detect outcomes (exit 3) and input errors (exit 2): one line, a fix hint, no traceback."""
from __future__ import annotations

import pytest

HTML = "<!DOCTYPE html>\n<html><head><title>404 Not Found</title></head><body>Not found</body></html>\n"


def test_autodetect_single(cli, tmp_path, monkeypatch, report):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "robots.txt").write_text("User-agent: *\nDisallow: /x\n", encoding="utf-8")
    res = cli("check", "--bots", "GPTBot", "--url", "/x")
    assert res.code == 0 and res.err == ""
    assert "GPTBot /x => deny @L2" in res.out
    assert report(tmp_path)["source"]["path"] == "public/robots.txt"


def test_autodetect_none_exit3(cli, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = cli("check")
    assert res.code == 3 and res.out == ""
    assert res.err == "error: no robots.txt found in ./, public/, static/, app/ — pass the path\n"


def test_autodetect_many_exit3(cli, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "public").mkdir()
    for p in ("robots.txt", "public/robots.txt"):
        (tmp_path / p).write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    res = cli("check")
    assert res.code == 3 and res.out == ""
    assert res.err == "error: found 2 robots.txt files: ./robots.txt, public/robots.txt — pass one path\n"
    assert "Traceback" not in res.err


def test_autodetect_none_exit3_korean(cli, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = cli("check", "--lang", "ko")
    assert res.code == 3
    assert res.err.startswith("오류: robots.txt 를 ./, public/, static/, app/ 에서 찾지 못했습니다")


CASES = {
    "missing-file": (["check", "nope/robots.txt"], "file not found: nope/robots.txt"),
    "html-page": (["check", "page.html.txt"], "not a robots.txt file: page.html.txt (looks like HTML)"),
    "binary": (["check", "blob.bin"], "not a robots.txt file: blob.bin (binary or not UTF-8)"),
    "bad-intent": (["check", "robots.txt", "--intent", "training deny"], "bad --intent clause 'training deny'"),
    "intent-bot-not-in-bots": (["check", "robots.txt", "--bots", "GPTBot", "--intent", "ClaudeBot=deny"],
                               "--intent names bot 'ClaudeBot' that is not in --bots"),
    "bad-url": (["check", "robots.txt", "--url", "example.com/x"], "bad URL 'example.com/x'"),
    "missing-urls-file": (["check", "robots.txt", "--urls", "nope-urls.txt"], "file not found: nope-urls.txt"),
}


@pytest.fixture
def broken(work):
    (work / "page.html.txt").write_text(HTML, encoding="utf-8")
    (work / "blob.bin").write_bytes(b"\x00\xff\xfe\x01binary")
    return work


@pytest.mark.parametrize("name", list(CASES))
def test_input_errors_exit2(cli, broken, name):
    argv, expect = CASES[name]
    res = cli(*argv)
    assert res.code == 2 and res.out == ""
    assert res.err.count("\n") == 1, res.err
    assert res.err.startswith("error: ") and expect in res.err
    assert "—" in res.err or "(" in res.err                      # every message says what to do or what was seen
    assert "Traceback" not in res.err


@pytest.mark.parametrize("name", list(CASES))
def test_input_errors_exit2_korean(cli, broken, name):
    res = cli(*CASES[name][0], "--lang", "ko")
    assert res.code == 2 and res.err.count("\n") == 1
    assert res.err.startswith("오류: ")


def test_other_input_errors(cli, work):
    (work / "bad.json").write_text("{not json", encoding="utf-8")
    for argv, fragment in (
        (["check", "robots.txt", "--bots-file", "bad.json"], "bad --bots-file bad.json"),
        (["check", "robots.txt", "--bots-file", "missing.json"], "file not found: missing.json"),
        (["check", "robots.txt", "--bots", "123"], "bad bot name '123'"),
        (["check", "robots.txt", "--out", "no/such/dir/report.json"], "cannot write no/such/dir/report.json"),
        (["check", "robots.txt", "--lang", "fr"], "bad --lang 'fr'"),
        (["check", "robots.txt", "--bogus"], "unrecognized arguments"),
        (["diff", "robots.txt"], "required"),
        (["check", "samples"], "file not found: samples"),
    ):
        res = cli(*argv)
        assert res.code == 2, argv
        assert res.err.startswith("error: ") and fragment in res.err and res.err.count("\n") == 1, (argv, res.err)


def test_fixed_out_never_overwrites_the_input(cli, work):
    res = cli("check", "robots.txt", "--intent", "training=deny", "--fixed-out", "robots.txt")
    assert res.code == 2 and "is the input file itself" in res.err
    assert "Disallow: /\n" in (work / "robots.txt").read_text(encoding="utf-8")


def test_empty_robots_txt_is_valid(cli, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "robots.txt").write_text("", encoding="utf-8")
    res = cli("check", "--bots", "GPTBot", "--url", "/")
    assert res.code == 0 and "GPTBot / => allow @L0" in res.out
