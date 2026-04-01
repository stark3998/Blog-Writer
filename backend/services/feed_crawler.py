"""Feed Crawler Service — Discover feeds, parse articles, and orchestrate crawls."""

import hashlib
import logging
import re
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup, Tag

from backend.db.cosmos_client import (
    create_crawl_job,
    get_crawled_article,
    get_feed_source,
    list_crawled_articles,
    list_failed_crawled_articles,
    update_crawl_job,
    update_feed_source,
    upsert_crawled_article,
)
from backend.services.relevance_classifier import classify_article
from backend.services.auto_publisher import process_relevant_article, publish_best_linkedin_post
from backend.services.notification_service import notify

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

COMMON_FEED_PATHS = [
    "/feed", "/feed/", "/rss", "/rss/", "/atom.xml",
    "/feed.xml", "/rss.xml", "/index.xml", "/feeds/posts/default",
    "/blog/feed", "/blog/rss",
]


def _article_id(url: str) -> str:
    """Generate a deterministic ID from an article URL."""
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()[:32]


def discover_feed(base_url: str) -> dict[str, Any]:
    """Discover RSS/Atom feed for a given blog URL.

    Returns dict with keys: feedUrl, feedType ('rss' or 'html'), siteName.
    """
    base_url = base_url.strip().rstrip("/")
    logger.info(f"Discovering feed for: {base_url}")

    try:
        resp = requests.get(
            base_url, headers={"User-Agent": USER_AGENT}, timeout=15, allow_redirects=True
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(f"Failed to fetch base URL {base_url}: {exc}")
        return {"feedUrl": "", "feedType": "html", "siteName": base_url}

    soup = BeautifulSoup(resp.text, "html.parser")

    # Extract site name
    site_name = ""
    title_tag = soup.find("title")
    if title_tag:
        site_name = title_tag.get_text().strip()[:100]

    # Strategy 1: Look for <link> tags pointing to RSS/Atom feeds
    for link_tag in soup.find_all("link", attrs={"rel": "alternate"}):
        if not isinstance(link_tag, Tag):
            continue
        link_type = str(link_tag.get("type", "")).lower()
        if any(ft in link_type for ft in ["rss", "atom", "xml"]):
            href = str(link_tag.get("href", "")).strip()
            if href:
                feed_url = urljoin(base_url, href)
                logger.info(f"Found feed via <link> tag: {feed_url}")
                return {"feedUrl": feed_url, "feedType": "rss", "siteName": site_name}

    # Strategy 2: Try common feed paths
    for path in COMMON_FEED_PATHS:
        candidate = base_url + path
        try:
            probe = requests.get(
                candidate, headers={"User-Agent": USER_AGENT}, timeout=10,
                allow_redirects=True,
            )
            if probe.status_code == 200:
                content_type = probe.headers.get("content-type", "").lower()
                text_start = probe.text[:500].strip().lower()
                if any(
                    sig in content_type or sig in text_start
                    for sig in ["xml", "rss", "atom", "<feed", "<rss", "<?xml"]
                ):
                    logger.info(f"Found feed at common path: {candidate}")
                    return {"feedUrl": candidate, "feedType": "rss", "siteName": site_name}
        except requests.RequestException:
            continue

    # No feed found — fall back to HTML scraping
    logger.info(f"No RSS/Atom feed found for {base_url}, will use HTML scraping")
    return {"feedUrl": "", "feedType": "html", "siteName": site_name}


def parse_rss_feed(feed_url: str) -> list[dict[str, Any]]:
    """Parse an RSS/Atom feed and return a list of articles."""
    logger.info(f"Parsing RSS feed: {feed_url}")
    parsed = feedparser.parse(feed_url, agent=USER_AGENT)

    articles: list[dict[str, Any]] = []
    for entry in parsed.entries:
        url = getattr(entry, "link", "") or ""
        if not url:
            continue

        title = getattr(entry, "title", "") or ""
        summary = getattr(entry, "summary", "") or ""
        # Clean HTML from summary
        if summary and "<" in summary:
            summary = BeautifulSoup(summary, "html.parser").get_text()[:500]

        published = ""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
            except (TypeError, ValueError):
                pass
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            try:
                published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc).isoformat()
            except (TypeError, ValueError):
                pass

        articles.append({
            "url": url.strip(),
            "title": title.strip(),
            "summary": summary.strip(),
            "published": published,
        })

    logger.info(f"Parsed {len(articles)} articles from RSS feed")
    return articles


