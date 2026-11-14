# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-11-14

### Fixed

- Running `check` or `diff` on the unchanged fixed copy after a fix run (a natural double check) no longer replaces
  the report for the original file: that run is written to `crawlpolicy-report.fixed.json` and a note says so. An
  explicit `--out` is always used as given, and a fixed copy the user has edited gets the usual default report.
- The fix run says the fixed copy was already re-checked, and the skill instructions say not to run the script on
  `robots.fixed.txt` again.
- The fixed copy is written byte for byte (no newline translation), so its bytes always match `fix.sha256`.
- README: the measurement table uses the 0.1.1 run (mean score gain +0.59) and the test count is 122.

### Known issues

- The note that a report was written to `crawlpolicy-report.fixed.json` is printed in the terminal but is not stored
  in the JSON report.
- Still open from 0.1.0: the contradictory verification lines for fixed copies over 500 KiB, the Applebot fallback
  note shown even when the file has an `Applebot` group, and the unneeded legend in single-bot line output.
- The Korean screenshot `docs/images/check-fix.ko.png` shows an English shell comment (`# fix part of the output`)
  on the command line; it will be replaced when screenshots are next retaken.

## [0.1.0] - 2026-11-14

### Fixed

- The proposed fix now beats a longer opposing rule as a whole prefix (`Disallow: /blog/public/*`) instead of
  pinning only the sampled URL, and re-verification also judges one child probe per rule and intent path.
- Rule patterns are compiled once per rule and verdicts are shared between bots using the same groups, so a 500 KiB
  file is judged in seconds; the printed patch is cut at 40 lines and a fixed copy over 500 KiB is reported as not
  verified with a warning.
- Usage errors (unknown option, bad command, missing value or argument) are translated in `--lang ko`, with a hint.
- `check --help` no longer prints the usage prefix twice.
- A `#` comment that mentions `<html` no longer makes a valid robots.txt look like a web page.

### Added

- `check`: one run builds the crawler x URL verdict matrix by RFC 9309 (winning rule line per cell, group merge,
  no inheritance of the `*` group, exact product-token match, longest match with Allow on ties, `*` and `$`,
  percent normalization), compares it with a one-line intent, proposes a minimal fix, re-verifies the fixed copy
  (`robots.fixed.txt`) and writes `crawlpolicy-report.json`.
- `diff OLD NEW`: lists only the cells whose verdict changed; exit code 1 when any did.
- Built-in crawler table (OpenAI, Anthropic, Google, Apple, Perplexity, Common Crawl; sources and check date in the
  script), `--bots` with roles or any token (`DocsHarvester/2.1` becomes `DocsHarvester`), `--bots-file` to replace
  the table.
- Intent grammar `<who>[:<path>]=allow|deny` joined by `;`, roles `training`, `search`, `user`, `control`, `ai`, `all`.
- Auto-detection of `./robots.txt`, `public/`, `static/`, `app/` when the path is omitted.
- English and Korean output and `--help` (`--lang`, `CRAWLPOLICY_LANG`, `LC_ALL`/`LC_MESSAGES`/`LANG`).
- Exit codes 0 (ok), 1 (intent violated or verdicts changed), 2 (input error), 3 (robots.txt not found or ambiguous).
- Agent skill (`skills/crawlpolicy/SKILL.md`), Claude Code plugin and marketplace manifests, evaluation set.

### Known issues

- When the fixed copy is over 500 KiB, the `re-verified: 0 violations, 0 changed cells` line is still printed above the
  "could not be fully verified" warning; read the warning as the result.
- The Applebot fallback note is printed even when the file has an `Applebot` group.
- The legend (`A`, `D`, `!`) is also printed for the single-bot line output, where those symbols are not used.
