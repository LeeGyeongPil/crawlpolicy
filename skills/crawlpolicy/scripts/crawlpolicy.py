#!/usr/bin/env python3
"""crawlpolicy - RFC 9309 robots.txt verdicts per crawler and URL, checked against a one-line intent.

One run of ``check`` builds the bot x URL verdict matrix (with the winning rule line of every cell), compares it with
the intent, proposes a minimal fix, re-verifies the fixed copy and writes crawlpolicy-report.json.
``diff OLD NEW`` lists only the cells whose verdict changed. Standard library only, no network access.

Exit codes: 0 ok / nothing changed, 1 intent violated / verdicts changed, 2 input error, 3 robots.txt auto-detect failed.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

VERSION = "0.1.3"
MAX_BYTES = 500 * 1024  # RFC 9309 section 2.5: a crawler parses at least the first 500 KiB
MAX_AUTO_URLS = 40
MAX_PATCH_LINES = 40  # longer patches are cut on screen; the whole fixed copy is in the fixed file
CANDIDATES = ("robots.txt", "public/robots.txt", "static/robots.txt", "app/robots.txt")
DEFAULT_REPORT = "crawlpolicy-report.json"
DEFAULT_FIXED = "robots.fixed.txt"
FIXED_REPORT = "crawlpolicy-report.fixed.json"  # a later run on the unchanged fixed copy goes here (see pick_report_path)
ROLES = ("training", "search", "user", "control", "ai", "all")
TABLE_ROLES = ("training", "search", "user", "control", "other")
UA_TOKEN = re.compile(r"[A-Za-z_-]+")

# ---------------------------------------------------------------------------------------------------------------
# Built-in crawler table. Tokens and roles come from each provider's own documentation (checked 2026-11-14).
# Bingbot is left out on purpose: its page could not be verified. Pass any token with --bots or replace the whole
# table with --bots-file.
# ---------------------------------------------------------------------------------------------------------------
BOTS_CHECKED = "2026-11-14"
SRC_OPENAI = "https://developers.openai.com/api/docs/bots"
SRC_ANTHROPIC = ("https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-"
                 "site-owners-block-the-crawler")
SRC_GOOGLE = "https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers"
SRC_APPLE = "https://support.apple.com/en-us/119829"
SRC_PERPLEXITY = "https://docs.perplexity.ai/guides/bots"
SRC_CCBOT = "https://commoncrawl.org/ccbot"

BOT_TABLE = [
    {"token": "GPTBot", "role": "training", "ai": True, "provider": "OpenAI", "note": None, "source": SRC_OPENAI},
    {"token": "OAI-SearchBot", "role": "search", "ai": True, "provider": "OpenAI", "note": None, "source": SRC_OPENAI},
    {"token": "ChatGPT-User", "role": "user", "ai": True, "provider": "OpenAI", "note": "user_requested",
     "source": SRC_OPENAI},
    {"token": "OAI-AdsBot", "role": "other", "ai": True, "provider": "OpenAI", "note": None, "source": SRC_OPENAI},
    {"token": "ClaudeBot", "role": "training", "ai": True, "provider": "Anthropic", "note": None,
     "source": SRC_ANTHROPIC},
    {"token": "Claude-SearchBot", "role": "search", "ai": True, "provider": "Anthropic", "note": None,
     "source": SRC_ANTHROPIC},
    {"token": "Claude-User", "role": "user", "ai": True, "provider": "Anthropic", "note": None, "source": SRC_ANTHROPIC},
    {"token": "Google-Extended", "role": "control", "ai": True, "provider": "Google", "note": "control_token",
     "source": SRC_GOOGLE},
    {"token": "Google-CloudVertexBot", "role": "other", "ai": True, "provider": "Google", "note": None,
     "source": SRC_GOOGLE},
    {"token": "Googlebot", "role": "search", "ai": False, "provider": "Google", "note": None, "source": SRC_GOOGLE},
    {"token": "Applebot", "role": "search", "ai": False, "provider": "Apple", "note": "apple_fallback",
     "source": SRC_APPLE},
    {"token": "Applebot-Extended", "role": "control", "ai": True, "provider": "Apple", "note": "control_token",
     "source": SRC_APPLE},
    {"token": "PerplexityBot", "role": "search", "ai": True, "provider": "Perplexity", "note": None,
     "source": SRC_PERPLEXITY},
    {"token": "Perplexity-User", "role": "user", "ai": True, "provider": "Perplexity", "note": "user_requested",
     "source": SRC_PERPLEXITY},
    {"token": "CCBot", "role": "training", "ai": True, "provider": "Common Crawl", "note": None, "source": SRC_CCBOT},
]

# ---------------------------------------------------------------------------------------------------------------
# Messages. Every user-visible sentence lives here, once per language. JSON keys are language neutral.
# ---------------------------------------------------------------------------------------------------------------
MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "usage_prefix": "usage: ",
        "title_positional": "arguments",
        "title_options": "options",
        "title_commands": "commands",
        "help_description": ("Per-crawler robots.txt verdicts by RFC 9309, compared with a one-line intent. "
                             "Exit codes: 0 ok, 1 intent violated / verdict changed, 2 input error, 3 no single "
                             "robots.txt found."),
        "help_help": "show this help and exit",
        "help_version": "show the version and exit",
        "help_check": ("verdict matrix + intent check + minimal fix + re-verification of the fixed copy, in one run"),
        "help_diff": "list only the cells whose verdict changed between two robots.txt files",
        "help_robots": ("saved robots.txt (default: the only one of ./robots.txt, public/, static/, app/)"),
        "help_old": "the older saved robots.txt",
        "help_new": "the newer saved robots.txt",
        "help_intent": ("intent: '<who>[:<path>]=allow|deny' joined by ';' (who = training|search|user|control|ai|all "
                        "or a bot token; the last matching clause wins), e.g. \"training=deny; search=allow; "
                        "all:/admin/=deny\""),
        "help_bots": ("comma list of bot tokens or roles (default: the built-in table), e.g. "
                      "\"ai,DocsHarvester/2.1\""),
        "help_bots_file": "JSON file that replaces the built-in crawler table",
        "help_url": "URL or /path to judge (repeatable); default: sample URLs taken from the rules",
        "help_urls": "text file with one URL or /path per line (# comments allowed)",
        "help_out": ("where to write the JSON report (default: crawlpolicy-report.json; a run on the unchanged fixed "
                     "copy it names goes to crawlpolicy-report.fixed.json)"),
        "help_fixed_out": "where to write the fixed copy when a fix is needed (default: robots.fixed.txt)",
        "help_lang": "output language en|ko (default: CRAWLPOLICY_LANG, then LC_ALL/LC_MESSAGES/LANG)",
        "col_bot": "bot",
        "legend": "A = allow, D = deny, @L<n> = deciding line (@L0 = no rule applies), ! = intent violated",
        "summary": "{cells} cells: {allowed} allow, {denied} deny, {violations} intent violations",
        "no_intent": "No --intent given: verdicts only, nothing was compared.",
        "ok_all": "Intent holds in every checked cell.",
        "viol_head": "Intent violations ({n}):",
        "viol_line": "  ! {verdict}  (wanted {want} by '{clause}')",
        "patch_head": "Proposed fix as a patch against {src}:",
        "reverified": "re-verified: {v} violations, {c} changed cells",
        "not_verified": "The fix could not be fully verified — review the patch by hand before using it.",
        "wrote_report": "report: {path}",
        "outputs_kept": ("files: no input file was changed; this run only added {files} as its record — these are "
                         "new output files, not edits: leave them in place"),
        "outputs_replaced": ("files: no input file was changed; this run wrote {files} as its record ({replaced} "
                             "already existed and was replaced) — these are output files, not edits: leave them in "
                             "place"),
        "wrote_fixed": "fixed copy: {path} (already re-checked by this run — answer from this output, no second run)",
        "kept_report": ("note: {path} is the fixed copy that {kept} already re-verified, so {kept} (the verdicts for "
                        "{src}) is kept and this run is written to {out}"),
        "notes_head": "Provider notes:",
        "note_user_requested": "requests made on a user's behalf; the provider says robots.txt may not apply to them",
        "note_control_token": "control token: does not crawl itself, it only controls how crawled content may be used",
        "note_apple_fallback": "without an Applebot group Apple falls back to the Googlebot rules (not applied here)",
        "ignored_note": "{n} lines are not used for verdicts (see ignored_lines in the report)",
        "warn_truncated": "warning: {path} is larger than 500 KiB — only the first 500 KiB were read",
        "diff_head": "diff {old} -> {new}",
        "diff_none": "No verdict changed ({cells} cells compared).",
        "diff_some": "{n} of {cells} cells changed:",
        "diff_line": "  {bot} {url}: {before} -> {after}  ({change})",
        "diff_gained": "gained access",
        "diff_lost": "lost access",
        "err_prefix": "error: ",
        "err_file_not_found": "file not found: {path} — pass the path to a saved robots.txt",
        "err_not_a_file": "not a file: {path} — pass the path to a saved robots.txt",
        "err_html": "not a robots.txt file: {path} (looks like HTML) — save the raw file, not the web page",
        "err_binary": "not a robots.txt file: {path} (binary or not UTF-8) — save the plain text file",
        "err_bad_clause": ("bad --intent clause '{clause}' — use <who>[:<path>]=allow|deny, e.g. training=deny or "
                           "all:/admin/=deny"),
        "err_intent_bot": "--intent names bot '{token}' that is not in --bots — add it to --bots or fix the name",
        "err_bad_url": "bad URL '{url}' — use https://host/path or /path",
        "err_urls_not_found": "file not found: {path} — pass an existing --urls file with one URL per line",
        "err_bad_bot": "bad bot name '{name}' — use a product token such as GPTBot or a name like DocsHarvester/2.1",
        "err_no_bots": "no bots selected — pass --bots with at least one token or role",
        "err_bots_file_missing": "file not found: {path} — pass an existing --bots-file JSON file",
        "err_bots_file_bad": ("bad --bots-file {path}: {why} — expected a JSON list of {{\"token\": ..., \"role\": "
                              "training|search|user|control|other}} objects"),
        "err_none_found": "no robots.txt found in ./, public/, static/, app/ — pass the path",
        "err_many_found": "found {n} robots.txt files: {files} — pass one path, e.g. {first}",
        "err_write": "cannot write {path}: {why} — choose a writable path",
        "err_out_clash": "{opt} {path} would overwrite {what} — choose another path",
        "err_out_not_report": "--out {path} already exists and is not a crawlpolicy report — choose another path",
        "what_input": "the input file",
        "what_urls": "the --urls file",
        "what_bots_file": "the --bots-file file",
        "what_report": "the report (--out)",
        "what_fixed": "the fixed copy (--fixed-out)",
        "what_old_report": "an earlier crawlpolicy report",
        "err_usage": "{detail} — run with --help to see the options",
        "err_unrecognized": "unrecognized arguments: {args} — run with --help to see the options",
        "err_invalid_choice": "'{value}' is not valid here — choose from: {choices}",
        "err_expected_arg": "option {opt} needs a value — see --help for the expected value",
        "err_required": "missing required argument(s): {args} — see --help for the expected arguments",
        "warn_fix_large": ("warning: the fixed copy {path} is larger than 500 KiB, so crawlers may not read the lines "
                           "added at its end — shorten the file by hand"),
        "patch_more": "... {n} more patch lines — see {path} for the whole fixed copy",
        "err_bad_lang": "bad --lang '{value}' — use en or ko",
        "err_no_urls": "no URLs to judge — pass --url or --urls",
    },
    "ko": {
        "usage_prefix": "사용법: ",
        "title_positional": "인자",
        "title_options": "옵션",
        "title_commands": "명령",
        "help_description": ("RFC 9309 기준으로 크롤러별 robots.txt 판정을 내고 한 줄 의도와 대조합니다. "
                             "종료 코드: 0 정상, 1 의도 위반·판정 변경, 2 입력 오류, 3 robots.txt 를 하나로 못 정함."),
        "help_help": "이 도움말을 보여 주고 끝냅니다",
        "help_version": "버전을 보여 주고 끝냅니다",
        "help_check": "판정 행렬 + 의도 대조 + 최소 수정안 + 수정본 재검증을 한 번에 합니다",
        "help_diff": "두 robots.txt 사이에서 판정이 바뀐 칸만 보여 줍니다",
        "help_robots": "저장한 robots.txt (생략하면 ./robots.txt, public/, static/, app/ 중 하나뿐일 때 그 파일)",
        "help_old": "이전 robots.txt",
        "help_new": "새 robots.txt",
        "help_intent": ("의도: '<누구>[:<경로>]=allow|deny' 를 ';' 로 잇기 (누구 = training|search|user|control|ai|all "
                        "또는 봇 토큰, 뒤 절이 앞 절을 덮음). 예: \"training=deny; search=allow; all:/admin/=deny\""),
        "help_bots": "봇 토큰·역할 쉼표 목록 (기본: 내장 표). 예: \"ai,DocsHarvester/2.1\"",
        "help_bots_file": "내장 크롤러 표를 통째로 바꾸는 JSON 파일",
        "help_url": "판정할 URL 또는 /경로 (여러 번 가능). 기본: 규칙에서 뽑은 표본 URL",
        "help_urls": "한 줄에 URL 또는 /경로 하나인 텍스트 파일 (# 주석 가능)",
        "help_out": ("JSON 보고서 경로 (기본: crawlpolicy-report.json, 그 보고서의 수정본을 그대로 다시 검사하면 "
                     "crawlpolicy-report.fixed.json)"),
        "help_fixed_out": "수정이 필요할 때 수정본을 쓸 경로 (기본: robots.fixed.txt)",
        "help_lang": "출력 언어 en|ko (기본: CRAWLPOLICY_LANG, 그다음 LC_ALL/LC_MESSAGES/LANG)",
        "col_bot": "봇",
        "legend": "A = 허용, D = 차단, @L<n> = 결정한 줄 번호 (@L0 = 적용되는 규칙 없음), ! = 의도 위반",
        "summary": "{cells}칸: 허용 {allowed}, 차단 {denied}, 의도 위반 {violations}",
        "no_intent": "--intent 가 없어 판정만 냈고 대조는 하지 않았습니다.",
        "ok_all": "검사한 모든 칸에서 의도가 지켜집니다.",
        "viol_head": "의도 위반 ({n}건):",
        "viol_line": "  ! {verdict}  ('{clause}' 에 따라 {want} 여야 함)",
        "patch_head": "{src} 에 대한 수정 패치 제안:",
        "reverified": "재검증: 위반 {v}, 바뀐 정상 칸 {c}",
        "not_verified": "수정안을 끝까지 검증하지 못했습니다 — 쓰기 전에 패치를 직접 확인하세요.",
        "wrote_report": "보고서: {path}",
        "outputs_kept": ("파일: 입력 파일은 하나도 바꾸지 않았고, 이번 실행의 기록으로 {files} 만 새로 썼습니다 — 편집이 아니라 "
                         "새 출력 파일이니 그대로 두세요"),
        "outputs_replaced": ("파일: 입력 파일은 하나도 바꾸지 않았고, 이번 실행의 기록으로 {files} 를 썼습니다 (이미 있던 "
                             "{replaced} 는 새 내용으로 바꿈) — 편집이 아니라 출력 파일이니 그대로 두세요"),
        "wrote_fixed": "수정본: {path} (이번 실행에서 이미 재검증함 — 다시 돌리지 말고 이 출력으로 답하세요)",
        "kept_report": ("참고: {path} 는 이미 {kept} 에서 재검증한 수정본입니다. {src} 의 판정이 담긴 {kept} 는 "
                        "그대로 두고 이번 결과는 {out} 에 씁니다"),
        "notes_head": "제공자 메모:",
        "note_user_requested": "사용자 요청으로 가져오는 봇이며, 제공자가 robots.txt 가 적용되지 않을 수 있다고 밝힘",
        "note_control_token": "제어 토큰: 직접 크롤링하지 않고 수집된 내용의 사용 방식만 정함",
        "note_apple_fallback": "Applebot 그룹이 없으면 Apple 은 Googlebot 규칙을 따름 (여기서는 적용하지 않음)",
        "ignored_note": "{n}개 줄은 판정에 쓰지 않았습니다 (보고서의 ignored_lines 참고)",
        "warn_truncated": "경고: {path} 가 500 KiB 보다 커서 앞 500 KiB 만 읽었습니다",
        "diff_head": "비교 {old} -> {new}",
        "diff_none": "바뀐 판정이 없습니다 ({cells}칸 비교).",
        "diff_some": "{cells}칸 중 {n}칸이 바뀌었습니다:",
        "diff_line": "  {bot} {url}: {before} -> {after}  ({change})",
        "diff_gained": "접근 허용으로 바뀜",
        "diff_lost": "접근 차단으로 바뀜",
        "err_prefix": "오류: ",
        "err_file_not_found": "파일이 없습니다: {path} — 저장한 robots.txt 경로를 넘기세요",
        "err_not_a_file": "파일이 아닙니다: {path} — 저장한 robots.txt 경로를 넘기세요",
        "err_html": "robots.txt 파일이 아닙니다: {path} (HTML 로 보임) — 웹 페이지가 아니라 원본 파일을 저장하세요",
        "err_binary": "robots.txt 파일이 아닙니다: {path} (바이너리이거나 UTF-8 이 아님) — 텍스트 파일로 저장하세요",
        "err_bad_clause": ("--intent 절 '{clause}' 가 잘못됐습니다 — <누구>[:<경로>]=allow|deny 형식으로 쓰세요. "
                           "예: training=deny, all:/admin/=deny"),
        "err_intent_bot": "--intent 가 --bots 에 없는 봇 '{token}' 을(를) 가리킵니다 — --bots 에 넣거나 이름을 고치세요",
        "err_bad_url": "잘못된 URL '{url}' — https://host/path 또는 /path 형식으로 쓰세요",
        "err_urls_not_found": "파일이 없습니다: {path} — 한 줄에 URL 하나인 --urls 파일 경로를 넘기세요",
        "err_bad_bot": "잘못된 봇 이름 '{name}' — GPTBot 같은 제품 토큰이나 DocsHarvester/2.1 형식으로 쓰세요",
        "err_no_bots": "선택된 봇이 없습니다 — --bots 에 토큰이나 역할을 하나 이상 넘기세요",
        "err_bots_file_missing": "파일이 없습니다: {path} — 존재하는 --bots-file JSON 경로를 넘기세요",
        "err_bots_file_bad": ("--bots-file {path} 가 잘못됐습니다: {why} — {{\"token\": ..., \"role\": "
                              "training|search|user|control|other}} 객체의 JSON 목록이어야 합니다"),
        "err_none_found": "robots.txt 를 ./, public/, static/, app/ 에서 찾지 못했습니다 — 경로를 넘기세요",
        "err_many_found": "robots.txt 가 {n}개 있습니다: {files} — 경로를 하나 넘기세요 (예: {first})",
        "err_write": "{path} 에 쓸 수 없습니다: {why} — 쓸 수 있는 경로를 고르세요",
        "err_out_clash": "{opt} {path} 는 {what} 을(를) 덮어씁니다 — 다른 경로를 고르세요",
        "err_out_not_report": "--out {path} 는 이미 있는 파일이고 crawlpolicy 보고서가 아닙니다 — 다른 경로를 고르세요",
        "what_input": "입력 파일",
        "what_urls": "--urls 파일",
        "what_bots_file": "--bots-file 파일",
        "what_report": "보고서(--out)",
        "what_fixed": "수정본(--fixed-out)",
        "what_old_report": "이전 crawlpolicy 보고서",
        "err_usage": "인자가 잘못됐습니다 ({detail}) — --help 로 옵션을 확인하세요",
        "err_unrecognized": "알 수 없는 인자: {args} — --help 로 옵션을 확인하세요",
        "err_invalid_choice": "'{value}' 는 여기서 쓸 수 없는 값입니다 — 다음 중 고르세요: {choices}",
        "err_expected_arg": "옵션 {opt} 에 값이 필요합니다 — --help 로 기대하는 값을 확인하세요",
        "err_required": "필수 인자가 없습니다: {args} — --help 로 필요한 인자를 확인하세요",
        "warn_fix_large": ("경고: 수정본 {path} 가 500 KiB 보다 커서 끝에 추가한 줄을 크롤러가 읽지 못할 수 있습니다 — "
                           "파일을 직접 줄이세요"),
        "patch_more": "... 패치 {n}줄 더 있음 — 전체 수정본은 {path} 참고",
        "err_bad_lang": "--lang '{value}' 가 잘못됐습니다 — en 또는 ko 를 쓰세요",
        "err_no_urls": "판정할 URL 이 없습니다 — --url 또는 --urls 를 넘기세요",
    },
}


class CliError(Exception):
    """An input problem that ends the run with a one-line message and a non-zero exit code."""

    def __init__(self, code: int, key: str, **kw: object) -> None:
        super().__init__(key)
        self.code, self.key, self.kw = code, key, kw


def make_t(lang: str):
    table = MESSAGES[lang]

    def t(key: str, **kw: object) -> str:
        return table[key].format(**kw) if kw else table[key]

    return t


def resolve_lang(cli_value: str | None = None, environ: dict | None = None) -> str:
    """--lang > CRAWLPOLICY_LANG > LC_ALL > LC_MESSAGES > LANG; ko* means Korean, everything else English."""
    env = os.environ if environ is None else environ
    if cli_value:
        return cli_value
    own = (env.get("CRAWLPOLICY_LANG") or "").strip().lower()
    if own in MESSAGES:
        return own
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = (env.get(name) or "").strip()
        if val:
            return "ko" if val.lower().startswith("ko") else "en"
    return "en"


# ---------------------------------------------------------------------------------------------------------------
# RFC 9309 parser and evaluator
# ---------------------------------------------------------------------------------------------------------------
_UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_KEEP = set(":/?#[]@!$&'()*+,;=")


def normalize_path(s: str) -> str:
    """Percent-normalize like RFC 9309 2.2.2: encode non-ASCII octets, upper-case escapes, decode unreserved ones."""
    data = s.encode("utf-8", "surrogateescape")
    out: list[str] = []
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x25 and i + 2 < len(data) and re.fullmatch(rb"[0-9A-Fa-f]{2}", data[i + 1:i + 3] or b""):
            v = int(data[i + 1:i + 3], 16)
            out.append(chr(v) if chr(v) in _UNRESERVED else "%%%02X" % v)
            i += 3
            continue
        ch = chr(b)
        if b < 0x80 and (ch in _UNRESERVED or ch in _KEEP):
            out.append(ch)
        else:
            out.append("%%%02X" % b)
        i += 1
    return "".join(out)


def make_matcher(pattern: str):
    """Rule pattern -> predicate on a normalized path, built once per rule (no shared cache).
    '*' matches any run of characters, a trailing '$' anchors the end; plain prefixes skip the regex engine."""
    anchored = pattern.endswith("$")
    core = pattern[:-1] if anchored else pattern
    if "*" not in core:
        literal = normalize_path(core)
        if anchored:
            return lambda path: path == literal
        return lambda path: path.startswith(literal)
    body = ".*".join(re.escape(normalize_path(part)) for part in core.split("*"))
    rx = re.compile(body + ("$" if anchored else ""), re.DOTALL)
    return lambda path: rx.match(path) is not None


def url_to_path(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return path


def parse_robots(text: str) -> tuple[list[dict], list[dict]]:
    """Return (groups, ignored_lines). A group is {agents, lines, rules}; blank, unknown and Sitemap lines keep it open."""
    groups: list[dict] = []
    ignored: list[dict] = []
    cur: dict | None = None
    open_ua = False  # True right after a User-agent line: the next User-agent joins the same group
    for no, raw in enumerate(re.split(r"\r\n|\r|\n", text), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, sep, val = line.partition(":")
        if not sep:
            ignored.append({"line": no, "text": raw.strip()[:200], "reason": "unparsable"})
            continue
        key, val = key.strip().lower(), val.strip()
        if key in ("user-agent", "useragent", "user agent"):
            if cur is None or not open_ua:
                cur = {"agents": [], "lines": [], "rules": []}
                groups.append(cur)
            open_ua = True
            m = UA_TOKEN.match(val)
            cur["agents"].append("*" if val == "*" else (m.group(0).lower() if m else ""))
            cur["lines"].append(no)
        elif key in ("allow", "disallow"):
            open_ua = False
            if cur is None:
                ignored.append({"line": no, "text": raw.strip()[:200], "reason": "rule-before-user-agent"})
            elif val:  # an empty Allow/Disallow value is no rule at all
                cur["rules"].append({"line": no, "type": key, "path": val, "_m": make_matcher(val)})
        elif key != "sitemap":
            ignored.append({"line": no, "text": raw.strip()[:200], "reason": "nonstandard"})
    return groups, ignored


def select_groups(groups: list[dict], token: str) -> tuple[list[dict], str]:
    """Product-token match is exact and case-insensitive (not a substring); '*' only when nothing matched."""
    low = token.lower()
    own = [g for g in groups if low in g["agents"]]
    if own:
        return own, "own"
    star = [g for g in groups if "*" in g["agents"]]
    return (star, "star") if star else ([], "none")


def decide(groups: list[dict], token: str, url: str, memo: dict | None = None) -> dict:
    """Verdict for one bot and URL: allowed, winning rule line (None when no rule applies), rule text, group kind.
    ``memo`` (one per matrix build) lets bots that share the same groups share the rule scan."""
    chosen, kind = select_groups(groups, token)
    path = normalize_path(url_to_path(url))
    if path == "/robots.txt":
        return {"allowed": True, "rule_line": None, "rule": None, "group": kind}
    key = (tuple(id(g) for g in chosen), path)
    best = memo.get(key, False) if memo is not None else False
    if best is False:
        best = None
        for g in chosen:
            for r in g["rules"]:
                if not r["_m"](path):
                    continue
                allow = r["type"] == "allow"
                length = len(r["path"])  # most octets wins; on a tie Allow wins
                if best is None or length > best[0] or (length == best[0] and allow and not best[1]):
                    best = (length, allow, r)
        if memo is not None:
            memo[key] = best
    if best is None:
        return {"allowed": True, "rule_line": None, "rule": None, "group": kind}
    r = best[2]
    return {"allowed": best[1], "rule_line": r["line"], "rule": f"{r['type'].capitalize()}: {r['path']}", "group": kind}


def normalize_bot(name: str) -> str | None:
    """'ClaudeBot/1.0' -> 'ClaudeBot'; 'Mozilla/5.0 (compatible; X/2)' -> 'X'."""
    name = name.strip()
    if name.lower().startswith("mozilla/"):
        m = re.search(r"compatible;\s*([A-Za-z_-]+)", name)
        return m.group(1) if m else None
    m = UA_TOKEN.match(name)
    return m.group(0) if m else None


def verdict_text(bot: str, url: str, allowed: bool, rule_line: int | None) -> str:
    return f"{bot} {url} => {'allow' if allowed else 'deny'} @L{rule_line or 0}"


# ---------------------------------------------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------------------------------------------
def _visible_head(data: bytes) -> bytes:
    """First 2 KiB without '#' comments, lower-cased: a comment that mentions '<html' is not an HTML page."""
    lines = data[:2048].lstrip(b"\xef\xbb\xbf").split(b"\n")
    return b"\n".join(ln.split(b"#", 1)[0] for ln in lines).lstrip(b" \t\r\n").lower()


def read_robots(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise CliError(2, "err_file_not_found", path=path)
    if not p.is_file():
        raise CliError(2, "err_not_a_file", path=path)
    try:
        data = p.read_bytes()
    except OSError as e:
        raise CliError(2, "err_file_not_found", path=f"{path} ({e.strerror})") from e
    head = _visible_head(data)
    warnings: list[str] = []
    cut = data
    if len(data) > MAX_BYTES:
        cut = data[:MAX_BYTES]
        cut = cut[: cut.rfind(b"\n") + 1] or cut
        warnings.append("truncated")
    if b"\x00" in cut[:4096]:
        raise CliError(2, "err_binary", path=path)
    try:
        text = cut.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CliError(2, "err_binary", path=path) from e
    if head.startswith((b"<!doctype html", b"<html", b"<head", b"<body")) or b"<html" in head[:1024]:
        raise CliError(2, "err_html", path=path)
    lines = len(re.split(r"\r\n|\r|\n", text)) - (1 if text.endswith(("\n", "\r")) else 0)
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "lines": lines,
            "text": text, "warnings": warnings}


def autodetect_robots() -> str:
    found = [c for c in CANDIDATES if Path(c).is_file()]
    if not found:
        raise CliError(3, "err_none_found")
    if len(found) > 1:
        shown = [("./" + f) if "/" not in f else f for f in found]
        raise CliError(3, "err_many_found", n=len(found), files=", ".join(shown), first=shown[0])
    return found[0]


def pick_robots(arg: str | None) -> str:
    """The file check reads: the given path, or auto-detect when it was left out.

    The bare default name `robots.txt` is what people and agents type when they mean "the robots.txt" without
    choosing one. When public/, static/ or app/ also holds a robots.txt, silently judging ./robots.txt could answer
    for the wrong file, so that case stops like a left-out path (exit 3). `./robots.txt` or any other path is used
    as given.
    """
    if not arg:
        return autodetect_robots()
    if arg == "robots.txt" and sum(1 for c in CANDIDATES if Path(c).is_file()) > 1:
        return autodetect_robots()  # 후보가 여럿이면 종료 3 — 어느 파일인지 사용자에게 물어야 한다
    return arg


def check_url(url: str) -> str:
    u = url.strip()
    if re.search(r"\s", u) or not (u.startswith("/") and not u.startswith("//") or re.match(r"https?://[^/\s]+", u)):
        raise CliError(2, "err_bad_url", url=url)
    try:
        urllib.parse.urlsplit(u)  # 'http://[bad' 같은 깨진 IPv6 호스트는 여기서 막는다 — 나중에 traceback·종료 1 이 되지 않게
    except ValueError as e:
        raise CliError(2, "err_bad_url", url=url) from e
    return u


def collect_urls(url_args: list[str] | None, urls_file: str | None) -> list[str]:
    urls: list[str] = []
    for u in url_args or []:
        urls.append(check_url(u))
    if urls_file:
        p = Path(urls_file)
        if not p.is_file():
            raise CliError(2, "err_urls_not_found", path=urls_file)
        try:
            raw = p.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as e:
            raise CliError(2, "err_binary", path=urls_file) from e
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(check_url(line))
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


def sample_urls(groups: list[dict], clauses: list[dict]) -> list[str]:
    """Auto URLs: '/' plus every rule and intent path ('$' dropped, '*' -> 'x')."""
    out = ["/"]
    paths = [r["path"] for g in groups for r in g["rules"]] + [c["path"] for c in clauses]
    for p in paths:
        if p.endswith("$"):
            p = p[:-1]
        p = p.replace("*", "x")
        if p.startswith("/") and not re.search(r"\s", p) and p not in out and len(out) < MAX_AUTO_URLS:
            out.append(p)
    return out


def load_bot_table(bots_file: str | None) -> list[dict]:
    if not bots_file:
        return [dict(b) for b in BOT_TABLE]
    p = Path(bots_file)
    if not p.is_file():
        raise CliError(2, "err_bots_file_missing", path=bots_file)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CliError(2, "err_bots_file_bad", path=bots_file, why=str(e)[:80]) from e
    if not isinstance(data, list) or not data:
        raise CliError(2, "err_bots_file_bad", path=bots_file, why="not a non-empty list")
    table = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or not isinstance(item.get("token"), str) or not UA_TOKEN.fullmatch(item["token"]):
            raise CliError(2, "err_bots_file_bad", path=bots_file, why=f"item {i} has no valid token")
        role = item.get("role", "other")
        if role not in TABLE_ROLES:
            raise CliError(2, "err_bots_file_bad", path=bots_file, why=f"item {i} has role {role!r}")
        table.append({"token": item["token"], "role": role, "ai": bool(item.get("ai", role != "other")),
                      "provider": item.get("provider"), "note": None, "source": item.get("source")})
    return table


def bot_matches(who: str, bot: dict) -> bool:
    if who == "all":
        return True
    if who == "ai":
        return bool(bot["ai"])
    if who in ROLES:
        return bot["role"] == who
    return bot["token"].lower() == who


def resolve_bots(spec: str | None, table: list[dict]) -> list[dict]:
    if spec is None:
        return [dict(b) for b in table]
    out: list[dict] = []
    seen: set[str] = set()
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if item.lower() in ROLES:
            members = [dict(b) for b in table if bot_matches(item.lower(), b)]
        else:
            token = normalize_bot(item)
            if token is None:
                raise CliError(2, "err_bad_bot", name=item)
            known = next((b for b in table if b["token"].lower() == token.lower()), None)
            members = [dict(known)] if known else [{"token": token, "role": "other", "ai": False, "provider": None,
                                                    "note": None, "source": None}]
        for b in members:
            if b["token"].lower() not in seen:
                seen.add(b["token"].lower())
                out.append(b)
    if not out:
        raise CliError(2, "err_no_bots")
    return out


def parse_intent(text: str | None, bots: list[dict]) -> list[dict]:
    """'<who>[:<path>]=allow|deny' clauses joined by ';'. Later clauses override earlier ones."""
    if not text or not text.strip():
        return []
    tokens = {b["token"].lower() for b in bots}
    clauses = []
    for raw in text.split(";"):
        raw = raw.strip()
        if not raw:
            continue
        left, eq, want = raw.rpartition("=")
        want = want.strip().lower()
        if not eq or want not in ("allow", "deny"):
            raise CliError(2, "err_bad_clause", clause=raw)
        who, colon, path = left.strip().partition(":")
        who, path = who.strip(), path.strip()
        if not who or re.search(r"\s", who) or (colon and not (path.startswith("/") or path.startswith("*"))):
            raise CliError(2, "err_bad_clause", clause=raw)
        if not colon:
            path = "/"
        if who.lower() not in ROLES:
            token = normalize_bot(who)
            if token is None:
                raise CliError(2, "err_bad_clause", clause=raw)
            who = token.lower()
            if who not in tokens:
                raise CliError(2, "err_intent_bot", token=token)
        else:
            who = who.lower()
        clauses.append({"who": who, "path": path, "want": want, "text": raw, "_m": make_matcher(path)})
    return clauses


def want_for(bot: dict, url: str, clauses: list[dict]) -> int | None:
    """Index of the last clause that covers this bot and URL, or None when the cell has no intent."""
    path = normalize_path(url_to_path(url))
    hit = None
    for i, c in enumerate(clauses):
        if bot_matches(c["who"], bot) and c["_m"](path):
            hit = i
    return hit


def build_cells(groups: list[dict], bots: list[dict], urls: list[str], clauses: list[dict]) -> list[dict]:
    cells = []
    memo: dict = {}
    for bot in bots:
        for url in urls:
            d = decide(groups, bot["token"], url, memo)
            ci = want_for(bot, url, clauses)
            want = clauses[ci]["want"] if ci is not None else None
            ok = want is None or (want == "allow") == d["allowed"]
            cells.append({"bot": bot["token"], "url": url, "allowed": d["allowed"], "rule_line": d["rule_line"],
                          "rule": d["rule"], "group": d["group"], "intent": want, "ok": ok,
                          "verdict": verdict_text(bot["token"], url, d["allowed"], d["rule_line"]), "_clause": ci})
    return cells


# ---------------------------------------------------------------------------------------------------------------
# Fix proposal: add the fewest lines that make the intent hold, then re-judge the whole matrix
# ---------------------------------------------------------------------------------------------------------------
def split_lines(text: str) -> list[str]:
    lines = re.split(r"\r\n|\r|\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _rule_line(r: dict) -> str:
    return f"{r['type'].capitalize()}: {r['path']}"


def apply_fix(text: str, groups: list[dict], bots: list[dict], adds: dict[str, list[str]]) -> tuple[str, list[dict]]:
    """Return (fixed text, added blocks). A bot with its own single-bot group gets lines at the end of that group;
    every other bot gets a new group at the end of the file (it carries the '*' rules when it used to fall back to them)."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = split_lines(text)
    inserts: dict[int, list[str]] = {}
    blocks: list[dict] = []
    appended: dict[tuple, list[str]] = {}
    star_rules = [_rule_line(r) for g in groups if "*" in g["agents"] for r in g["rules"]]
    for bot in bots:
        add = adds.get(bot["token"])
        if not add:
            continue
        low = bot["token"].lower()
        own = next((g for g in groups if g["agents"] == [low]), None)
        if own is not None:
            end = max(own["lines"] + [r["line"] for r in own["rules"]])
            inserts.setdefault(end, []).extend(add)
            blocks.append({"bots": [bot["token"]], "group_line": end, "lines": list(add)})
            continue
        _, kind = select_groups(groups, bot["token"])
        base = star_rules if kind == "star" else []
        appended.setdefault((tuple(base), tuple(add)), []).append(bot["token"])
    out: list[str] = []
    for no, raw in enumerate(lines, 1):
        out.append(raw)
        out.extend(inserts.get(no, []))
    for (base, add), tokens in appended.items():
        if out and out[-1].strip():
            out.append("")
        out.extend(f"User-agent: {tok}" for tok in tokens)
        out.extend(base)
        out.extend(add)
        blocks.append({"bots": tokens, "group_line": len(lines), "lines": list(add)})
    return nl.join(out) + nl, blocks


