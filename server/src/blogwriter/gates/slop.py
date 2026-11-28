"""The structural slop detectors and the AI-tell lint (the ``SlopChecker`` port).

Plan, gate 4: "Structural slop detectors: 'isn't X, it's Y', rule-of-three density,
rhetorical-question openers, colon reveals, formulaic closers, runs of one-sentence paragraphs,
low variation in sentence length. Also a minimum density of specifics."

Every finding is a ``warn`` with a code-point span into the checked text (``flat-rhythm`` and a
document-wide ``low-specificity`` have no span when no single place is to blame). Detectors
that measure a density report only above their threshold; the others report every occurrence.
Whether a text is "over threshold" as a whole is the caller's decision: gate 4 turns the
findings into one overridable blocker (:mod:`blogwriter.gates.originality`); posts and voice
show them.

=====================  =====================================================================
code                   what
=====================  =====================================================================
``isnt-x-its-y``       "X isn't A, it's B" and its variants ("not just A, but B", "It's not
                       A. It's B.", "less about A, more about B")
``rule-of-three``      triads ("fast, cheap, and safe"; "Faster. Cheaper. Safer.") when they
                       are denser than :attr:`SlopThresholds.triads_per_1000_words`;
                       triads of names, numbers or code are specifics, not slop
``rhetorical-opener``  a paragraph that opens with a question the writer then answers
``colon-reveal``       "The result: ...", "Here's the thing: ...", "The catch? ..."
``formulaic-closer``   "In conclusion", "Ultimately", "Let that sink in", "What do you
                       think?" in the last two paragraphs
``one-sentence-run``   a run of :attr:`SlopThresholds.one_sentence_run` or more one-sentence
                       paragraphs
``flat-rhythm``        a stretch of sentences whose lengths barely vary (coefficient of
                       variation below :attr:`SlopThresholds.rhythm_min_cv`)
``low-specificity``    fewer than :attr:`SlopThresholds.specifics_per_100_words` specifics
                       (numbers, names, acronyms, identifiers, inline code) per 100 words;
                       reported on the vaguest paragraphs
``ai-tell``            ``blogwriter.linkedin.lint.AI_TELLS`` with that module's exact recipe
                       (apostrophes U+2018/U+2019 read as ``'``, each pattern wrapped as
                       ``(?i)(?<![\\p{L}\\p{N}])<pattern>(?![\\p{L}\\p{N}])``) and a leading
                       "TL;DR"
=====================  =====================================================================

Messages never quote the text (the span shows where); an ``ai-tell`` message names the phrase
from the fixed list. CPU-bound: run long texts through ``asyncio.to_thread``.
"""

from __future__ import annotations

import bisect
import itertools
import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

import regex

from blogwriter.domain.common import Finding
from blogwriter.gates.text import (
    Block,
    Sentence,
    TextView,
    markdown_view,
    plain_view,
    sentences,
    words,
)
from blogwriter.linkedin.lint import AI_TELLS

__all__ = [
    "AI_TELL",
    "ARTICLE_THRESHOLDS",
    "COLON_REVEAL",
    "FLAT_RHYTHM",
    "FORMULAIC_CLOSER",
    "ISNT_X_ITS_Y",
    "LOW_SPECIFICITY",
    "ONE_SENTENCE_RUN",
    "PLAIN_THRESHOLDS",
    "RHETORICAL_OPENER",
    "RULE_OF_THREE",
    "SLOP_CODES",
    "STRUCTURAL_CODES",
    "SlopDetectors",
    "SlopThresholds",
    "TextMeasure",
    "measure",
]

ISNT_X_ITS_Y: Final = "isnt-x-its-y"
RULE_OF_THREE: Final = "rule-of-three"
RHETORICAL_OPENER: Final = "rhetorical-opener"
COLON_REVEAL: Final = "colon-reveal"
FORMULAIC_CLOSER: Final = "formulaic-closer"
ONE_SENTENCE_RUN: Final = "one-sentence-run"
FLAT_RHYTHM: Final = "flat-rhythm"
LOW_SPECIFICITY: Final = "low-specificity"
AI_TELL: Final = "ai-tell"

