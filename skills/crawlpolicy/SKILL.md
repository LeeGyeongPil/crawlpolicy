---
name: crawlpolicy
description: Computes with a bundled script what a saved robots.txt really allows or blocks for each crawler and URL by RFC 9309 (winning rule line, no inheritance of the * group, exact product-token match, longest match), compares the result with the user's intent such as "block AI training crawlers, keep search allowed", proposes a minimal fix, re-verifies the fixed copy and diffs two versions of the file. Use when the user asks whether a robots.txt blocks or allows GPTBot, ClaudeBot, Google-Extended, Applebot, PerplexityBot or any other bot or URL, wants AI training crawlers blocked while search or user-request crawlers stay allowed, wants a robots.txt checked or fixed before deploy, asks whether their scraper or RAG collector (for example DocsHarvester/2.1) may fetch URLs under a site's robots.txt, or wants an old and a new robots.txt compared (also asked in Korean about robots.txt 크롤러 허용·차단). Not for server blocking rules, llms.txt, meta robots tags, X-Robots-Tag headers, sitemaps or writing crawlers.
license: MIT
compatibility: Python 3.10+ (standard library only). No network access.
---

# Judge a robots.txt per crawler and URL

People and models misjudge robots.txt by hand: the longest rule wins (not the first), Allow wins a tie, `$` anchors
the end, groups naming the same bot are merged, a bot with its own group does not inherit `*`, and `Applebot` is not
`Applebot-Extended`. So even when the file looks short and simple, do not judge it by reading it: run the bundled
script once, as your first step, in the user's folder, with no listing or reading of files before it. `<skill-dir>` is
the folder that holds this SKILL.md; it holds `scripts/crawlpolicy.py`, so call it by that full path.

Check a file against what the user wants (also fixes and re-verifies when the intent is violated):

```
python3 <skill-dir>/scripts/crawlpolicy.py check --intent "training=deny; search=allow; all:/admin/=deny" --lang en
```

Compare two versions:

```
python3 <skill-dir>/scripts/crawlpolicy.py diff robots.old.txt robots.txt --lang en
```

- Give `check` no file when the user says "robots.txt", "our robots.txt" or "the one here": the script uses the
  only one of `./robots.txt`, `public/`, `static/`, `app/` and stops with exit 3 when there are several. Pass a
  path only when the user gave a path or another file name. Use `--lang ko` for a Korean answer.
- For a question about some bots or URLs, leave out `--intent` and add `--bots "GPTBot,ClaudeBot"` plus one
  `--url /path` per URL asked (or `--urls FILE`, one per line). A collector name such as `DocsHarvester/2.1` works in
  `--bots`. Without `--url` the script judges `/` and the paths named in the rules, not the URLs asked.
- Intent: clauses `<who>[:<path>]=allow|deny` joined by `;`. `<who>` is `training`, `search`, `user`, `control`,
  `ai` (all AI bots), `all` or one bot token; `<path>` is a robots pattern (`/admin/`, `/*.pdf$`), default `/`. A
  later clause overrides an earlier one; cells no clause covers are not checked.
- Run it once and answer from that run. It already re-checked its fix, so do not run it again on `robots.fixed.txt`.
- The script never changes the user's files. It writes its JSON record (and the fix `robots.fixed.txt` when the
  intent is violated) to the current folder; the last `files:` line names them. These are output files, not edits,
  so they stay even when the user says "do not edit any file":
  do not delete, move or redirect them and do not pass `--out`. Never edit the user's robots.txt.

## Reading the output

- Few cells: one line per cell, `<bot> <url> => allow|deny @L<n>  (<winning rule>)`. `@L0` with `no matching rule`
  means nothing applies, so the URL is allowed. Many cells: a table where `A@L2` is allow by line 2 and `D@L7` deny
  by line 7. A trailing `!` marks a cell that breaks the intent.
- Violations repeat as verdict lines, then a patch and `re-verified: <n> violations, <m> changed cells`. Zero and zero
  means the fix meets the intent and changes no other cell; anything else: say the fix is not verified.
- `diff` prints one line per changed cell ending in `(gained access)` or `(lost access)`.
- Exit code: 0 intent holds or nothing changed; 1 violations or changed verdicts; 2 input problem; 3 no single
  robots.txt could be picked.

## Report back

- Start the answer with the verdict lines, violation lines or diff lines pasted exactly as printed, in a code block,
  before any summary. Then say in plain words which bots may or may not fetch which URLs, with the deciding line.
- For violations, add the patch as printed and the cause in one sentence (often: a bot group does not inherit `*`).
- Mention printed provider notes: user-request bots such as ChatGPT-User may not follow robots.txt, and
  `Google-Extended` or `Applebot-Extended` only control use. robots.txt is a request, not enforcement.
- Exit code 2 or 3: show the error line as is and stop; it says how to fix the input. Ask for the file or path.
