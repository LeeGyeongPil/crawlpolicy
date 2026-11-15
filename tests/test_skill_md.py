"""AC-8: static checks on SKILL.md that keep a skill-eval run inside its 6 turns."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "crawlpolicy" / "SKILL.md"
ALLOWED_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def _split():
    text = SKILL.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    assert m, "frontmatter missing"
    keys = {line.split(":", 1)[0] for line in m.group(1).splitlines() if re.match(r"^[A-Za-z-]+:", line)}
    return keys, m.group(1), m.group(2)


def test_frontmatter_standard_keys_only():
    keys, front, body = _split()
    assert keys <= ALLOWED_KEYS and {"name", "description"} <= keys
    desc = re.search(r"^description: (.*)$", front, re.M).group(1)
    assert len(desc) <= 1024 and not re.search(r"[<>]", desc) and "Use when" in desc
    assert len(body.splitlines()) <= 500


def test_core_commands_at_most_two():
    _, _, body = _split()
    blocks = re.findall(r"```\n(.*?)```", body, re.S)
    commands = [ln for b in blocks for ln in b.splitlines() if "crawlpolicy.py" in ln]
    assert len(commands) == 2
    assert [c.split()[2] for c in commands] == ["check", "diff"]
    assert all(c.startswith("python3 <skill-dir>/scripts/crawlpolicy.py ") for c in commands)
    assert "--intent" in commands[0] and len(body.splitlines()) < 80


def test_intent_grammar_is_inline():
    _, _, body = _split()
    for word in ("<who>[:<path>]=allow|deny", "training", "search", "user", "control", "ai", "all", "later clause"):
        assert word in body, word


def test_no_help_or_exploration():
    _, _, body = _split()
    assert "--help" not in body and "-h " not in body
    assert not re.search(r"`(ls|find|cat|grep|head|tree)\b", body)
    assert not re.search(r"\b(list|explore|browse|search through) the (folder|files|directory)\b", body, re.I)
    assert not re.search(r"(open|read|re-?read|inspect|parse) (the )?(json )?report", body, re.I)
    assert "crawlpolicy-report" not in body


def test_body_at_most_60_lines():
    _, _, body = _split()
    assert len(body.strip("\n").splitlines()) <= 60


def test_run_first_and_paste_lines_rules():
    """plan v3 section 4: run the script even for a simple-looking file, ask with --bots/--url, paste the lines first."""
    _, _, body = _split()
    flat = " ".join(body.split())
    assert "even when the file looks short and simple, do not judge it by reading it: run the bundled script once" in flat
    assert "as your first step" in flat
    assert "--bots" in body and "--url /path` per URL asked" in flat
    assert ("Start the answer with the verdict lines, violation lines or diff lines pasted exactly as printed, in a "
            "code block, before any summary.") in flat
    assert "=> allow|deny @L<n>  (<winning rule>)" in body                    # the shape the agent will see
    assert "Exit code 2 or 3: show the error line as is and stop" in flat
    assert "do not run it again on `robots.fixed.txt`" in flat


def test_package_has_no_agent_config_or_hooks():
    for name in (".claude", "CLAUDE.md", "AGENTS.md", ".mcp.json", "hooks", "agents", "commands"):
        assert not (REPO / name).exists(), name
    manifest = (REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    assert not re.search(r'"(hooks|mcpServers|agents|commands)"', manifest)