SLOP_CODES: Final = (
    ISNT_X_ITS_Y,
    RULE_OF_THREE,
    RHETORICAL_OPENER,
    COLON_REVEAL,
    FORMULAIC_CLOSER,
    ONE_SENTENCE_RUN,
    FLAT_RHYTHM,
    LOW_SPECIFICITY,
    AI_TELL,
)
STRUCTURAL_CODES: Final = frozenset(
    {
        ISNT_X_ITS_Y,
        RULE_OF_THREE,
        RHETORICAL_OPENER,
        COLON_REVEAL,
        FORMULAIC_CLOSER,
        ONE_SENTENCE_RUN,
        FLAT_RHYTHM,
    }
)
"""The seven structural detectors of the plan (gate 4 counts them against its budget)."""


@dataclass(frozen=True, slots=True)
class SlopThresholds:
    """Where the density detectors start to report."""

    one_sentence_run: int = 4
    triad_min: int = 2
    triads_per_1000_words: float = 4.0
    rhythm_min_sentences: int = 8
    rhythm_window: int = 10
    rhythm_step: int = 5
    rhythm_min_cv: float = 0.30
    specifics_min_words: int = 60
    specifics_per_100_words: float = 2.0
    vague_paragraph_words: int = 30
    opener_max_words: int = 20
    reveal_question_max_words: int = 4


PLAIN_THRESHOLDS: Final = SlopThresholds()
"""Posts, voice samples, comments: LinkedIn breaks lines often, so a run needs 4."""
ARTICLE_THRESHOLDS: Final = SlopThresholds(one_sentence_run=3)
"""Long-form: three one-sentence paragraphs in a row is already a run."""

_MESSAGES: Final = {
    ISNT_X_ITS_Y: "“Isn't X, it's Y” construction. Say what it is, directly.",
    RULE_OF_THREE: "Triads everywhere read as formula. Keep the items that carry a fact.",
    RHETORICAL_OPENER: "Opens with a question the paragraph answers. Lead with the answer.",
    COLON_REVEAL: "Colon or question reveal (“The result: ...”). State it plainly.",
    FORMULAIC_CLOSER: "Formulaic closer. End on a concrete point, not a slogan or a prompt.",
    ONE_SENTENCE_RUN: "A run of one-sentence paragraphs. Join the thoughts that belong together.",
    FLAT_RHYTHM: "Sentence lengths barely vary here. Mix short and long sentences.",
    LOW_SPECIFICITY: (
        "Few specifics: no numbers, names, tools or first-hand details. Add what only you know."
    ),
}

_APOSTROPHES: Final = str.maketrans({"\u2019": "'", "\u2018": "'"})
_DASHES: Final = r"(?:,|;|\u2014|\u2013|\s-\s)"

# -- isn't X, it's Y ---------------------------------------------------------------------------
_NEG: Final = (
    r"(?:isn't|is\s+not|aren't|are\s+not|wasn't|was\s+not|weren't|were\s+not|'s\s+not|'re\s+not)"
)
_DET: Final = (
    r"(?:a|an|the|just|only|merely|simply|really|about|your|our|their|my|its|his|her|some|any"
    r"|more|less|one|another|what|how|why|who|where|when)"
)
_PRONOUN_IS: Final = r"(?:it|they|this|that|he|she|we|you)(?:'s|'re|\s+is|\s+are|\s+was|\s+were)"
_SAME_SENTENCE: Final = (
    re.compile(
        rf"\b{_NEG}\s+{_DET}\b[^.!?;:\n]{{0,80}}?{_DASHES}\s*(?:but\s+)?{_PRONOUN_IS}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:doesn't|does\s+not|don't|do\s+not)\s+(?:just\s+|only\s+|merely\s+|simply\s+)?"
        rf"[a-z]+\b[^.!?;:\n]{{0,80}}?{_DASHES}\s*(?:but\s+)?(?:it|they|this|that|we|you)\s+"
        r"(?:[a-z]+s|do|does)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bnot\s+(?:just|only|merely|simply)\s+(?:about\s+)?[^.!?;:\n]{1,60}?"
        rf"(?:,|\u2014|\u2013|\s-\s)\s*(?:but\b|{_PRONOUN_IS}\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:less|not\s+so\s+much)\s+about\s+[^.!?;:\n]{1,60}?"
        r"(?:,|\u2014|\u2013|\s-\s|\s+and)\s*(?:more|but)\b",
        re.IGNORECASE,
    ),
)
_FIRST_OF_PAIR: Final = re.compile(rf"\b{_NEG}\s+{_DET}\b", re.IGNORECASE)
_SECOND_OF_PAIR: Final = re.compile(
    rf"^(?:but\s+|instead,?\s+|rather,?\s+)?{_PRONOUN_IS}\s+"
    rf"(?:{_DET}|all|actually|really|now|still|always|never)\b",
    re.IGNORECASE,
)
_PAIR_MAX_WORDS: Final = 20