def probe_urls(groups: list[dict], clauses: list[dict], urls: list[str]) -> list[str]:
    """One child path per rule or intent path ('/blog/public/' -> '/blog/public/x'), to see whole prefixes."""
    out: list[str] = []
    for p in sample_urls(groups, clauses)[1:]:
        child = p + "x" if p.endswith("/") else p + "/x"
        if child not in urls and child not in out:
            out.append(child)
    return out[:MAX_AUTO_URLS]


def propose_fix(text: str, groups: list[dict], bots: list[dict], urls: list[str], clauses: list[dict],
                before: list[dict]) -> dict:
    """Iterate: add the broadest missing clause line per bot, re-judge, and beat a longer opposing rule as a whole.

    Re-judging covers the matrix URLs plus one child probe per rule and intent path, so a fix that only holds for the
    sampled URL but leaves the rest of a path prefix open is caught."""
    adds: dict[str, list[str]] = {}
    probes = probe_urls(groups, clauses, urls)
    urls = urls + probes
    before = before + build_cells(groups, bots, probes, clauses)
    cur_groups, cur = groups, before
    fixed_text = text
    blocks: list[dict] = []
    for _ in range(6):
        bad = [c for c in cur if not c["ok"]]
        if not bad:
            break
        progressed = False
        for bot in bots:
            mine = [c for c in bad if c["bot"] == bot["token"]]
            if not mine:
                continue
            have = adds.setdefault(bot["token"], [])
            cands = []
            for c in sorted(mine, key=lambda c: len(clauses[c["_clause"]]["path"])):
                cl = clauses[c["_clause"]]
                cands.append(("Allow" if cl["want"] == "allow" else "Disallow") + ": " + cl["path"])
            new = next((x for x in cands if x not in have), None)
            if new:
                have.append(new)
                progressed = True
                continue
            for c in mine:  # the broad line is already there but a longer rule still wins
                kind = "Allow" if c["intent"] == "allow" else "Disallow"
                opposing = c["rule"].split(": ", 1)[1] if c["rule"] else ""
                if opposing and not opposing.endswith("$"):
                    line = f"{kind}: {opposing}*"  # one octet longer than the opposing rule, same prefix: beats all of it
                else:  # only an end-anchored rule (or nothing) is in the way: pin the exact path
                    line = f"{kind}: {url_to_path(c['url'])}$"
                if line not in have:
                    have.append(line)
                    progressed = True
        if not progressed:
            break
        fixed_text, blocks = apply_fix(text, groups, bots, adds)
        cur_groups, _ = parse_robots(fixed_text)
        cur = build_cells(cur_groups, bots, urls, clauses)
    adds = {k: v for k, v in adds.items() if v}
    fixed_text, blocks = apply_fix(text, groups, bots, adds) if adds else (text, [])
    after = build_cells(parse_robots(fixed_text)[0], bots, urls, clauses)
    was = {(c["bot"], c["url"]): c for c in before}
    regressions = sum(1 for c in after if was[(c["bot"], c["url"])]["ok"] and was[(c["bot"], c["url"])]["allowed"] != c["allowed"])
    violations_after = sum(1 for c in after if not c["ok"])
    too_large = len(fixed_text.encode("utf-8")) > MAX_BYTES  # a crawler may stop reading after 500 KiB
    return {"text": fixed_text, "blocks": blocks, "violations_after": violations_after, "regressions": regressions,
            "verified": violations_after == 0 and regressions == 0 and not too_large, "too_large": too_large,
            "probe_urls": probes}


