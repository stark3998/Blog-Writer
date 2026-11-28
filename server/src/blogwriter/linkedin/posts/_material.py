"""What a post is made from: the article, brief, quick search, stories, POVs, idea, owner take
and (repurposing) the earlier post, loaded once and rendered as delimited data for a model.

Two rules hold for everything rendered here:

- **No links.** URLs never reach the drafter or the checker: Markdown links keep their text,
  bare URLs, autolinks and footnote definitions are dropped, and citations are given by id,
  publisher and title only. A drafter that never sees a URL cannot copy one (the "default
  prompts produce 0 links" invariant).
- **Data, not instructions.** Every text sits inside a tagged section; ``<`` and ``>`` inside
  the text are replaced by look-alikes so no text can close its section.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal
from urllib.parse import urlsplit

import regex

from blogwriter.db import NotFound, Store
from blogwriter.domain.articles import Article
from blogwriter.domain.common import Citation
from blogwriter.domain.docstore import read_doc
from blogwriter.domain.posts import Post
from blogwriter.domain.research import Brief, QuickSearch
from blogwriter.domain.signals import Idea
from blogwriter.domain.stories import PovEntry, StoryEntry
from blogwriter.linkedin.posts._post import owner_take
from blogwriter.stories import load_bank

__all__ = [
    "ARTICLE_MAX_CHARS",
    "SourceItem",
    "Sources",
    "citation_publisher",
    "data",
    "load_sources",
    "neutral",
    "pov_text",
    "render_sources",
    "story_text",
    "strip_links",
]

ARTICLE_MAX_CHARS: Final = 60_000
"""The article body a model sees (a flagship is 900-3,000 words; this bounds the tokens)."""

type SourceKind = Literal[
    "article",
    "brief",
    "quick_search",
    "citation",
    "story",
    "pov",
    "interview",
    "take",
    "idea",
    "source_post",
]

_FOOTNOTE_DEF: Final = regex.compile(r"(?m)^[ \t]*\[\^[^\]\n]+\]:.*(?:\n(?:[ \t]+\S.*))*$")
_REFERENCE_DEF: Final = regex.compile(r"(?m)^[ \t]{0,3}\[[^\]\n]+\]:[ \t]*\S+.*$")
_IMAGE: Final = regex.compile(r"!\[([^\]\n]*)\]\((?:[^()\s]|\([^()\s]*\))*(?:\s+\"[^\"]*\")?\)")
_LINK: Final = regex.compile(r"\[([^\]\n]+)\]\((?:[^()\s]|\([^()\s]*\))*(?:\s+\"[^\"]*\")?\)")
_AUTOLINK: Final = regex.compile(r"<(?:https?|mailto|ftp):[^>\s]*>", regex.IGNORECASE)
_BARE_URL: Final = regex.compile(r"(?i)\b(?:https?://|ftp://|www\.)[^\s<>()\[\]{}]+")
_FOOTNOTE_REF: Final = regex.compile(r"\[\^[^\]\n]+\]")
_SPACES: Final = regex.compile(r"[ \t]{2,}")


def strip_links(text: str) -> str:
    """``text`` (Markdown or plain) without any URL: link text kept, URLs and footnote or
    reference definitions dropped."""
    out = _FOOTNOTE_DEF.sub("", text)
    out = _REFERENCE_DEF.sub("", out)
    out = _IMAGE.sub(lambda m: m.group(1), out)
    out = _LINK.sub(lambda m: m.group(1), out)
    out = _AUTOLINK.sub("", out)
    out = _BARE_URL.sub("", out)
    out = _FOOTNOTE_REF.sub("", out)
    return _SPACES.sub(" ", out).strip()


def neutral(text: str) -> str:
    """``text`` with ``<`` and ``>`` replaced by look-alikes, so it cannot open or close a
    tagged section of a prompt."""
    return text.replace("<", "\u2039").replace(">", "\u203a")


def data(text: str) -> str:
    """Text made safe to sit inside a tagged data section (no links, no tags)."""
    return neutral(strip_links(text))


def _attribute(text: str) -> str:
    return data(text).replace('"', "'").replace("\n", " ")


def citation_publisher(citation: Citation) -> str:
    """The publisher to name in an inline attribution (never the domain: LinkedIn would link
    it). Falls back to the registrable label of the domain, capitalized (``Microsoft``)."""
    if citation.publisher and citation.publisher.strip():
        return citation.publisher.strip()[:100]
    host = urlsplit(citation.url).hostname or citation.domain
    labels = [label for label in host.split(".") if label and label != "www"]
    core = labels[-2] if len(labels) >= 2 else (labels[0] if labels else "source")
    return core[:1].upper() + core[1:]


@dataclass(frozen=True, slots=True)
class SourceItem:
    """One checkable piece of material: ``ref`` is how a model names it (``story:<id>``,
    ``brief:claim-2``, ``article``)."""

    ref: str
    kind: SourceKind
    text: str
    citation_id: str | None = None


@dataclass(slots=True)
class Sources:
    """Everything a post draws on. ``missing`` names references that could not be read and
    ``unusable`` stories or POVs that are not approved with a current passing classifier
    verdict (generation refuses both; the derived-claims check fails closed on ``missing``)."""

    items: list[SourceItem] = field(default_factory=list[SourceItem])
    citations: dict[str, Citation] = field(default_factory=dict[str, Citation])
    missing: list[str] = field(default_factory=list[str])
    unusable: list[str] = field(default_factory=list[str])
    article: Article | None = None
    brief: Brief | None = None
    quick_search: QuickSearch | None = None
    idea: Idea | None = None
    source_post: Post | None = None
    stories: list[StoryEntry] = field(default_factory=list[StoryEntry])
    povs: list[PovEntry] = field(default_factory=list[PovEntry])

    @property
    def owner_texts(self) -> list[str]:
        """The writer's own words (stories, POVs, interview quotes, the typed take)."""
        return [i.text for i in self.items if i.kind in ("story", "pov", "interview", "take")]

    @property
    def texts(self) -> list[str]:
        return [item.text for item in self.items]

    def add(
        self, ref: str, kind: SourceKind, text: str | None, citation_id: str | None = None
    ) -> None:
        if text is not None and text.strip():
            self.items.append(
                SourceItem(ref=ref, kind=kind, text=text.strip(), citation_id=citation_id)
            )