# -- rule of three ----------------------------------------------------------------------------
_ITEM: Final = r"[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*){0,2}"
_TRIAD: Final = re.compile(rf"\b({_ITEM}),\s+({_ITEM}),?\s+(?:and|or)\s+({_ITEM})\b")
_CONJUNCTIONS: Final = frozenset({"and", "or", "but", "nor"})

# -- colon reveals ----------------------------------------------------------------------------
_REVEAL_NOUN: Final = (
    r"(?:results?|answer|catch|kicker|twist|truth|problem|reality|secret|lesson|takeaway|irony"
    r"|punchline|verdict|upshot|bottom\s+line|outcome|fix|difference|trick|key|point|shift"
    r"|payoff|news|part|question|story|reason|rub|deal|thing|short\s+version|real\s+story)"
)
_REVEAL_ADJ: Final = (
    r"(?:(?:real|actual|simple|short|hard|honest|uncomfortable|brutal|surprising|best|worst"
    r"|good|bad|big|biggest|only|main|ugly|plain|crazy|wild|funny|sad|ironic)\s+)?"
)
_REVEALS: Final = (
    re.compile(
        rf"^(?:(?:and|but|so)\s+)?(?:here(?:'s|\s+is)\s+)?the\s+{_REVEAL_ADJ}{_REVEAL_NOUN}"
        r"\s*(?::|\?|\u2014|\u2013)\s*\S",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:(?:and|but|so)\s+)?here(?:'s|\s+is)\s+(?:the\s+(?:thing|kicker|catch|twist|deal"
        r"|truth|problem|reality|secret|point|takeaway|lesson|irony|rub)\b|(?:what|why|how|where"
        r"|when|who)\b(?:\s+[\w'-]+){0,2}(?=\s*:))\s*[:.]",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:spoiler(?:\s+alert)?|plot\s+twist|translation|one\s+word|long\s+story\s+short"
        r"|in\s+short|bottom\s+line|lesson\s+learned|pro\s+tip|hot\s+take|real\s+talk"
        r"|truth\s+bomb)\s*(?::|\u2014|\u2013)\s*\S",
        re.IGNORECASE,
    ),
)

# -- formulaic closers ------------------------------------------------------------------------
_CLOSER_START: Final = re.compile(
    r"^(?:in\s+conclusion|to\s+conclude|in\s+summary|to\s+summari[sz]e|to\s+sum\s+(?:it\s+)?up"
    r"|in\s+closing|all\s+in\s+all|at\s+the\s+end\s+of\s+the\s+day|ultimately|the\s+bottom\s+line"
    r"|final\s+thoughts?|key\s+takeaways?|wrapping\s+up|in\s+the\s+end|moving\s+forward"
    r"|going\s+forward|the\s+road\s+ahead)\b",
    re.IGNORECASE,
)
_CLOSER_ANY: Final = re.compile(
    r"\b(?:let\s+that\s+sink\s+in|food\s+for\s+thought|only\s+time\s+will\s+tell"
    r"|the\s+(?:choice|decision)\s+is\s+yours|the\s+ball\s+is\s+in\s+your\s+court"
    r"|the\s+future\s+(?:is\s+(?:here|now|bright|yours)|belongs\s+to)"
    r"|the\s+question\s+is\s+(?:no\s+longer|not)\s+(?:whether|if)"
    r"|it's\s+not\s+a\s+question\s+of\s+if|the\s+rest\s+is\s+history"
    r"|makes\s+all\s+the\s+difference|now\s+it's\s+your\s+turn|over\s+to\s+you|your\s+move"
    r"|what\s+do\s+you\s+think\?|what's\s+your\s+take\?|what\s+are\s+your\s+thoughts\?"
    r"|thoughts\?|agree\?|are\s+you\s+ready\?|the\s+time\s+(?:to\s+act\s+)?is\s+now"
    r"|stay\s+tuned|buckle\s+up|the\s+journey\s+(?:is\s+just\s+beginning|continues))",
    re.IGNORECASE,
)