# ---------------------------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------------------------
def render_matrix(bots: list[dict], urls: list[str], cells: list[dict], t) -> list[str]:
    by = {(c["bot"], c["url"]): c for c in cells}
    if len(bots) == 1:  # one bot (a collector check): one line per URL reads better than one wide row
        return ["  " + c["verdict"] + (" !" if not c["ok"] else "") for c in cells]
    head = [t("col_bot")] + urls
    rows = [head]
    for b in bots:
        row = [b["token"]]
        for u in urls:
            c = by[(b["token"], u)]
            row.append(("A" if c["allowed"] else "D") + "@L" + str(c["rule_line"] or 0) + ("" if c["ok"] else "!"))
        rows.append(row)
    widths = [max(len(r[i]) for r in rows) for i in range(len(head))]
    return ["  ".join(v.ljust(widths[i]) for i, v in enumerate(r)).rstrip() for r in rows]


def public_cell(c: dict) -> dict:
    return {k: v for k, v in c.items() if not k.startswith("_")}


def file_info(src: dict) -> dict:
    return {"path": src["path"], "sha256": src["sha256"], "bytes": src["bytes"], "lines": src["lines"]}


def write_text(path: str, content: str) -> None:
    try:
        # newline="" 로 바이트를 그대로 쓴다 — 보고서에 적은 수정본 sha256 이 어느 OS 에서도 파일과 같아야 한다
        Path(path).write_text(content, encoding="utf-8", newline="")
    except OSError as e:
        raise CliError(2, "err_write", path=path, why=e.strerror or str(e)) from e


