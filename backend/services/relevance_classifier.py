"""Relevance Classifier — Keyword pre-filter + AI classification for articles."""

import json
import logging
import re
from typing import Any

import requests
from bs4 import BeautifulSoup

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
        # Core terms
        "artificial intelligence", "machine learning", "deep learning",
        "neural network", "llm", "large language model", "gpt",
        "generative ai", "gen ai", "genai", "copilot", "chatgpt",
        "openai", "langchain", "rag", "retrieval augmented",
        "transformer", "diffusion model", "prompt engineering",
        "fine-tuning", "fine tuning", "vector database",
        "embedding", "ai agent", "agentic", "computer vision",
        "natural language processing", "nlp", "mlops",
        # Agents & orchestration
        "ai agents", "multi-agent", "agent framework", "autogen",
        "crewai", "semantic kernel", "function calling", "tool use",
        "mcp", "model context protocol", "agent365", "agent 365",
        "agent id", "agentid",
        # Microsoft AI Foundry / Azure AI
        "ai foundry", "azure ai foundry", "microsoft foundry",
        "azure ai studio", "azure ai services", "azure ai search",
        "azure machine learning", "azure ml", "azure cognitive",
        "microsoft copilot", "copilot studio", "copilot agents",
        "microsoft 365 copilot", "m365 copilot",
        # ML fundamentals
        "ml", "model training", "model inference", "inference",
        "training data", "supervised learning", "unsupervised learning",
        "reinforcement learning", "classification", "regression", "clustering",
        # Models & architectures
        "foundation model", "small language model", "slm", "multimodal",
        "vision language", "text-to-image", "text-to-speech", "speech-to-text",
        "stable diffusion", "midjourney", "claude", "gemini",
        "llama", "mistral", "phi-3", "phi-4",
        # Tuning & optimization
        "fine-tune", "finetuning", "lora", "qlora",
        "quantization", "distillation", "pruning", "onnx", "tensorrt",
        # RAG & data
        "vector search", "vector store", "semantic search",
        "knowledge graph", "chunking", "reranking", "hybrid search",
        # Ops & platforms
        "ai ops", "aiops", "model deployment", "model serving",
        "hugging face", "huggingface", "pytorch", "tensorflow",
        "scikit-learn", "jupyter",
        # Responsible AI
        "responsible ai", "ai safety", "ai ethics", "hallucination",
        "guardrails", "red teaming", "ai governance",
    ],
}

# Priority topics get a 1.5x score multiplier (AI-focused content is higher priority)
PRIORITY_TOPICS: dict[str, float] = {
    "ai": 1.5,
}

# High-signal keywords get extra weight (these indicate deep technical content)
HIGH_SIGNAL_KEYWORDS: dict[str, list[str]] = {
    "cloud security": [
        "zero trust", "cspm", "cwpp", "cnapp", "sase", "devsecops",
        "threat detection", "incident response", "penetration testing",
    ],
    "azure": [
        "azure kubernetes", "aks", "bicep", "arm template", "azure policy",
        "azure landing zone", "azure sentinel", "azure openai",
    ],
    "ai": [
        "foundry", "microsoft foundry", "red team","azure ai", "llm", "large language model", "rag", "retrieval augmented",
        "fine-tuning", "fine tuning", "vector database", "ai agent",
        "agentic", "model context protocol", "prompt engineering",
        "foundation model", "lora", "qlora", "semantic kernel",
        "ai foundry", "azure ai foundry", "microsoft foundry",
        "agent365", "agent 365", "copilot studio", "copilot agents",
        "multi-agent", "agent framework", "autogen", "crewai",
    ],
}

USER_AGENT = "BlogWriter/1.0 (content-classifier)"


def _normalize(text: str) -> str:
    """Lowercase and normalize whitespace."""
    return re.sub(r"\s+", " ", text.lower().strip())


def get_active_keywords(topic: str) -> list[str]:
    """Get active keywords for a topic: Cosmos DB override if present, else code default."""
    from backend.db.cosmos_client import get_topic_keywords

    override = get_topic_keywords(topic)
    if override is not None:
        return override
    return TOPIC_KEYWORDS.get(topic, [topic])