# -- AI tells (the exact linkedin.lint recipe) -------------------------------------------------
_AI_TELL_RES: Final = tuple(
    regex.compile(rf"(?i)(?<![\p{{L}}\p{{N}}]){pattern}(?![\p{{L}}\p{{N}}])")
    for pattern in AI_TELLS
)
_TLDR_RE: Final = regex.compile(r"(?i)^\s*tl;?dr\b")

# -- specifics --------------------------------------------------------------------------------
_GENERIC_ACRONYMS: Final = frozenset({"AI", "IT", "OK", "FAQ", "TLDR", "LLM", "LLMS", "ML"})
_NUMBER_WORDS: Final = frozenset(
    {
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "million",
        "billion",
        "dozen",
    }
)
_IDENTIFIER_RE: Final = re.compile(r"[\w-]{2,}(?:[./@:][\w-]{2,})+|\w+_\w+")
_HASHTAG_LINE_RE: Final = re.compile(r"(?:\s*#\w+)+\s*")

_READ_BLOCKS: Final = frozenset({"paragraph", "list_item", "callout"})
_PARAGRAPH_BLOCKS: Final = frozenset({"paragraph", "callout"})


@dataclass(frozen=True, slots=True)
class TextMeasure:
    """Word and specifics counts of a prose view (gate 4 budgets use them)."""

    words: int
    specifics: int

    @property
    def specifics_per_100_words(self) -> float:
        return 100.0 * self.specifics / self.words if self.words else 0.0


class SlopDetectors:
    """The slop detectors. Satisfies :class:`blogwriter.domain.ports.SlopChecker`."""

    def __init__(
        self,
        *,
        plain: SlopThresholds = PLAIN_THRESHOLDS,
        article: SlopThresholds = ARTICLE_THRESHOLDS,
    ) -> None:
        self._plain = plain
        self._article = article

    def check(self, text: str) -> list[Finding]:
        """Findings for plain text (posts, voice samples, comments), spans into ``text``."""
        return self.check_view(plain_view(text), self._plain)

    def check_markdown(self, body: str) -> list[Finding]:
        """Findings for an article-dialect body, spans into ``body``."""
        return self.check_view(markdown_view(body), self._article)

    def check_view(self, view: TextView, thresholds: SlopThresholds | None = None) -> list[Finding]:
        """Findings for a prepared view (gates reuse the article view)."""
        return self.analyze(view, thresholds)[0]

    def analyze(
        self, view: TextView, thresholds: SlopThresholds | None = None
    ) -> tuple[list[Finding], TextMeasure]:
        """Findings and the word and specifics counts, from one pass over ``view``."""
        limits = thresholds if thresholds is not None else self._article
        text = view.text.translate(_APOSTROPHES)  # same length: offsets stay valid
        analysis = _Analysis(view, text)
        findings: list[Finding] = []
        findings.extend(_isnt_x_its_y(analysis))
        findings.extend(_rule_of_three(analysis, limits))
        findings.extend(_questions_and_reveals(analysis, limits))
        findings.extend(_formulaic_closers(analysis))
        findings.extend(_one_sentence_runs(analysis, limits))
        findings.extend(_flat_rhythm(analysis, limits))
        findings.extend(_low_specificity(analysis, limits))
        findings.extend(_ai_tells(analysis))
        counts = TextMeasure(words=analysis.word_total, specifics=analysis.specific_total)
        return sorted(_dedupe(findings), key=_position), counts