def pick_report_path(out: str | None, inputs: list[dict]) -> tuple[str, dict | None]:
    """Return (report path to write, None or {"path": fixed copy, "kept": kept report, "src": its source file}).

    A check that proposed a fix leaves the evidence for the user's file in the default report. Running check or diff
    on that unchanged fixed copy afterwards (a natural "double check") must not replace it, so that run goes to a
    side file instead. An explicit --out is always honoured.
    """
    if out is not None:
        return out, None
    prev_path = Path(DEFAULT_REPORT)
    try:
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_REPORT, None  # 이전 보고서가 없거나 읽을 수 없으면 평소처럼 기본 경로에 쓴다
    fix = prev.get("fix") if isinstance(prev, dict) and prev.get("mode") == "check" else None
    if not (isinstance(fix, dict) and fix.get("needed") and isinstance(fix.get("path"), str)
            and isinstance(fix.get("sha256"), str)):
        return DEFAULT_REPORT, None
    fixed_file = (prev_path.resolve().parent / fix["path"]).resolve()  # 보고서 폴더 기준 상대 경로
    for src in inputs:
        # 같은 파일이고 내용도 그대로일 때만 "수정본 재확인"으로 본다 — 사용자가 고쳤다면 새 판정이 맞다
        if Path(src["path"]).resolve() == fixed_file and src["sha256"] == fix["sha256"]:
            source = prev.get("source") if isinstance(prev.get("source"), dict) else {}
            return FIXED_REPORT, {"path": src["path"], "kept": DEFAULT_REPORT, "src": source.get("path") or "?"}
    return DEFAULT_REPORT, None