def fetch_article_content(url: str, max_chars: int = 5000) -> str:
    """Fetch and extract main text content from an article URL.

    Returns plain text (up to max_chars), or empty string on failure.
    """
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.debug(f"Failed to fetch article content from {url}: {exc}")
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove non-content elements
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    # Try to find the main article content
    article = (
        soup.find("article")
        or soup.find("main")
        or soup.find("div", class_=re.compile(r"(post|article|entry|content|blog)", re.I))
        or soup.find("div", role="main")
    )

    text = (article or soup.body or soup).get_text(separator=" ", strip=True)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text[:max_chars]


def keyword_prefilter(
    title: str, summary: str, topics: list[str], content: str = ""
) -> dict[str, Any]:
    """Keyword match against title, summary, and optionally content.

    Returns a numeric keyword_score (0.0-1.0) based on match density and quality,
    plus matched topics/keywords. The score considers:
    - Number of distinct keyword matches
    - Whether matches are in the title (higher weight) vs body
    - Whether high-signal keywords matched
    """
    title_norm = _normalize(title)
    body_norm = _normalize(f"{summary} {content}")
    full_text = _normalize(f"{title} {summary} {content}")

    matched_topics: list[str] = []
    matched_keywords: list[str] = []
    raw_score = 0.0

    for topic in topics:
        topic_lower = topic.lower().strip()
        keywords = get_active_keywords(topic_lower)
        high_signal = [kw.lower() for kw in HIGH_SIGNAL_KEYWORDS.get(topic_lower, [])]
        priority_mult = PRIORITY_TOPICS.get(topic_lower, 1.0)

        # Check if this topic has a curated keyword list or just fell back to [topic]
        has_curated_list = topic_lower in TOPIC_KEYWORDS
        if not has_curated_list:
            from backend.db.cosmos_client import get_topic_keywords
            has_curated_list = get_topic_keywords(topic_lower) is not None

        if has_curated_list:
            for kw in keywords:
                if kw in full_text:
                    if topic_lower not in matched_topics:
                        matched_topics.append(topic_lower)
                    matched_keywords.append(kw)

                    # Score: title match = 0.15, body match = 0.05, high-signal bonus = 0.1
                    # Priority topics get a multiplier boost
                    base = 0.15 if kw in title_norm else 0.05
                    bonus = 0.1 if kw in high_signal else 0.0
                    raw_score += (base + bonus) * priority_mult
        else:
            # Unknown topic — check the full phrase first, then individual words
            if topic_lower in full_text:
                matched_topics.append(topic_lower)
                matched_keywords.append(topic_lower)
                base = 0.15 if topic_lower in title_norm else 0.05
                raw_score += base * priority_mult
            else:
                words = [w for w in topic_lower.split() if len(w) >= 3]
                for word in words:
                    if word in full_text:
                        if topic_lower not in matched_topics:
                            matched_topics.append(topic_lower)
                        matched_keywords.append(word)
                        base = 0.1 if word in title_norm else 0.03
                        raw_score += base * priority_mult

    # Cap the keyword score at 1.0
    keyword_score = min(raw_score, 1.0)

    return {
        "passed": len(matched_topics) > 0,
        "keyword_score": round(keyword_score, 3),
        "matched_topics": matched_topics,
        "matched_keywords": list(set(matched_keywords)),
    }