def measure(view: TextView) -> TextMeasure:
    """Words and specifics of a view."""
    analysis = _Analysis(view, view.text.translate(_APOSTROPHES))
    return TextMeasure(words=analysis.word_total, specifics=analysis.specific_total)


# --------------------------------------------------------------------------------------------
# Shared analysis
# --------------------------------------------------------------------------------------------


class _Analysis:
    """Sentences per block and the specifics counts, computed once per check."""

    def __init__(self, view: TextView, text: str) -> None:
        self.view = view
        self.text = text
        self.blocks: list[Block] = [
            block
            if not (
                block.kind != "other" and _HASHTAG_LINE_RE.fullmatch(text[block.start : block.end])
            )
            else Block("other", block.start, block.start)
            for block in view.blocks
        ]
        self.sentences: list[list[Sentence]] = [
            []
            if block.kind == "other"
            else sentences(text[block.start : block.end], offset=block.start)
            for block in self.blocks
        ]
        self.block_words: list[int] = [sum(s.words for s in found) for found in self.sentences]
        self.word_total = sum(
            count
            for block, count in zip(self.blocks, self.block_words, strict=True)
            if block.kind != "other"
        )
        self.block_specifics = self._count_specifics()
        self.specific_total = sum(self.block_specifics)

    def prose(self, kinds: frozenset[str]) -> Iterable[tuple[int, Block]]:
        return ((i, block) for i, block in enumerate(self.blocks) if block.kind in kinds)

    def _count_specifics(self) -> list[int]:
        text = self.text
        known_proper = {
            word.text.lower()
            for block, found in zip(self.blocks, self.sentences, strict=True)
            if block.kind not in ("heading", "other")
            for sentence in found
            for word in words(text[sentence.start : sentence.end], offset=sentence.start)[1:]
            if _capitalized(word.text)
        }
        code_starts = sorted(start for start, _ in self.view.code_spans)
        counts: list[int] = []
        for block, found in zip(self.blocks, self.sentences, strict=True):
            if block.kind == "other":
                counts.append(0)
                continue
            # Inline code is masked out of the prose view: each span counts once, by position.
            total = bisect.bisect_left(code_starts, block.end) - bisect.bisect_left(
                code_starts, block.start
            )
            for sentence in found:
                previous_proper = False
                sentence_words = words(text[sentence.start : sentence.end], offset=sentence.start)
                for index, word in enumerate(sentence_words):
                    if _specific_token(word.text):
                        if not (previous_proper and word.text.isalpha()):
                            total += 1  # "Microsoft Entra ID" is one name, not two specifics
                        previous_proper = word.text.isalpha()
                        continue
                    proper = block.kind != "heading" and _capitalized(word.text)
                    if proper and index == 0:
                        following = sentence_words[1].text if len(sentence_words) > 1 else ""
                        proper = word.text.lower() in known_proper or _capitalized(following)
                    if proper and not previous_proper:
                        total += 1
                    previous_proper = proper
            counts.append(total)
        return counts


def _capitalized(token: str) -> bool:
    core = token.strip("'")
    if len(core) < 2 or core.startswith(("I'", "I\u2019")):
        return False
    return core[0].isupper() and any(char.islower() for char in core[1:])


def _specific_token(token: str) -> bool:
    core = token.strip("'")
    if any(char.isdigit() for char in core):
        return True
    if _IDENTIFIER_RE.fullmatch(core):
        return True
    letters = [char for char in core if char.isalpha()]
    if len(letters) >= 2 and all(char.isupper() for char in letters):
        return core.upper() not in _GENERIC_ACRONYMS
    if sum(char.isupper() for char in core) >= 2:
        return True  # CamelCase: OAuth, GitHub, PowerShell, macOS
    return core.lower() in _NUMBER_WORDS


def _finding(code: str, start: int | None = None, end: int | None = None) -> Finding:
    return Finding(
        code=code,
        severity="warn",
        message=_MESSAGES[code],
        start=start,
        end=end,
        overridable=True,
    )