def scrape_blog_listing(base_url: str) -> list[dict[str, Any]]:
    """Scrape a blog listing page for article links (HTML fallback)."""
    logger.info(f"Scraping blog listing: {base_url}")

    try:
        resp = requests.get(
            base_url, headers={"User-Agent": USER_AGENT}, timeout=15, allow_redirects=True
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(f"Failed to scrape {base_url}: {exc}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    parsed_base = urlparse(base_url)
    articles: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    # Look for article links in common patterns
    candidates = (
        soup.find_all("article")
        or soup.find_all("div", class_=re.compile(r"(post|article|entry|blog)", re.I))
        or soup.find_all("a")
    )

    for element in candidates:
        if not isinstance(element, Tag):
            continue

        # Find the link
        if element.name == "a":
            anchor = element
        else:
            anchor = element.find("a")
            if not anchor or not isinstance(anchor, Tag):
                continue

        href = str(anchor.get("href", "")).strip()
        if not href or href == "#" or href.lower().startswith("javascript:"):
            continue

        resolved = urljoin(base_url, href)
        parsed_link = urlparse(resolved)

        # Only include links from the same domain
        if parsed_link.netloc != parsed_base.netloc:
            continue

        # Skip obvious non-article links
        skip_patterns = [
            "/tag/", "/category/", "/author/", "/page/",
            "/search", "/login", "/signup", "/about", "/contact",
            "#", "?", "/feed", "/rss",
        ]
        if any(pat in parsed_link.path.lower() for pat in skip_patterns):
            continue

        # Must have a path deeper than just "/"
        if len(parsed_link.path.strip("/")) < 3:
            continue

        if resolved in seen_urls:
            continue
        seen_urls.add(resolved)

        title = anchor.get_text().strip()[:200]
        if not title or len(title) < 5:
            # Try to find a heading nearby
            heading = element.find(["h1", "h2", "h3", "h4"])
            if heading:
                title = heading.get_text().strip()[:200]

        if not title or len(title) < 5:
            continue

        articles.append({
            "url": resolved,
            "title": title,
            "summary": "",
            "published": "",
        })

        if len(articles) >= 50:
            break

    logger.info(f"Scraped {len(articles)} article links from HTML")
    return articles


def _filter_by_age(articles: list[dict[str, Any]], max_age_days: int) -> list[dict[str, Any]]:
    """Filter articles to only those published within the last max_age_days.

    Articles with no published date are included (benefit of the doubt).
    """
    if max_age_days <= 0:
        return articles

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    filtered: list[dict[str, Any]] = []

    for article in articles:
        published = article.get("published", "")
        if not published:
            # No date available (e.g. HTML-scraped) — include it
            filtered.append(article)
            continue
        try:
            pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if pub_dt >= cutoff:
                filtered.append(article)
        except (ValueError, TypeError):
            # Unparseable date — include it
            filtered.append(article)

    logger.info(
        f"Age filter ({max_age_days}d): {len(articles)} -> {len(filtered)} articles"
    )
    return filtered


def _retry_failed_articles(source_id: str, source: dict[str, Any]) -> None:
    """Re-process articles that failed in previous crawls.

    Looks for articles with status 'error' and retryCount < 3 from the last 48 hours.
    """
    failed = list_failed_crawled_articles(source_id, max_retries=3, hours=48)
    if not failed:
        return

    logger.info(f"Retrying {len(failed)} previously failed articles for {source.get('name', source_id)}")
    for record in failed:
        retry_count = record.get("retryCount", 0) + 1
        article = {
            "url": record.get("articleUrl", ""),
            "title": record.get("title", ""),
            "summary": "",
            "published": "",
        }
        try:
            result = process_relevant_article(article, source)
            record["draftId"] = result.get("draft_id", "")
            record["status"] = result.get("status", "drafted")
            record["retryCount"] = retry_count
            record["lastError"] = ""
            logger.info(f"Retry succeeded for article: {article['title'][:60]}")
        except Exception as exc:
            record["status"] = "error"
            record["retryCount"] = retry_count
            record["lastError"] = str(exc)[:500]
            logger.warning(
                f"Retry {retry_count}/3 failed for article {article['url']}: {exc}"
            )
        upsert_crawled_article(record)


def crawl_feed_source(source_id: str) -> dict[str, Any]:
    """Crawl a feed source: fetch articles, classify, and auto-generate content.

    Returns crawl job summary.
    """
    source = get_feed_source(source_id)
    if not source:
        raise ValueError(f"Feed source not found: {source_id}")

    logger.info(f"Starting crawl for feed source: {source['name']} ({source_id})")

    # Re-process previously failed articles before starting new crawl
    _retry_failed_articles(source_id, source)

    job = create_crawl_job(source_id)

    try:
        # Fetch articles
        if source["feedType"] == "rss" and source.get("feedUrl"):
            articles = parse_rss_feed(source["feedUrl"])
        else:
            articles = scrape_blog_listing(source["baseUrl"])

        job_updates: dict[str, Any] = {"articlesFound": len(articles)}

        # Deduplicate against already-crawled articles
        new_articles: list[dict[str, Any]] = []
        for article in articles:
            aid = _article_id(article["url"])
            existing = get_crawled_article(aid)
            if not existing:
                new_articles.append(article)

        # Filter by article age
        max_age_days = source.get("maxArticleAgeDays", 7)
        new_articles = _filter_by_age(new_articles, max_age_days)

        relevant_count = 0
        processed_count = 0
        topics = source.get("topics", ["cloud security", "azure", "ai"])
        max_to_generate = source.get("maxArticlesToGenerate", 1)
        linkedin_candidates: list[dict[str, Any]] = []

        # Phase 1: Classify all new articles
        classified_articles: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for article in new_articles:
            classification = classify_article(
                title=article["title"],
                summary=article.get("summary", ""),
                content="",
                topics=topics,
            )
            classified_articles.append((article, classification))

        # Separate relevant from irrelevant, save irrelevant immediately
        relevant_articles: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for article, classification in classified_articles:
            aid = _article_id(article["url"])
            if not classification["is_relevant"]:
                crawled_record: dict[str, Any] = {
                    "id": aid,
                    "feedSourceId": source_id,
                    "articleUrl": article["url"],
                    "title": article["title"],
                    "isRelevant": False,
                    "relevanceScore": classification.get("relevance_score", 0),
                    "matchedTopics": classification.get("matched_topics", []),
                    "matchedKeywords": classification.get("matched_keywords", []),
                    "draftId": "",
                    "linkedinPostId": "",
                    "status": "skipped",
                    "crawledAt": datetime.now(timezone.utc).isoformat(),
                }
                upsert_crawled_article(crawled_record)
            else:
                relevant_count += 1
                relevant_articles.append((article, classification))

        # Phase 2: Rank relevant articles by technicality and pick top N
        if relevant_articles and max_to_generate > 0:
            from backend.services.auto_publisher import rank_articles_by_technicality

            ranked = rank_articles_by_technicality(
                [
                    {
                        "title": a["title"],
                        "summary": a.get("summary", ""),
                        "url": a["url"],
                        "relevance_score": c.get("relevance_score", 0),
                        "matched_topics": c.get("matched_topics", []),
                    }
                    for a, c in relevant_articles
                ],
                topics,
            )

            # Reorder relevant_articles by ranked order
            index_map = {item["url"]: rank for rank, item in enumerate(ranked)}
            relevant_articles.sort(key=lambda ac: index_map.get(ac[0]["url"], 999))
            top_articles = relevant_articles[:max_to_generate]
            skipped_articles = relevant_articles[max_to_generate:]
        else:
            top_articles = []
            skipped_articles = relevant_articles

        # Save skipped-by-rank articles as "skipped_rank"
        for article, classification in skipped_articles:
            aid = _article_id(article["url"])
            crawled_record = {
                "id": aid,
                "feedSourceId": source_id,
                "articleUrl": article["url"],
                "title": article["title"],
                "isRelevant": True,
                "relevanceScore": classification.get("relevance_score", 0),
                "matchedTopics": classification.get("matched_topics", []),
                "matchedKeywords": classification.get("matched_keywords", []),
                "draftId": "",
                "linkedinPostId": "",
                "status": "skipped_rank",
                "crawledAt": datetime.now(timezone.utc).isoformat(),
            }
            upsert_crawled_article(crawled_record)

        # Phase 3: Generate blogs for top N articles only
        for article, classification in top_articles:
            aid = _article_id(article["url"])
            crawled_record = {
                "id": aid,
                "feedSourceId": source_id,
                "articleUrl": article["url"],
                "title": article["title"],
                "isRelevant": True,
                "relevanceScore": classification.get("relevance_score", 0),
                "matchedTopics": classification.get("matched_topics", []),
                "matchedKeywords": classification.get("matched_keywords", []),
                "draftId": "",
                "linkedinPostId": "",
                "status": "pending",
                "crawledAt": datetime.now(timezone.utc).isoformat(),
            }
            try:
                result = process_relevant_article(article, source)
                crawled_record["draftId"] = result.get("draft_id", "")
                crawled_record["status"] = result.get("status", "drafted")
                processed_count += 1

                if result.get("linkedin_data"):
                    linkedin_candidates.append({
                        **result["linkedin_data"],
                        "crawled_article_id": aid,
                    })
            except Exception as exc:
                logger.error(f"Failed to process article {article['url']}: {exc}")
                crawled_record["status"] = "error"
                crawled_record["error"] = str(exc)[:500]
                crawled_record["lastError"] = str(exc)[:500]
                crawled_record["retryCount"] = 0

            upsert_crawled_article(crawled_record)

        # Phase 4: Select and publish the best LinkedIn post
        linkedin_result = None
        if linkedin_candidates:
            linkedin_result = publish_best_linkedin_post(linkedin_candidates, source)
            if linkedin_result and linkedin_result.get("post_id"):
                winner_aid = linkedin_candidates[linkedin_result["selected_index"]]["crawled_article_id"]
                winner_record = get_crawled_article(winner_aid)
                if winner_record:
                    winner_record["linkedinPostId"] = linkedin_result["post_id"]
                    winner_record["status"] = "published"
                    upsert_crawled_article(winner_record)

        # Update job and source
        now = datetime.now(timezone.utc).isoformat()
        job_updates.update({
            "articlesRelevant": relevant_count,
            "articlesProcessed": processed_count,
            "completedAt": now,
            "status": "completed",
        })
        if linkedin_result and linkedin_result.get("post_id"):
            job_updates["linkedinPostId"] = linkedin_result["post_id"]
            job_updates["linkedinSelectedIndex"] = linkedin_result["selected_index"]
        update_crawl_job(job["id"], job_updates)
        update_feed_source(source_id, {"lastCrawledAt": now})

        logger.info(
            f"Crawl complete for {source['name']}: "
            f"found={len(articles)}, new={len(new_articles)}, "
            f"relevant={relevant_count}, processed={processed_count}"
        )

        # Build summary with top article details for notifications
        top_article_summaries = []
        for article, classification in top_articles:
            top_article_summaries.append({
                "title": article["title"],
                "url": article["url"],
                "relevance_score": classification.get("relevance_score", 0),
                "matched_topics": classification.get("matched_topics", []),
            })

        crawl_summary = {
            "job_id": job["id"],
            "feed_source_id": source_id,
            "feed_source_name": source["name"],
            "articles_found": len(articles),
            "new_articles": len(new_articles),
            "articles_relevant": relevant_count,
            "articles_processed": processed_count,
            "top_articles": top_article_summaries,
            "linkedin_published": linkedin_result.get("post_id", "") if linkedin_result else "",
            "status": "completed",
        }

        notify("crawl_completed", crawl_summary)

        return crawl_summary

    except Exception as exc:
        logger.error(f"Crawl failed for {source['name']}: {exc}")
        update_crawl_job(job["id"], {
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "error": str(exc)[:500],
        })
        notify("pipeline_error", {
            "feed_source_id": source_id,
            "feed_source_name": source["name"],
            "error": str(exc)[:500],
            "stage": "crawl",
        })
        raise


def crawl_feed_source_stream(source_id: str) -> Generator[dict[str, Any], None, None]:
    """Crawl a feed source with streaming progress events.

    Yields dicts with keys: type (event name), data (event payload).
    Used by the SSE endpoint to give real-time feedback.
    """
    source = get_feed_source(source_id)
    if not source:
        yield {"type": "error", "data": {"error": f"Feed source not found: {source_id}"}}
        return

    source_name = source["name"]
    feed_type = source.get("feedType", "rss")

    yield {"type": "crawl_started", "data": {"source_name": source_name, "feed_type": feed_type}}

    job = create_crawl_job(source_id)

    try:
        # Fetch articles
        method = "RSS" if (feed_type == "rss" and source.get("feedUrl")) else "HTML scraping"
        yield {"type": "fetching_articles", "data": {"method": method}}

        if feed_type == "rss" and source.get("feedUrl"):
            articles = parse_rss_feed(source["feedUrl"])
        else:
            articles = scrape_blog_listing(source["baseUrl"])

        # Deduplicate
        new_articles: list[dict[str, Any]] = []
        for article in articles:
            aid = _article_id(article["url"])
            existing = get_crawled_article(aid)
            if not existing:
                new_articles.append(article)

        # Filter by article age
        max_age_days = source.get("maxArticleAgeDays", 7)
        pre_filter_count = len(new_articles)
        new_articles = _filter_by_age(new_articles, max_age_days)

        yield {
            "type": "articles_fetched",
            "data": {
                "total": len(articles),
                "new": pre_filter_count,
                "after_age_filter": len(new_articles),
                "max_age_days": max_age_days,
            },
        }

        job_updates: dict[str, Any] = {"articlesFound": len(articles)}
        relevant_count = 0
        processed_count = 0
        topics = source.get("topics", ["cloud security", "azure", "ai"])
        max_to_generate = source.get("maxArticlesToGenerate", 1)
        linkedin_candidates: list[dict[str, Any]] = []

        # Phase 1: Classify all new articles
        classified_articles: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for i, article in enumerate(new_articles):
            yield {
                "type": "classifying",
                "data": {"index": i + 1, "total": len(new_articles), "title": article["title"][:100]},
            }

            classification = classify_article(
                title=article["title"],
                summary=article.get("summary", ""),
                content="",
                topics=topics,
            )

            is_relevant = classification["is_relevant"]
            matched_topics = classification.get("matched_topics", [])
            score = classification.get("relevance_score", 0)

            yield {
                "type": "classified",
                "data": {
                    "index": i + 1,
                    "total": len(new_articles),
                    "title": article["title"][:100],
                    "is_relevant": is_relevant,
                    "matched_topics": matched_topics,
                    "relevance_score": score,
                },
            }

            if is_relevant:
                relevant_count += 1
                classified_articles.append((article, classification))
            else:
                aid = _article_id(article["url"])
                crawled_record: dict[str, Any] = {
                    "id": aid,
                    "feedSourceId": source_id,
                    "articleUrl": article["url"],
                    "title": article["title"],
                    "isRelevant": False,
                    "relevanceScore": score,
                    "matchedTopics": matched_topics,
                    "draftId": "",
                    "linkedinPostId": "",
                    "status": "skipped",
                    "crawledAt": datetime.now(timezone.utc).isoformat(),
                }
                upsert_crawled_article(crawled_record)

        # Phase 2: Rank relevant articles by technicality and pick top N
        if classified_articles and max_to_generate > 0:
            yield {
                "type": "ranking",
                "data": {"relevant_count": len(classified_articles), "max_to_generate": max_to_generate},
            }

            from backend.services.auto_publisher import rank_articles_by_technicality

            ranked = rank_articles_by_technicality(
                [
                    {
                        "title": a["title"],
                        "summary": a.get("summary", ""),
                        "url": a["url"],
                        "relevance_score": c.get("relevance_score", 0),
                        "matched_topics": c.get("matched_topics", []),
                    }
                    for a, c in classified_articles
                ],
                topics,
            )

            index_map = {item["url"]: rank for rank, item in enumerate(ranked)}
            classified_articles.sort(key=lambda ac: index_map.get(ac[0]["url"], 999))
            top_articles = classified_articles[:max_to_generate]
            skipped_articles = classified_articles[max_to_generate:]

            yield {
                "type": "ranked",
                "data": {
                    "top_count": len(top_articles),
                    "skipped_count": len(skipped_articles),
                    "top_titles": [a["title"][:80] for a, _ in top_articles],
                },
            }
        else:
            top_articles = []
            skipped_articles = classified_articles

        # Save skipped-by-rank articles
        for article, classification in skipped_articles:
            aid = _article_id(article["url"])
            crawled_record = {
                "id": aid,
                "feedSourceId": source_id,
                "articleUrl": article["url"],
                "title": article["title"],
                "isRelevant": True,
                "relevanceScore": classification.get("relevance_score", 0),
                "matchedTopics": classification.get("matched_topics", []),
                "matchedKeywords": classification.get("matched_keywords", []),
                "draftId": "",
                "linkedinPostId": "",
                "status": "skipped_rank",
                "crawledAt": datetime.now(timezone.utc).isoformat(),
            }
            upsert_crawled_article(crawled_record)

        # Phase 3: Generate blogs for top N articles only
        for i, (article, classification) in enumerate(top_articles):
            aid = _article_id(article["url"])

            yield {
                "type": "generating",
                "data": {
                    "index": i + 1,
                    "total_relevant": len(top_articles),
                    "title": article["title"][:100],
                },
            }

            crawled_record = {
                "id": aid,
                "feedSourceId": source_id,
                "articleUrl": article["url"],
                "title": article["title"],
                "isRelevant": True,
                "relevanceScore": classification.get("relevance_score", 0),
                "matchedTopics": classification.get("matched_topics", []),
                "matchedKeywords": classification.get("matched_keywords", []),
                "draftId": "",
                "linkedinPostId": "",
                "status": "pending",
                "crawledAt": datetime.now(timezone.utc).isoformat(),
            }

            try:
                result = process_relevant_article(article, source)
                crawled_record["draftId"] = result.get("draft_id", "")
                crawled_record["status"] = result.get("status", "drafted")
                processed_count += 1

                if result.get("linkedin_data"):
                    linkedin_candidates.append({
                        **result["linkedin_data"],
                        "crawled_article_id": aid,
                    })

                yield {
                    "type": "generated",
                    "data": {
                        "index": processed_count,
                        "total_relevant": len(top_articles),
                        "title": article["title"][:100],
                        "draft_id": result.get("draft_id", ""),
                        "status": result.get("status", "drafted"),
                    },
                }
            except Exception as exc:
                logger.error(f"Failed to process article {article['url']}: {exc}")
                crawled_record["status"] = "error"
                crawled_record["error"] = str(exc)[:500]
                crawled_record["lastError"] = str(exc)[:500]
                crawled_record["retryCount"] = 0

                yield {
                    "type": "generate_error",
                    "data": {
                        "title": article["title"][:100],
                        "error": str(exc)[:200],
                    },
                }

            upsert_crawled_article(crawled_record)

        # Phase 4: Select and publish the best LinkedIn post
        linkedin_result = None
        if linkedin_candidates:
            yield {
                "type": "selecting_best",
                "data": {"candidates": len(linkedin_candidates)},
            }

            linkedin_result = publish_best_linkedin_post(linkedin_candidates, source)

            if linkedin_result and linkedin_result.get("post_id"):
                winner_aid = linkedin_candidates[linkedin_result["selected_index"]]["crawled_article_id"]
                winner_record = get_crawled_article(winner_aid)
                if winner_record:
                    winner_record["linkedinPostId"] = linkedin_result["post_id"]
                    winner_record["status"] = "published"
                    upsert_crawled_article(winner_record)

                yield {
                    "type": "best_selected",
                    "data": {
                        "selected_index": linkedin_result["selected_index"],
                        "title": linkedin_result.get("title", ""),
                        "post_id": linkedin_result["post_id"],
                    },
                }
            elif linkedin_result and linkedin_result.get("skipped"):
                yield {
                    "type": "best_selected",
                    "data": {
                        "skipped": True,
                        "reason": linkedin_result.get("reason", "unknown"),
                    },
                }
            else:
                yield {
                    "type": "best_selected",
                    "data": {
                        "skipped": True,
                        "reason": "publish_failed",
                    },
                }

        # Finalize
        now = datetime.now(timezone.utc).isoformat()
        job_updates.update({
            "articlesRelevant": relevant_count,
            "articlesProcessed": processed_count,
            "completedAt": now,
            "status": "completed",
        })
        if linkedin_result and linkedin_result.get("post_id"):
            job_updates["linkedinPostId"] = linkedin_result["post_id"]
            job_updates["linkedinSelectedIndex"] = linkedin_result["selected_index"]
        update_crawl_job(job["id"], job_updates)
        update_feed_source(source_id, {"lastCrawledAt": now})

        yield {
            "type": "complete",
            "data": {
                "job_id": job["id"],
                "feed_source_id": source_id,
                "articles_found": len(articles),
                "new_articles": len(new_articles),
                "articles_relevant": relevant_count,
                "articles_processed": processed_count,
                "linkedin_published": linkedin_result.get("post_id", "") if linkedin_result else "",
                "status": "completed",
            },
        }

    except Exception as exc:
        logger.error(f"Crawl failed for {source_name}: {exc}")
        update_crawl_job(job["id"], {
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "error": str(exc)[:500],
        })
        yield {"type": "error", "data": {"error": str(exc)[:500]}}