def ai_classify(
    title: str, content: str, topics: list[str]
) -> dict[str, Any]:
    """Use GPT-4o to classify article relevance and technicality.

    Returns:
        {is_relevant: bool, relevance_score: float, technicality_score: float,
         matched_topics: list[str], reasoning: str}
    """
    from backend.services.blog_service import _get_openai_client

    client, model = _get_openai_client()

    topics_str = ", ".join(topics)
    # Use up to 4000 chars of content for better classification
    truncated = content[:4000] if content else "(no content available)"

    prompt = (
        f"You are a technical content classifier. Analyze this article and determine:\n"
        f"1. **Relevance**: Is it relevant to ANY of these topics: {topics_str}?\n"
        f"2. **Technical depth**: How technically deep and actionable is the content?\n\n"
        f"PRIORITY: Articles about AI, AI agents, Microsoft AI Foundry, Azure AI, "
        f"Agent365, Copilot agents, and agentic frameworks should be scored HIGHER. "
        f"These are high-priority topics.\n\n"
        f"Scoring criteria for relevance_score (0.0-1.0):\n"
        f"- 0.0-0.2: Not relevant or only tangentially mentions the topic\n"
        f"- 0.3-0.5: Somewhat relevant, mentions topic but not the focus\n"
        f"- 0.6-0.7: Relevant, topic is a significant part of the article\n"
        f"- 0.8-1.0: Highly relevant, article is primarily about the topic\n\n"
        f"Scoring criteria for technicality_score (0.0-1.0):\n"
        f"- 0.0-0.2: News/press release, no technical detail\n"
        f"- 0.3-0.5: Overview or tutorial for beginners\n"
        f"- 0.6-0.7: Intermediate technical content with code/architecture\n"
        f"- 0.8-1.0: Deep technical content — implementation details, advanced patterns, benchmarks\n\n"
        f"Article title: {title}\n"
        f"Article content:\n{truncated}\n\n"
        f"Respond in JSON:\n"
        f'{{"is_relevant": true/false, "relevance_score": 0.0-1.0, '
        f'"technicality_score": 0.0-1.0, '
        f'"matched_topics": ["topic1"], "reasoning": "1-2 sentence explanation"}}'
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_completion_tokens=300,
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
            "technicality_score": float(parsed.get("technicality_score", 0)),
            "matched_topics": list(parsed.get("matched_topics", [])),
            "reasoning": str(parsed.get("reasoning", "")),
        }
    except Exception as exc:
        logger.warning(f"AI classification failed: {exc}; defaulting to not relevant")
        return {
            "is_relevant": False,
            "relevance_score": 0,
            "technicality_score": 0,
            "matched_topics": [],
            "reasoning": f"Classification error: {exc}",
        }


def classify_article(
    title: str,
    summary: str,
    content: str,
    topics: list[str],
) -> dict[str, Any]:
    """Three-stage classification: keyword scoring -> AI confirmation -> weighted final score.

    Stage 1: Keyword prefilter with numeric scoring
    Stage 2: AI classification (runs if keywords matched OR content is available for borderline cases)
    Stage 3: Combine keyword + AI scores into a weighted final score

    Returns:
        {is_relevant: bool, relevance_score: float, technicality_score: float,
         matched_topics: list[str], matched_keywords: list[str],
         keyword_score: float, method: str, reasoning: str}
    """
    if not topics:
        topics = ["cloud security", "azure", "ai"]

    # Stage 1: Keyword pre-filter with scoring
    kw_result = keyword_prefilter(title, summary, topics, content)
    keyword_score = kw_result["keyword_score"]

    # Determine if we should run AI classification
    # Run AI if: keywords matched, OR we have content to analyze (borderline/fallback)
    should_run_ai = kw_result["passed"] or bool(content.strip())

    if not should_run_ai:
        # No keyword matches and no content for AI fallback — reject
        logger.debug(f"Article '{title[:60]}' failed keyword prefilter (score={keyword_score})")
        return {
            "is_relevant": False,
            "relevance_score": 0,
            "technicality_score": 0,
            "matched_topics": [],
            "matched_keywords": [],
            "keyword_score": keyword_score,
            "method": "keyword_prefilter",
            "reasoning": "No keyword matches found and no content for AI fallback",
        }

    # Stage 2: AI classification
    logger.debug(
        f"Article '{title[:60]}' keyword_score={keyword_score}, "
        f"matched_topics={kw_result['matched_topics']}, running AI classification"
    )
    ai_text = content if content else f"{title}. {summary}"
    ai_result = ai_classify(title, ai_text, topics)

    # Stage 3: Weighted final score
    # keyword_score contributes 30%, AI relevance_score contributes 70%
    ai_relevance = ai_result["relevance_score"]
    final_score = round(keyword_score * 0.3 + ai_relevance * 0.7, 3)

    # Determine relevance: use AI verdict, but boost borderline cases with strong keyword matches
    is_relevant = ai_result["is_relevant"]
    if not is_relevant and keyword_score >= 0.3 and ai_relevance >= 0.4:
        # Borderline: strong keyword match + moderate AI score -> relevant
        is_relevant = True
        ai_result["reasoning"] += " (boosted by strong keyword matches)"

    # Merge topics from both stages
    all_topics = list(dict.fromkeys(
        ai_result["matched_topics"] + kw_result["matched_topics"]
    ))

    return {
        "is_relevant": is_relevant,
        "relevance_score": final_score,
        "technicality_score": ai_result.get("technicality_score", 0),
        "matched_topics": all_topics,
        "matched_keywords": kw_result["matched_keywords"],
        "keyword_score": keyword_score,
        "method": "keyword+ai",
        "reasoning": ai_result["reasoning"],
    }