# ---------------------------------------------------------------------------------------------------------------
# Output guard: every path the run writes is checked against every path it reads (and against each other) before
# anything is written, so a refused run leaves the folder exactly as it was.
# ---------------------------------------------------------------------------------------------------------------
def same_file(a: str, b: str) -> bool:
    """True when two paths name one file.

    Comparing resolved paths is not enough: a hard link, or another letter case on a case-insensitive disk (the
    macOS and Windows default), names the same file under a different path. os.path.samefile compares the inode.
    """
    if Path(a).resolve() == Path(b).resolve():
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False  # 둘 중 하나가 아직 없으면 같은 파일일 수 없다


def is_crawlpolicy_report(path: str) -> bool:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("tool") == "crawlpolicy"


def input_files(args, robots_paths: list[str]) -> list[tuple[str, str]]:
    """Every file this run reads, as (what-key for the error message, path)."""
    files = [("what_input", p) for p in robots_paths]
    if args.urls:
        files.append(("what_urls", args.urls))
    if args.bots_file:
        files.append(("what_bots_file", args.bots_file))
    return files


def refuse_input_clash(opt: str, out: str, inputs: list[tuple[str, str]]) -> None:
    for what, path in inputs:
        if same_file(out, path):
            raise CliError(2, "err_out_clash", opt=opt, path=out, what=what)