def story_text(story: StoryEntry) -> str:
    """A story as material: its fields as labelled lines (metrics as written, no source URLs)."""
    metrics = "; ".join(f"{metric.label}: {metric.value}" for metric in story.metrics)
    sections = [
        ("Title", story.title),
        ("Problem", story.problem),
        ("Actions", story.actions),
        ("Decisions and trade-offs", story.decisions),
        ("Metrics", metrics),
        ("Lessons", story.lessons),
    ]
    return "\n".join(f"{label}: {value}" for label, value in sections if value.strip())


def pov_text(pov: PovEntry) -> str:
    """A POV entry as material: the statement and its context."""
    sections = [("Position", pov.statement), ("Context", pov.context or "")]
    return "\n".join(f"{label}: {value}" for label, value in sections if value.strip())


def _unique(ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(ids))


async def _read[D: (Article, Brief, QuickSearch, Idea, Post, StoryEntry, PovEntry)](
    store: Store, cls: type[D], doc_id: str, sources: Sources, ref: str
) -> D | None:
    try:
        return (await read_doc(store, cls, doc_id)).doc
    except NotFound:
        sources.missing.append(ref)
        return None


def _add_brief(sources: Sources, brief: Brief) -> None:
    sources.add(f"brief:{brief.id}:summary", "brief", brief.executive_summary)
    for claim in brief.claims:
        sources.add(f"brief:{claim.id}", "brief", claim.text, claim.citation_id)
    for stat in brief.stats:
        citation = stat.citation_ids[0] if stat.citation_ids else None
        text = f"{stat.value}: {stat.label}" + (f" (as of {stat.as_of})" if stat.as_of else "")
        sources.add(f"brief:{stat.id}", "brief", text, citation)
    for finding in brief.findings:
        citation = finding.citation_ids[0] if finding.citation_ids else None
        sources.add(f"brief:{finding.id}", "brief", finding.text, citation)
    for index, counterpoint in enumerate(brief.counterpoints):
        sources.add(f"brief:counterpoint-{index + 1}", "brief", counterpoint)
    for citation in brief.citations:
        sources.citations.setdefault(citation.id, citation)


def _add_quick_search(sources: Sources, search: QuickSearch) -> None:
    sources.add(f"quick_search:{search.id}:answer", "quick_search", search.answer)
    for claim in search.claims:
        sources.add(f"quick_search:{claim.id}", "quick_search", claim.text, claim.citation_id)
    for citation in search.citations:
        sources.citations.setdefault(citation.id, citation)