def _position(finding: Finding) -> tuple[int, int, str]:
    start = finding.start if finding.start is not None else -1
    end = finding.end if finding.end is not None else -1
    return start, end, finding.code


def _dedupe(findings: Sequence[Finding]) -> list[Finding]:
    seen: set[tuple[str, int | None, int | None]] = set()
    out: list[Finding] = []
    for finding in findings:
        key = (finding.code, finding.start, finding.end)
        if key not in seen:
            seen.add(key)
            out.append(finding)
    return out


# --------------------------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------------------------


def _isnt_x_its_y(analysis: _Analysis) -> list[Finding]:
    spans: list[tuple[int, int]] = []
    for index, block in analysis.prose(_READ_BLOCKS):
        segment = analysis.text[block.start : block.end]
        for pattern in _SAME_SENTENCE:
            for match in pattern.finditer(segment):
                spans.append((block.start + match.start(), block.start + match.end()))
        found = analysis.sentences[index]
        for first, second in itertools.pairwise(found):
            if (
                first.words <= _PAIR_MAX_WORDS
                and second.words <= _PAIR_MAX_WORDS
                and _FIRST_OF_PAIR.search(first.text)
                and _SECOND_OF_PAIR.search(second.text)
            ):
                spans.append((first.start, second.end))
    return [_finding(ISNT_X_ITS_Y, start, end) for start, end in _merge(spans)]