def check_writable(path: str) -> None:
    """Fail before the first write, so an unwritable report does not leave a fixed copy behind (or the reverse)."""
    p = Path(path)
    if p.is_dir():
        raise CliError(2, "err_write", path=path, why=os.strerror(21))  # EISDIR
    if not p.parent.is_dir():
        raise CliError(2, "err_write", path=path, why=os.strerror(2))  # ENOENT
    if p.exists() and not os.access(p, os.W_OK):
        raise CliError(2, "err_write", path=path, why=os.strerror(13))  # EACCES


def files_line(t, written: list[str], replaced: list[str]) -> str:
    """The last line of every run: inputs untouched, and which outputs were new or replaced an existing file."""
    if replaced:
        return t("outputs_replaced", files=", ".join(written), replaced=", ".join(replaced))
    return t("outputs_kept", files=", ".join(written))


def run_check(args, t, out_lines: list[str], err_lines: list[str]) -> int:
    path = pick_robots(args.robots)
    src = read_robots(path)
    table = load_bot_table(args.bots_file)
    bots = resolve_bots(args.bots, table)
    clauses = parse_intent(args.intent, bots)
    groups, ignored = parse_robots(src["text"])
    given = collect_urls(args.url, args.urls)
    urls = given or sample_urls(groups, clauses)
    if not urls:
        raise CliError(2, "err_no_urls")
    fixed_path = args.fixed_out or DEFAULT_FIXED
    cells = build_cells(groups, bots, urls, clauses)
    violations = [c for c in cells if not c["ok"]]

    # 쓰기 전에 출력 경로를 모두 검사한다 — 거부된 실행은 폴더를 하나도 바꾸지 않는다
    out_path, kept = pick_report_path(args.out, [src])  # 수정본을 다시 검사해도 원본 보고서는 지킨다
    inputs = input_files(args, [path])
    refuse_input_clash("--out", out_path, inputs)
    if violations:  # 수정본은 이번에 쓸 때만 입력과 겹치면 안 된다 (수정본을 다시 검사하는 실행은 그 파일이 입력이다)
        refuse_input_clash("--fixed-out", fixed_path, inputs)
        if is_crawlpolicy_report(fixed_path):
            raise CliError(2, "err_out_clash", opt="--fixed-out", path=fixed_path, what="what_old_report")
    if same_file(out_path, fixed_path):
        # 보고서와 수정본 자리는 이번에 수정안이 없어도 겹치면 안 된다. 사용자가 직접 준 옵션을 탓한다
        if args.fixed_out is not None:
            raise CliError(2, "err_out_clash", opt="--fixed-out", path=fixed_path, what="what_report")
        raise CliError(2, "err_out_clash", opt="--out", path=out_path, what="what_fixed")
    if args.out is not None and Path(out_path).exists() and not is_crawlpolicy_report(out_path):
        raise CliError(2, "err_out_not_report", path=out_path)  # 직접 준 --out 이 사용자 파일을 덮지 않게
    written = [out_path] + ([fixed_path] if violations else [])
    for p in written:
        check_writable(p)
    replaced = [p for p in written if Path(p).exists()]  # files: 줄에서 새로 만든 것과 바꾼 것을 정직하게 나눈다

    fix = None
    fix_report = None
    if violations:
        fix = propose_fix(src["text"], groups, bots, urls, clauses, cells)
        fix_report = {"needed": True, "path": fixed_path, "sha256": hashlib.sha256(fix["text"].encode("utf-8")).hexdigest(),
                      "added": [{"bots": b["bots"], "group_line": b["group_line"], "text": "\n".join(b["lines"])}
                                for b in fix["blocks"]],
                      "patch": "\n".join(difflib.unified_diff(split_lines(src["text"]), split_lines(fix["text"]),
                                                              fromfile=path, tofile=fixed_path, lineterm="", n=1)),
                      "verified": fix["verified"], "violations_after": fix["violations_after"],
                      "regressions": fix["regressions"], "probe_urls": fix["probe_urls"]}
        if fix["too_large"]:
            src["warnings"] = src["warnings"] + ["fix_too_large"]
    elif clauses:
        fix_report = {"needed": False, "path": None, "sha256": None, "added": [], "patch": "", "verified": True,
                      "violations_after": 0, "regressions": 0, "probe_urls": []}
    group_of = {b["token"]: select_groups(groups, b["token"])[1] for b in bots}
    allowed = sum(1 for c in cells if c["allowed"])
    exit_code = 1 if violations else 0
    report = {
        "tool": "crawlpolicy", "version": VERSION, "rfc": "9309", "mode": "check", "source": file_info(src),
        "old": None, "new": None,
        "groups": [{"agents": g["agents"], "lines": g["lines"], "rules": [public_cell(r) for r in g["rules"]]}
                   for g in groups],
        "ignored_lines": ignored, "warnings": src["warnings"],
        "bots": [{"token": b["token"], "role": b["role"], "ai": b["ai"], "provider": b["provider"],
                  "group": group_of[b["token"]], "note": b["note"]} for b in bots],
        "urls": urls, "urls_source": "given" if given else "auto",
        "cells": [public_cell(c) for c in cells],
        "summary": {"cells": len(cells), "allowed": allowed, "denied": len(cells) - allowed, "violations": len(violations)},
        "intent": {"text": args.intent, "clauses": [{"who": c["who"], "path": c["path"], "want": c["want"]} for c in clauses]}
        if clauses else None,
        "fix": fix_report, "changed": [], "exit_code": exit_code,
    }
    if fix:
        write_text(fixed_path, fix["text"])
    write_text(out_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    for w in src["warnings"]:
        if w == "truncated":
            err_lines.append(t("warn_truncated", path=path))
        elif w == "fix_too_large":
            err_lines.append(t("warn_fix_large", path=fixed_path))
    out_lines.extend(render_matrix(bots, urls, cells, t))
    out_lines.append("")
    out_lines.append(t("legend"))
    out_lines.append(t("summary", cells=len(cells), allowed=allowed, denied=len(cells) - allowed, violations=len(violations)))
    if ignored:
        out_lines.append(t("ignored_note", n=len(ignored)))
    if not clauses:
        out_lines.append(t("no_intent"))
    elif not violations:
        out_lines.append(t("ok_all"))
    else:
        out_lines.append("")
        out_lines.append(t("viol_head", n=len(violations)))
        for c in violations:
            out_lines.append(t("viol_line", verdict=c["verdict"], want=c["intent"], clause=clauses[c["_clause"]]["text"]))
        out_lines.append("")
        out_lines.append(t("patch_head", src=path))
        patch_lines = fix_report["patch"].split("\n")
        out_lines.extend(patch_lines[:MAX_PATCH_LINES])
        if len(patch_lines) > MAX_PATCH_LINES:
            out_lines.append(t("patch_more", n=len(patch_lines) - MAX_PATCH_LINES, path=fixed_path))
        out_lines.append("")
        out_lines.append(t("reverified", v=fix["violations_after"], c=fix["regressions"]))
        if not fix["verified"]:
            out_lines.append(t("not_verified"))
        out_lines.append(t("wrote_fixed", path=fixed_path))
    notes = []
    for b in bots:
        if b["note"] and b["note"] not in notes:
            notes.append(b["note"])
    if notes:
        out_lines.append("")
        out_lines.append(t("notes_head"))
        for n in notes:
            who = ", ".join(b["token"] for b in bots if b["note"] == n)
            out_lines.append(f"  {who}: " + t("note_" + n))
    if kept:
        out_lines.append(t("kept_report", out=out_path, **kept))
    out_lines.append(t("wrote_report", path=out_path))
    # "파일을 고치지 말라"는 요청에서도 보고서를 지우지 않게, 입력은 그대로이고 무엇을 새로 썼는지·바꿨는지 마지막 줄에 밝힌다
    out_lines.append(files_line(t, written, replaced))
    return exit_code


def run_diff(args, t, out_lines: list[str], err_lines: list[str]) -> int:
    old, new = read_robots(args.old), read_robots(args.new)
    table = load_bot_table(args.bots_file)
    bots = resolve_bots(args.bots, table)
    g_old, _ = parse_robots(old["text"])
    g_new, _ = parse_robots(new["text"])
    given = collect_urls(args.url, args.urls)
    urls = given or list(dict.fromkeys(sample_urls(g_old, []) + sample_urls(g_new, [])))[:MAX_AUTO_URLS * 2]
    c_old = {(c["bot"], c["url"]): c for c in build_cells(g_old, bots, urls, [])}
    c_new = build_cells(g_new, bots, urls, [])
    changed = []
    for c in c_new:
        o = c_old[(c["bot"], c["url"])]
        if o["allowed"] != c["allowed"]:
            changed.append({"bot": c["bot"], "url": c["url"],
                            "before": {k: o[k] for k in ("allowed", "rule_line", "rule", "verdict")},
                            "after": {k: c[k] for k in ("allowed", "rule_line", "rule", "verdict")}})
    exit_code = 1 if changed else 0
    report = {
        "tool": "crawlpolicy", "version": VERSION, "rfc": "9309", "mode": "diff", "source": None,
        "old": file_info(old), "new": file_info(new), "groups": [], "ignored_lines": [],
        "warnings": old["warnings"] + new["warnings"],
        "bots": [{"token": b["token"], "role": b["role"], "ai": b["ai"], "provider": b["provider"], "group": None,
                  "note": b["note"]} for b in bots],
        "urls": urls, "urls_source": "given" if given else "auto", "cells": [],
        "summary": {"cells": len(c_new), "allowed": sum(1 for c in c_new if c["allowed"]),
                    "denied": sum(1 for c in c_new if not c["allowed"]), "violations": 0},
        "intent": None, "fix": None, "changed": changed, "exit_code": exit_code,
    }
    out_path, kept = pick_report_path(args.out, [old, new])  # 원본과 수정본의 diff 도 원본 보고서를 덮지 않는다
    refuse_input_clash("--out", out_path, input_files(args, [args.old, args.new]))
    if args.out is not None and Path(out_path).exists() and not is_crawlpolicy_report(out_path):
        raise CliError(2, "err_out_not_report", path=out_path)  # 수정본(robots.fixed.txt) 같은 사용자 파일도 지킨다
    check_writable(out_path)
    replaced = [out_path] if Path(out_path).exists() else []
    write_text(out_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    for src, name in ((old, args.old), (new, args.new)):
        for _ in src["warnings"]:
            err_lines.append(t("warn_truncated", path=name))
    out_lines.append(t("diff_head", old=args.old, new=args.new))
    if not changed:
        out_lines.append(t("diff_none", cells=len(c_new)))
    else:
        out_lines.append(t("diff_some", n=len(changed), cells=len(c_new)))
        for ch in changed:
            def short(v: dict) -> str:
                return ("allow" if v["allowed"] else "deny") + f" @L{v['rule_line'] or 0}"
            # 어느 봇이 접근을 얻었는지·잃었는지 줄마다 낱말로 밝힌다 (allow/deny 화살표만으로는 답이 흔들린다)
            change = t("diff_gained") if ch["after"]["allowed"] else t("diff_lost")
            out_lines.append(t("diff_line", bot=ch["bot"], url=ch["url"], before=short(ch["before"]),
                               after=short(ch["after"]), change=change))
    if kept:
        out_lines.append(t("kept_report", out=out_path, **kept))
    out_lines.append(t("wrote_report", path=out_path))
    out_lines.append(files_line(t, [out_path], replaced))  # diff 는 보고서 하나만 쓴다
    return exit_code


# ---------------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------------
def usage_error(message: str) -> CliError:
    """Map the common argparse messages to translated ones; anything else keeps argparse's wording as detail."""
    m = re.match(r"unrecognized arguments: (.*)$", message)
    if m:
        return CliError(2, "err_unrecognized", args=m.group(1))
    m = re.match(r"argument (\S+): invalid choice: '([^']*)'.*?\(choose from (.*)\)$", message)
    if m:
        return CliError(2, "err_invalid_choice", value=m.group(2), choices=m.group(3).replace("'", ""))
    m = re.match(r"argument (\S+): expected one argument$", message)
    if m:
        return CliError(2, "err_expected_arg", opt=m.group(1))
    m = re.match(r"the following arguments are required: (.*)$", message)
    if m:
        return CliError(2, "err_required", args=m.group(1))
    return CliError(2, "err_usage", detail=message)


def build_parser(t) -> argparse.ArgumentParser:
    class Fmt(argparse.RawDescriptionHelpFormatter):
        def add_usage(self, usage, actions, groups, prefix=None):
            return super().add_usage(usage, actions, groups, t("usage_prefix") if prefix is None else prefix)

    class Parser(argparse.ArgumentParser):
        def __init__(self, *a, **kw):
            kw.setdefault("formatter_class", Fmt)
            kw["add_help"] = False
            super().__init__(*a, **kw)
            self._positionals.title = t("title_positional")
            self._optionals.title = t("title_options")
            self.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help=t("help_help"))

        def error(self, message):  # one-line error, exit 2, never a usage dump
            raise usage_error(message)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--bots", metavar="LIST", help=t("help_bots"))
        p.add_argument("--bots-file", metavar="FILE", help=t("help_bots_file"))
        p.add_argument("--url", action="append", metavar="U", help=t("help_url"))
        p.add_argument("--urls", metavar="FILE", help=t("help_urls"))
        p.add_argument("--out", default=None, metavar="FILE", help=t("help_out"))  # None = DEFAULT_REPORT (see pick_report_path)
        p.add_argument("--lang", choices=sorted(MESSAGES), default=argparse.SUPPRESS, help=t("help_lang"))

    parser = Parser(prog="crawlpolicy.py", description=t("help_description"))
    parser.add_argument("--version", action="version", version=f"crawlpolicy {VERSION}", help=t("help_version"))
    parser.add_argument("--lang", choices=sorted(MESSAGES), default=argparse.SUPPRESS, help=t("help_lang"))
    sub = parser.add_subparsers(dest="command", metavar="{check,diff}", title=t("title_commands"),
                                parser_class=Parser)
    sub.required = True
    c = sub.add_parser("check", help=t("help_check"), description=t("help_check"))
    c.add_argument("robots", nargs="?", metavar="ROBOTS", help=t("help_robots"))
    c.add_argument("--intent", metavar="CLAUSES", help=t("help_intent"))
    # None = DEFAULT_FIXED; 직접 준 값인지 알아야 경로 충돌 오류에서 그 옵션을 탓할 수 있다
    c.add_argument("--fixed-out", default=None, metavar="FILE", help=t("help_fixed_out"))
    common(c)
    d = sub.add_parser("diff", help=t("help_diff"), description=t("help_diff"))
    d.add_argument("old", metavar="OLD", help=t("help_old"))
    d.add_argument("new", metavar="NEW", help=t("help_new"))
    common(d)
    return parser


def _flag_value(argv: list[str], flag: str) -> str | None:
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(flag + "="):
            return a[len(flag) + 1:]
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cli_lang = _flag_value(argv, "--lang")
    bad_lang = cli_lang if cli_lang is not None and cli_lang not in MESSAGES else None
    lang = resolve_lang(None if bad_lang else cli_lang)
    t = make_t(lang)
    out_lines: list[str] = []
    err_lines: list[str] = []
    code = 0
    try:
        if bad_lang is not None:
            raise CliError(2, "err_bad_lang", value=bad_lang)
        args = build_parser(t).parse_args(argv)
        code = run_check(args, t, out_lines, err_lines) if args.command == "check" else run_diff(args, t, out_lines, err_lines)
    except CliError as e:
        kw = {k: (t(v) if k == "what" else v) for k, v in e.kw.items()}  # 'what' 은 메시지 키라 언어별로 바꾼다
        err_lines.append(t("err_prefix") + t(e.key, **kw))
        code = e.code
    except SystemExit as e:  # --help / --version already printed by argparse
        return int(e.code or 0)
    if out_lines:
        sys.stdout.write("\n".join(out_lines) + "\n")
    if err_lines:
        sys.stderr.write("\n".join(err_lines) + "\n")
    return code


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
