---
name: crawlpolicy
description: Computes with a bundled script what a saved robots.txt really allows or blocks for each crawler and URL by RFC 9309 (winning rule line, no inheritance of the * group, exact product-token match, longest match), compares the result with the user's intent such as "block AI training crawlers, keep search allowed", proposes a minimal fix, re-verifies the fixed copy and diffs two versions of the file. Use when the user asks whether a robots.txt blocks or allows GPTBot, ClaudeBot, Google-Extended, Applebot, PerplexityBot or any other bot or URL, wants AI training crawlers blocked while search or user-request crawlers stay allowed, wants a robots.txt checked or fixed before deploy, asks whether their scraper or RAG collector (for example DocsHarvester/2.1) may fetch URLs under a site's robots.txt, or wants an old and a new robots.txt compared (also asked in Korean about robots.txt 크롤러 허용·차단). Not for server blocking rules, llms.txt, meta robots tags, X-Robots-Tag headers, sitemaps or writing crawlers.
license: MIT
compatibility: Python 3.10+ (standard library only). No network access.
---

# Judge a robots.txt per crawler and URL

Your next step is one command, run in the user's folder, with no listing or reading of files first: the script finds
`robots.txt`, judges every bot and URL, compares with the intent, writes a fix and re-checks it. `<skill-dir>` below is
the folder that holds this SKILL.md; call the script by that full path and stay in the user's folder, because
the JSON report and the fixed copy `robots.fixed.txt` land in the current folder. People and models misjudge robots.txt by hand: the longest
rule wins (not the first), a bot with its own group does not inherit the `*` rules, and `Applebot` is not
`Applebot-Extended`.

The bundled script is `scripts/crawlpolicy.py` inside `<skill-dir>`; run it by its full path.

Check a file against what the user wants (also fixes and re-verifies when the intent is violated):

```
python3 <skill-dir>/scripts/crawlpolicy.py check robots.txt --intent "training=deny; search=allow; all:/admin/=deny" --lang en
```

Compare two versions:

```
python3 <skill-dir>/scripts/crawlpolicy.py diff robots.old.txt robots.txt --lang en
```

- Leave out `robots.txt` when the user did not name the file: the script uses the only one of `./robots.txt`,
  `public/`, `static/`, `app/`. Use `--lang ko` for a Korean answer.
- Leave out `--intent` when the user only asks whether a bot may fetch something. Then add `--bots "GPTBot"` and
  `--url /path` (repeat `--url`) or `--urls FILE` (one URL or path per line). A collector name such as
  `DocsHarvester/2.1` is accepted in `--bots`. Without `--url` the script judges `/` and every path named in the rules.
- Intent: clauses `<who>[:<path>]=allow|deny` joined by `;`. `<who>` is `training`, `search`, `user`, `control`,
  `ai` (all AI bots) or `all`, or one bot token such as `GPTBot`. `<path>` is a robots pattern (`/admin/`, `/*.pdf$`),
  default `/`. A later clause overrides an earlier one; cells no clause covers are not checked.
- Run it once and answer from that run. That run already re-checked the fixed copy, so do not run `check` or `diff`
  on `robots.fixed.txt` afterwards. Never edit the user's robots.txt: the fix is the copy `robots.fixed.txt`.

## Reading the output

- A table of bots by URLs (`A@L3` is allow decided by line 3, `D@L7` deny by line 7, `@L0` no rule applies, `!` the
  intent is violated), or one `<bot> <url> => allow|deny @L<n>` line per URL when a single bot is asked.
- Violations come as the same verdict lines, then a patch and `re-verified: <n> violations, <m> changed cells`. Zero
  and zero means the fixed copy meets the intent without changing any other cell. Anything else: say the fix is not
  verified and show what is left.
- Exit code: 0 intent holds or nothing changed; 1 violations found or verdicts changed; 2 an input problem; 3 no single
  robots.txt could be picked.

## Report back

- Paste the violation lines and the patch as printed, then say in plain words which bots end up allowed and blocked.
  Explain the usual cause in one sentence: a bot group without the `*` rules does not inherit them.
- Mention the provider notes when printed: user-request bots such as ChatGPT-User may not follow robots.txt, and
  `Google-Extended` or `Applebot-Extended` only control use, they do not crawl. robots.txt is a request, not enforcement.
- Exit code 2 or 3: show the error line as is and stop. It says how to fix the input; ask the user for the file or path.