def _merge(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _rule_of_three(analysis: _Analysis, limits: SlopThresholds) -> list[Finding]:
    triads: list[tuple[int, int]] = []
    for index, block in analysis.prose(_READ_BLOCKS):
        segment = analysis.text[block.start : block.end]
        for match in _TRIAD.finditer(segment):
            before = segment[: match.start()].rstrip()
            if before.endswith(","):
                continue  # part of a longer list
            items = [match.group(1), match.group(2), match.group(3)]
            if any(item.split()[0].lower() in _CONJUNCTIONS for item in items):
                continue
            if (
                sum(
                    _item_is_specific(item, position=match.start(n + 1), segment=segment)
                    for n, item in enumerate(items)
                )
                >= 2
            ):
                continue  # names, numbers or code: specifics, not a formula
            triads.append((block.start + match.start(), block.start + match.end()))
        found = analysis.sentences[index]
        run: list[Sentence] = []
        for sentence in [*found, None]:
            if sentence is not None and 0 < sentence.words <= 3:
                run.append(sentence)
                continue
            if len(run) == 3:
                triads.append((run[0].start, run[-1].end))
            run = []
    words_total = max(analysis.word_total, 1)
    density = 1000.0 * len(triads) / words_total
    if len(triads) < limits.triad_min or density <= limits.triads_per_1000_words:
        return []
    return [_finding(RULE_OF_THREE, start, end) for start, end in _merge(triads)]


def _item_is_specific(item: str, *, position: int, segment: str) -> bool:
    tokens = item.split()
    for offset, token in enumerate(tokens):
        if _specific_token(token):
            return True
        sentence_start = offset == 0 and _at_sentence_start(segment, position)
        if _capitalized(token) and not sentence_start:
            return True
    return False


def _at_sentence_start(segment: str, position: int) -> bool:
    before = segment[:position].rstrip()
    return not before or before[-1] in ".!?:"


def _questions_and_reveals(analysis: _Analysis, limits: SlopThresholds) -> list[Finding]:
    findings: list[Finding] = []
    last_prose = max(
        (index for index, block in enumerate(analysis.blocks) if block.kind != "other"),
        default=-1,
    )
    for index, _ in analysis.prose(_PARAGRAPH_BLOCKS):
        found = analysis.sentences[index]
        later_prose = index < last_prose
        for position, sentence in enumerate(found):
            followed = position + 1 < len(found)
            question = sentence.text.endswith("?") and not sentence.text.startswith(('"', "'", "“"))
            if question and sentence.words <= limits.reveal_question_max_words and followed:
                findings.append(_finding(COLON_REVEAL, sentence.start, sentence.end))
                continue
            if (
                question
                and position == 0
                and sentence.words <= limits.opener_max_words
                and (followed or later_prose)
            ):
                findings.append(_finding(RHETORICAL_OPENER, sentence.start, sentence.end))
                continue
            if any(pattern.search(sentence.text) for pattern in _REVEALS):
                findings.append(_finding(COLON_REVEAL, sentence.start, sentence.end))
    return findings


def _formulaic_closers(analysis: _Analysis) -> list[Finding]:
    closing = [index for index, _ in analysis.prose(_PARAGRAPH_BLOCKS)][-2:]
    findings: list[Finding] = []
    for index in closing:
        for sentence in analysis.sentences[index]:
            if _CLOSER_START.search(sentence.text) or _CLOSER_ANY.search(sentence.text):
                findings.append(_finding(FORMULAIC_CLOSER, sentence.start, sentence.end))
    return findings


def _one_sentence_runs(analysis: _Analysis, limits: SlopThresholds) -> list[Finding]:
    findings: list[Finding] = []
    run: list[int] = []

    def close() -> None:
        if len(run) >= limits.one_sentence_run:
            first = analysis.sentences[run[0]][0]
            last = analysis.sentences[run[-1]][-1]
            findings.append(_finding(ONE_SENTENCE_RUN, first.start, last.end))
        run.clear()

    for index, block in enumerate(analysis.blocks):
        if block.kind == "paragraph" and len(analysis.sentences[index]) == 1:
            run.append(index)
        else:
            close()
    close()
    return findings


def _flat_rhythm(analysis: _Analysis, limits: SlopThresholds) -> list[Finding]:
    found = [
        sentence
        for index, _ in analysis.prose(_PARAGRAPH_BLOCKS)
        for sentence in analysis.sentences[index]
    ]
    if len(found) < limits.rhythm_min_sentences:
        return []
    window = min(limits.rhythm_window, len(found))
    starts = list(range(0, len(found) - window + 1, max(1, limits.rhythm_step)))
    if starts[-1] != len(found) - window:
        starts.append(len(found) - window)
    flagged: list[tuple[int, int]] = []
    for first in starts:
        lengths = [sentence.words for sentence in found[first : first + window]]
        mean = statistics.fmean(lengths)
        if mean <= 0:
            continue
        if statistics.pstdev(lengths) / mean < limits.rhythm_min_cv:
            flagged.append((found[first].start, found[first + window - 1].end))
    return [_finding(FLAT_RHYTHM, start, end) for start, end in _merge_touching(flagged)]


def _merge_touching(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _low_specificity(analysis: _Analysis, limits: SlopThresholds) -> list[Finding]:
    total_words = analysis.word_total
    if total_words < limits.specifics_min_words:
        return []
    if 100.0 * analysis.specific_total / total_words >= limits.specifics_per_100_words:
        return []
    vague = [
        _finding(LOW_SPECIFICITY, block.start, block.end)
        for index, block in analysis.prose(_PARAGRAPH_BLOCKS)
        if analysis.block_words[index] >= limits.vague_paragraph_words
        and analysis.block_specifics[index] == 0
    ]
    return vague or [_finding(LOW_SPECIFICITY)]


def _ai_tells(analysis: _Analysis) -> list[Finding]:
    quotes = [block for block in analysis.blocks if block.kind == "quote"]
    findings: list[Finding] = []
    for pattern in _AI_TELL_RES:
        for match in pattern.finditer(analysis.text):
            if any(block.start <= match.start() < block.end for block in quotes):
                continue
            findings.append(
                Finding(
                    code=AI_TELL,
                    severity="warn",
                    message=f"AI tell: {match.group(0)!r}.",
                    start=match.start(),
                    end=match.end(),
                    overridable=True,
                )
            )
    if _TLDR_RE.match(analysis.text):
        findings.append(
            Finding(
                code=AI_TELL,
                severity="warn",
                message="Starts with TL;DR.",
                start=0,
                end=min(5, len(analysis.text)),
                overridable=True,
            )
        )
    return findings