async def load_sources(store: Store, post: Post, *, usable_only: bool = False) -> Sources:
    """Load what ``post`` draws on: its article (with the article's brief and owner signals),
    brief, quick search, idea (and a repurposed post), stories, POVs and the typed take."""
    sources = Sources()
    story_ids = list(post.story_ids)
    pov_ids = list(post.pov_ids)
    brief_id = post.brief_id
    if post.article_id is not None:
        article = await _read(store, Article, post.article_id, sources, "article")
        if article is not None:
            sources.article = article
            meta = article.meta
            header = "\n".join(
                part for part in (meta.title, meta.excerpt) if part is not None and part.strip()
            )
            body = strip_links(article.markdown)[:ARTICLE_MAX_CHARS]
            sources.add("article", "article", f"{header}\n\n{body}" if header else body)
            brief_id = brief_id or article.brief_id
            for signal in article.owner_signals:
                if signal.kind == "story":
                    story_ids.append(signal.id)
                elif signal.kind == "pov":
                    pov_ids.append(signal.id)
                elif signal.quote:
                    sources.add(f"interview:{signal.id}", "interview", signal.quote)
    if brief_id is not None:
        brief = await _read(store, Brief, brief_id, sources, "brief")
        if brief is not None:
            sources.brief = brief
            _add_brief(sources, brief)
    if post.quick_search_id is not None:
        search = await _read(store, QuickSearch, post.quick_search_id, sources, "quick_search")
        if search is not None:
            sources.quick_search = search
            _add_quick_search(sources, search)
    if post.idea_id is not None:
        idea = await _read(store, Idea, post.idea_id, sources, "idea")
        if idea is not None:
            sources.idea = idea
            sources.add(
                f"idea:{idea.id}",
                "idea",
                "\n".join(part for part in (idea.title, idea.note, idea.angle) if part),
            )
            if idea.repurpose_of is not None:
                earlier = await _read(store, Post, idea.repurpose_of, sources, "source_post")
                if earlier is not None:
                    sources.source_post = earlier
                    sources.add(f"post:{earlier.id}", "source_post", earlier.display_text)
                    story_ids.extend(earlier.story_ids)
                    pov_ids.extend(earlier.pov_ids)
    take = owner_take(post)
    if take is not None:
        sources.add("take", "take", take)
    await _add_bank(store, sources, _unique(story_ids), _unique(pov_ids), usable_only=usable_only)
    for citation in sources.citations.values():
        sources.add(
            f"citation:{citation.id}",
            "citation",
            f"{citation_publisher(citation)}: {citation.title}",
            citation.id,
        )
    return sources


async def _add_bank(
    store: Store,
    sources: Sources,
    story_ids: Sequence[str],
    pov_ids: Sequence[str],
    *,
    usable_only: bool,
) -> None:
    if not story_ids and not pov_ids:
        return
    usable_stories: dict[str, StoryEntry] = {}
    usable_povs: dict[str, PovEntry] = {}
    if usable_only:
        bank = await load_bank(store, usable_only=True)
        usable_stories = {story.id: story for story in bank.stories}
        usable_povs = {pov.id: pov for pov in bank.povs}
    for story_id in story_ids:
        story = await _read(store, StoryEntry, story_id, sources, f"story:{story_id}")
        if story is None:
            continue
        if usable_only and story_id not in usable_stories:
            sources.unusable.append(f"story:{story_id}")
            continue
        sources.stories.append(story)
        sources.add(f"story:{story.id}", "story", story_text(story))
    for pov_id in pov_ids:
        pov = await _read(store, PovEntry, pov_id, sources, f"pov:{pov_id}")
        if pov is None:
            continue
        if usable_only and pov_id not in usable_povs:
            sources.unusable.append(f"pov:{pov_id}")
            continue
        sources.povs.append(pov)
        sources.add(f"pov:{pov.id}", "pov", pov_text(pov))


_TAGS: Final[dict[SourceKind, str]] = {
    "article": "article",
    "brief": "research",
    "quick_search": "research",
    "citation": "citation",
    "story": "story",
    "pov": "pov",
    "interview": "interview_quote",
    "take": "owner_take",
    "idea": "idea",
    "source_post": "source_post",
}


def render_sources(sources: Sources, *, kinds: Iterable[SourceKind] | None = None) -> str:
    """The items as tagged data sections (``<story ref="story:...">...</story>``), citations by
    id, publisher and title (never a URL)."""
    wanted = set(kinds) if kinds is not None else None
    blocks: list[str] = []
    for item in sources.items:
        if wanted is not None and item.kind not in wanted:
            continue
        tag = _TAGS[item.kind]
        attributes = f' ref="{_attribute(item.ref)}"'
        if item.citation_id is not None:
            attributes += f' citation="{_attribute(item.citation_id)}"'
        if item.kind == "citation" and item.citation_id is not None:
            citation = sources.citations.get(item.citation_id)
            if citation is not None:
                blocks.append(
                    f'<citation id="{_attribute(citation.id)}" '
                    f'publisher="{_attribute(citation_publisher(citation))}" '
                    f'title="{_attribute(citation.title)}"'
                    + (f' date="{_attribute(citation.date)}"' if citation.date else "")
                    + " />"
                )
            continue
        blocks.append(f"<{tag}{attributes}>\n{data(item.text)}\n</{tag}>")
    return "\n\n".join(blocks)
