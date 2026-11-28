# Spike: LinkedIn little-text serializer and post lint

**Verdict: go with changes.** The offline checks are complete. Three behaviours can only be confirmed by
the live Phase 0 test (see Open questions).

| Item | Location |
|---|---|
| Code | `server/src/blogwriter/linkedin/littletext.py`, `server/src/blogwriter/linkedin/lint.py` |
| Tests | `server/tests/linkedin/`: 339 passing |
| Script | `server/scripts/linkedin_spike.py`, dry run by default |

Checks: `ruff check`, `ruff format --check` and strict `pyright` are clean.
Run: `cd server && uv sync && uv run pytest -q`

## Design
- **Token model.** A post is made of `Text`, `Hashtag`, `Mention` and `Link` tokens, and has two representations kept in lockstep:
  - `display_text(tokens)` is what readers see, what the kit copies, and what counts are based on.
  - `api_commentary(tokens)` is the escaped `commentary` payload for `/rest/posts`.
  - `unescape(api_commentary(t)) == display_text(t)` holds for every valid sequence. It is fuzzed with 400 seeded strings.
- **Escaping.** The reserved characters are `| { } @ [ ] ( ) < > # \ * _ ~`. Each one in a `Text` run gets a backslash in a single `str.translate` pass, so a backslash is never escaped twice.
- **Exceptions to escaping**
  - An approved `Hashtag` goes out as plain `#Tag`.
  - A `Mention` goes out as `@[Name](urn:li:person:…|urn:li:organization:…)`.
  - `Link` tokens are refused unless `allow_links=True`. Each link sits on its own line and is escaped like text, because escaping can't cause a silent truncation.
- **Hashtag recognition** (`parse_display`)
  - Only tags on the approved list count, matched case-insensitively with the canonical spelling sent.
  - The tag must be a whole word at grapheme-cluster boundaries.
  - These stay escaped text: `C#`, `foo#bar`, `(#AI)`, `#AI's`, and unapproved tags.

## Lint rules (`lint_post`)
| Code | Severity | Rule |
|---|---|---|
| `too-long` | block | The escaped payload must be at most 3,000 **UTF-16 units**. Escapes count, and emoji count as 2. This is the conservative reading until the live test. |
| `leading-blank` | block | The post can't start with whitespace or a blank line. |
| `invalid-text` | block | Control characters and lone surrogates (raised by the serializer). |
| `hook-past-fold` | warn | The first line must end before the mobile fold. The fold is at 140 characters or 3 rendered lines, whichever comes first. Blank lines count, and a line wraps every 48 characters (a heuristic). |
| `external-link`, `email-address` | block | `http(s)://` and `www.` URLs, and email addresses. `allow_links` permits URLs only. |
| `domain-like` | block | Words LinkedIn auto-links, such as `README.md`, `setup.py`, `install.sh`, `x.ai`, `foo.io`, `ASP.NET` and `learn.microsoft.com`. Matching uses a list of real TLDs at word boundaries. |
| `filename-like` | warn | `config.yaml`, `data.json` and similar non-TLD extensions. |
| `too-many-hashtags` | block | More than 3 approved hashtags. |
| `unapproved-hashtag` | warn | A `#word` that would post as plain text. |
| `lookalike-hashtag` | warn | A full-width `＃` used like a hashtag. |
| `too-many-emoji` | warn | More than 2 emoji, counted by grapheme cluster. ZWJ families, flags, keycaps and skin tones each count as 1. |
| `invisible-character` | block | Default-ignorable code points: ZWSP, word joiner, BOM, soft hyphen, bidi controls, CGJ, Hangul fillers, Mongolian VS, and so on. ZWJ, VS15/16 and tag characters are allowed inside emoji clusters. |
| `unicode-styled-text` | block | Math bold/italic letters (U+1D400–U+1D7FF). |
| `engagement-bait` | block | Imperatives at the start of a sentence: comment YES, save this (post), follow for more, repost/like/share if, tag someone, drop your thoughts. Also `agree?`. Prose such as "it looks like if…" or "save this setting" doesn't match. |
| `formula-opener` | block | Openers such as "Unpopular opinion", "Here's the thing", "X is dead.", "Most people get X wrong" and "Nobody talks about". "Let that sink in" blocks anywhere in the post. |
| `ai-tell` | warn | The list from `backend/prompts/humanizer_prompt.md:20-37`. "critical" is left out because it is a real security severity level. A leading "TL;DR" also warns. |
| `exclamation` | warn | More than one exclamation mark. |
| `em-dash-wall` | warn | Three or more em dashes. |

Prose that must **not** match is covered by false-positive tests: e.g., i.e., U.S., etc., v1.2.3, OAuth 2.0, 3.14, 10.30, Node.js, Next.js, 9.8 and SP 800-63-4.

## Open questions for the live Phase 0 test (`scripts/linkedin_spike.py --live`)
1. Does LinkedIn count the 3,000 limit before or after escapes, and in UTF-16 or code points? The lint assumes the strictest reading.
2. Do escaped reserved characters inside a URL (allowed only with the link exception) stay clickable?
3. Can a token with only `w_member_social` poll `GET /rest/documents/{urn}`? The script falls back to a fixed wait if not.
4. Do the rendered mobile fold and the 48-characters-per-line heuristic match a real phone? Check against the published text post.

## Plan changes from this spike
- Approvals store both representations: the display text for the kit and counts, and the API payload. The invariant `payload == api_commentary(parse_display(display))` is tested.
- The bait rules are anchored to sentence starts. Unanchored phrase lists produce false positives in technical prose.

## Reviewer notes (main agent, 2026-11-26)
The independent review agent timed out before reporting. These checks were run directly instead.
- **Worst-case timing on 3,000-character adversarial inputs:** 17 shapes tested (dot, dash and label runs, email-like text, @, # and ! runs, emoji and ZWJ runs, URL runs, opener baits). Every lint run finished under 10 ms after one fix.
  - The fix: the email pattern was quadratic, 78 ms on `x.x.…@`. A lookbehind that pins the start of the local part brought it to 4.8 ms.
  - Regression tests now cover these inputs.
- **Invisible-character runs** now merge into a single finding instead of one finding per character.
- **False positives:** a corpus of 30 realistic sentences about Entra, OAuth, MCP, NIST AI RMF, ATLAS IDs, CVEs, OWASP LLM IDs, versions, decimals and times produces **0 findings**. 24 of them are pinned as tests.
- **Totals:** 369 tests pass; ruff and strict pyright are clean.
