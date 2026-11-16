"""AC-7: bilingual CLI. --lang > CRAWLPOLICY_LANG > LC_ALL > LC_MESSAGES > LANG; non-ko means English."""
from __future__ import annotations

import re
import string

import pytest

import crawlpolicy as cp
from conftest import DEMO_INTENT


def test_lang_precedence():
    r = cp.resolve_lang
    assert r(None, {}) == "en"
    assert r(None, {"LANG": "ko_KR.UTF-8"}) == "ko"
    assert r(None, {"LANG": "de_DE.UTF-8"}) == "en"
    assert r(None, {"LANG": "ko_KR.UTF-8", "LC_MESSAGES": "en_US.UTF-8"}) == "en"       # LC_MESSAGES beats LANG
    assert r(None, {"LC_MESSAGES": "en_US.UTF-8", "LC_ALL": "ko_KR.UTF-8"}) == "ko"      # LC_ALL beats LC_MESSAGES
    assert r(None, {"LC_ALL": "en_US", "CRAWLPOLICY_LANG": "ko"}) == "ko"               # tool variable beats locale
    assert r(None, {"LC_ALL": "ko_KR", "CRAWLPOLICY_LANG": "en"}) == "en"
    assert r("ko", {"CRAWLPOLICY_LANG": "en", "LANG": "en_US"}) == "ko"                  # --lang beats everything
    assert r("en", {"CRAWLPOLICY_LANG": "ko", "LANG": "ko_KR"}) == "en"
    assert r(None, {"CRAWLPOLICY_LANG": "fr", "LANG": "ko_KR"}) == "ko"                  # unknown tool value is skipped


def test_message_keys_equal():
    assert set(cp.MESSAGES["en"]) == set(cp.MESSAGES["ko"])
    fields = string.Formatter()
    for key, en in cp.MESSAGES["en"].items():
        ko = cp.MESSAGES["ko"][key]
        names = lambda s: {f for _, f, _, _ in fields.parse(s) if f} - {"cell_word"}  # noqa: E731 (cell_word is an English-only noun)
        assert names(en) == names(ko), key
        assert ko.strip() and en.strip(), key


def test_korean_messages_are_really_korean():
    same = [k for k, v in cp.MESSAGES["ko"].items() if v == cp.MESSAGES["en"][k] and k not in ("diff_line", "err_usage") and re.search("[A-Za-z]{4,}", v)]
    assert same == []


def test_outputs_per_language(cli, work, report):
    en = cli("check", "robots.txt", "--intent", DEMO_INTENT, "--lang", "en")
    rep_en = report(work)
    ko = cli("check", "robots.txt", "--intent", DEMO_INTENT, "--lang", "ko")
    rep_ko = report(work)
    assert "Intent violations (17):" in en.out and "re-verified: 0 violations, 0 changed cells" in en.out
    assert "의도 위반 (17건):" in ko.out and "재검증: 위반 0, 바뀐 정상 칸 0" in ko.out
    assert "re-verified" not in ko.out and "Intent violations" not in ko.out and "재검증" not in en.out
    assert en.out.splitlines()[0].startswith("bot ") and ko.out.splitlines()[0].startswith("봇 ")
    assert rep_en == rep_ko                      # JSON keys and values are language neutral
    d_en = cli("diff", "robots.old.txt", "robots.txt", "--lang", "en")
    d_ko = cli("diff", "robots.old.txt", "robots.txt", "--lang", "ko")
    assert "7 of 85 cells changed" in d_en.out and "85칸 중 7칸이 바뀌었습니다" in d_ko.out


def test_env_variable_switches_language(cli, work, monkeypatch):
    monkeypatch.setenv("CRAWLPOLICY_LANG", "ko")
    assert "허용" in cli("check", "robots.txt").out
    assert "허용" not in cli("check", "robots.txt", "--lang", "en").out


def test_help_follows_the_language(cli):
    en = cli("--lang", "en", "check", "--help")
    ko = cli("--lang", "ko", "check", "--help")
    assert en.code == ko.code == 0
    assert "usage:" in en.out and "options:" in en.out and "show this help and exit" in en.out
    assert "사용법:" in ko.out and "옵션:" in ko.out and "이 도움말을 보여 주고 끝냅니다" in ko.out
    assert "usage:" not in ko.out and "show this help" not in ko.out
    top_ko = cli("--lang", "ko", "--help")
    assert "명령:" in top_ko.out and "종료 코드" in top_ko.out and "commands:" not in top_ko.out
    assert "output language" in cli("diff", "--help", "--lang", "en").out
    assert "출력 언어" in cli("diff", "--help", "--lang", "ko").out
