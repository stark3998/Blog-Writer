"""Relevance Classifier — Keyword pre-filter + AI classification for articles."""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Default keyword map: topic -> keywords that suggest relevance
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "cloud security": [
        "cloud security", "cloud-security", "cybersecurity", "cyber security",
        "zero trust", "zero-trust", "devsecops", "siem", "soar", "cspm",
        "cwpp", "cnapp", "sase", "security posture", "threat detection",
        "vulnerability", "compliance", "identity security", "iam",
        "data protection", "encryption", "firewall", "intrusion",
        "incident response", "penetration testing", "security operations",
        "sentinel", "defender", "security center",
    ],
    "azure": [
        "azure", "microsoft cloud", "entra", "azure ad",
        "azure devops", "azure kubernetes", "aks", "azure functions",
        "azure storage", "cosmos db", "azure sql", "bicep",
        "arm template", "azure monitor", "azure policy",
        "azure landing zone", "microsoft defender", "azure sentinel",
        "azure openai", "azure ai", "microsoft fabric",
    ],
    "ai": [
        "artificial intelligence", "machine learning", "deep learning",
        "neural network", "llm", "large language model", "gpt",
        "generative ai", "gen ai", "genai", "copilot", "chatgpt",
        "openai", "langchain", "rag", "retrieval augmented",
        "transformer", "diffusion model", "prompt engineering",
        "fine-tuning", "fine tuning", "vector database",
        "embedding", "ai agent", "agentic", "computer vision",
        "natural language processing", "nlp", "mlops",
    ],
}


def _normalize(text: str) -> str:
    """Lowercase and normalize whitespace."""
    return re.sub(r"\s+", " ", text.lower().strip())


def keyword_prefilter(
    title: str, summary: str, topics: list[str]
) -> dict[str, Any]:
    """Quick keyword match against title and summary.

    Returns:
        {passed: bool, matched_topics: list[str], matched_keywords: list[str]}
    """
    text = _normalize(f"{title} {summary}")
    matched_topics: list[str] = []
    matched_keywords: list[str] = []

    for topic in topics:
        topic_lower = topic.lower().strip()
        keywords = TOPIC_KEYWORDS.get(topic_lower, [topic_lower])
        for kw in keywords:
            if kw in text:
                if topic_lower not in matched_topics:
                    matched_topics.append(topic_lower)
                matched_keywords.append(kw)

    return {
        "passed": len(matched_topics) > 0,
        "matched_topics": matched_topics,
        "matched_keywords": list(set(matched_keywords)),
    }


def ai_classify(
    title: str, content: str, topics: list[str]
) -> dict[str, Any]:
    """Use GPT-4o to classify article relevance.

    Returns:
        {is_relevant: bool, relevance_score: float, matched_topics: list[str], reasoning: str}
    """
    from backend.services.blog_service import _get_openai_client

    client, model = _get_openai_client()

    topics_str = ", ".join(topics)
    # Truncate content to keep token cost low
    truncated = content[:3000] if content else "(no content available)"

    prompt = (
        f"You are a content classifier. Determine if this article is relevant to "
        f"ANY of these topics: {topics_str}.\n\n"
        f"Article title: {title}\n"
        f"Article content preview:\n{truncated}\n\n"
        f"Respond in JSON format:\n"
        f'{{"is_relevant": true/false, "relevance_score": 0.0-1.0, '
        f'"matched_topics": ["topic1"], "reasoning": "brief explanation"}}'
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_completion_tokens=200,
        )
        raw = response.choices[0].message.content or ""

        # Clean JSON fences
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```json?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        parsed = json.loads(text)
        return {
            "is_relevant": bool(parsed.get("is_relevant", False)),
            "relevance_score": float(parsed.get("relevance_score", 0)),
            "matched_topics": list(parsed.get("matched_topics", [])),
            "reasoning": str(parsed.get("reasoning", "")),
        }
    except Exception as exc:
        logger.warning(f"AI classification failed: {exc}; defaulting to not relevant")
        return {
            "is_relevant": False,
            "relevance_score": 0,
            "matched_topics": [],
            "reasoning": f"Classification error: {exc}",
        }


def classify_article(
    title: str,
    summary: str,
    content: str,
    topics: list[str],
) -> dict[str, Any]:
    """Two-stage classification: keyword pre-filter then AI confirmation.

    Returns:
        {is_relevant: bool, relevance_score: float, matched_topics: list[str],
         method: str, reasoning: str}
    """
    if not topics:
        topics = ["cloud security", "azure", "ai"]

    # Stage 1: Keyword pre-filter
    kw_result = keyword_prefilter(title, summary, topics)

    if not kw_result["passed"]:
        logger.debug(f"Article '{title[:60]}' failed keyword prefilter")
        return {
            "is_relevant": False,
            "relevance_score": 0,
            "matched_topics": [],
            "method": "keyword_prefilter",
            "reasoning": "No keyword matches found",
        }

    # Stage 2: AI classification for confirmation
    logger.debug(
        f"Article '{title[:60]}' passed keyword prefilter "
        f"(topics={kw_result['matched_topics']}), running AI classification"
    )
    ai_text = content if content else f"{title}. {summary}"
    ai_result = ai_classify(title, ai_text, topics)

    return {
        "is_relevant": ai_result["is_relevant"],
        "relevance_score": ai_result["relevance_score"],
        "matched_topics": ai_result["matched_topics"] or kw_result["matched_topics"],
        "method": "keyword+ai",
        "reasoning": ai_result["reasoning"],
    }
