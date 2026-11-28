"""blogwriter.articles: articles from plan to publication (contract W-A7).

Create, autosave with If-Match plus ``baseVersion`` (content conflicts only), versions (keep the
newest ``BW_ARTICLES_KEEP_VERSIONS`` plus the pinned ones), the article status machine and the
who-writes rule, the portfolio-like preview, the Markdown export and the clearance attestation.

Python API (wave B and the owner routes)::

    from blogwriter.articles import (
        create_article, owner_save, pipeline_write, set_links, transition, ArticleLinks,
        SavedArticle, StaleArticle, compose_document, word_stats,
    )

AI illustration candidates (:mod:`.illustrations`): the illustrations job attaches them
(:func:`attach_illustrations`, never content); the owner makes one the hero
(:func:`choose_hero`, :func:`clear_hero`), inserts one after a heading
(:func:`insert_illustration`, a versioned body change), edits its alt text
(:func:`edit_illustration_alt`) or discards it (:func:`discard_illustration`).

``Article.markdown`` is the article-dialect body and ``Article.meta`` its frontmatter; the
canonical document is ``compose_document(meta, body)`` (re-exported from
``blogwriter.domain.compose``, like ``word_stats``). Wiring: ``get_router()`` only (no jobs,
schedule entries, lifecycle or AI).
"""

from blogwriter.articles.content import CASE_STUDY_FORMAT, MarkdownTooLarge
from blogwriter.articles.illustrations import (
    HeadingNotFound,
    IllustrationInserted,
    IllustrationInUse,
    IllustrationNotFound,
    InvalidAltText,
    MediaFull,
    attach_illustrations,
    choose_hero,
    clear_hero,
    discard_illustration,
    edit_illustration_alt,
    image_markdown,
    insert_illustration,
    link_illustration_job,
    markdown_headings,
)
from blogwriter.articles.routes import (
    DEFAULT_LIST_LIMIT,
    OWNER_TRANSITIONS,
    article_problems,
    get_router,
)
from blogwriter.articles.service import (
    Actor,
    ArticleLinks,
    SavedArticle,
    StaleArticle,
    attest_clearance,
    create_article,
    owner_save,
    pipeline_write,
    restore_version,
    set_links,
    transition,
)
from blogwriter.articles.settings import ArticlesSettings, get_settings
from blogwriter.domain.compose import compose_document, word_stats

__all__ = [
    "CASE_STUDY_FORMAT",
    "DEFAULT_LIST_LIMIT",
    "OWNER_TRANSITIONS",
    "Actor",
    "ArticleLinks",
    "ArticlesSettings",
    "HeadingNotFound",
    "IllustrationInUse",
    "IllustrationInserted",
    "IllustrationNotFound",
    "InvalidAltText",
    "MarkdownTooLarge",
    "MediaFull",
    "SavedArticle",
    "StaleArticle",
    "article_problems",
    "attach_illustrations",
    "attest_clearance",
    "choose_hero",
    "clear_hero",
    "compose_document",
    "create_article",
    "discard_illustration",
    "edit_illustration_alt",
    "get_router",
    "get_settings",
    "image_markdown",
    "insert_illustration",
    "link_illustration_job",
    "markdown_headings",
    "owner_save",
    "pipeline_write",
    "restore_version",
    "set_links",
    "transition",
    "word_stats",
]
