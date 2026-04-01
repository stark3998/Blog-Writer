"""Quick test script — sends sample notifications to the configured WEBHOOK_URL."""

import os
import sys

# Load .env
from dotenv import load_dotenv
load_dotenv()

from backend.services.notification_service import notify

SAMPLE_EVENTS = {
    "blog_published": {
        "title": "Securing Azure Kubernetes Service with Workload Identity",
        "excerpt": "A deep dive into replacing pod-managed identities with the new AKS Workload Identity federation model, including Terraform examples and RBAC best practices.",
        "slug": "securing-aks-workload-identity",
        "blog_url": "https://stark3998.github.io/portfolio/blog/securing-aks-workload-identity",
        "source_url": "https://techcommunity.microsoft.com/blog/fasttrackforazureblog/aks-workload-identity",
        "hero_image_url": "https://picsum.photos/800/400",
        "tags": ["azure", "kubernetes", "security", "workload-identity"],
        "topics": ["cloud security", "azure", "kubernetes"],
    },
    "linkedin_published": {
        "post_id": "urn:li:share:7654321098765",
        "title": "Securing Azure Kubernetes Service with Workload Identity",
        "excerpt": "Deep dive into AKS Workload Identity federation.",
        "blog_url": "https://stark3998.github.io/portfolio/blog/securing-aks-workload-identity",
        "article_url": "https://techcommunity.microsoft.com/blog/fasttrackforazureblog/aks-workload-identity",
        "image_url": "https://picsum.photos/800/400",
        "hashtags": ["#Azure", "#Kubernetes", "#CloudSecurity", "#AKS", "#ZeroTrust"],
        "post_text_preview": "🔐 AKS just got a major security upgrade. Workload Identity federation replaces pod-managed identities — here's why it matters and how to set it up with Terraform...",
    },
    "crawl_completed": {
        "feed_source_name": "Microsoft Tech Community",
        "articles_found": 24,
        "new_articles": 8,
        "articles_relevant": 3,
        "articles_processed": 1,
        "linkedin_published": "urn:li:share:7654321098765",
        "top_articles": [
            {
                "title": "Securing AKS with Workload Identity Federation",
                "url": "https://example.com/article-1",
                "relevance_score": 0.92,
                "matched_topics": ["azure", "kubernetes", "security"],
            },
            {
                "title": "What's New in Azure OpenAI GPT-4o Fine-Tuning",
                "url": "https://example.com/article-2",
                "relevance_score": 0.78,
                "matched_topics": ["azure", "ai"],
            },
            {
                "title": "Microsoft Defender for Cloud — March Updates",
                "url": "https://example.com/article-3",
                "relevance_score": 0.65,
                "matched_topics": ["cloud security"],
            },
        ],
    },
    "pipeline_error": {
        "feed_source_name": "Microsoft Tech Community",
        "stage": "linkedin_publish",
        "title": "Securing AKS with Workload Identity",
        "error": "LinkedInAPIError: 401 Unauthorized — access token expired. Session ID: abc-123. The LinkedIn OAuth session needs to be re-authenticated.",
    },
    "linkedin_session_expiring": {
        "session_id": "abc-123",
        "days_remaining": 5,
        "message": "LinkedIn session expires in 5 days. Re-authenticate soon to continue auto-publishing.",
    },
}


def main():
    url = os.environ.get("WEBHOOK_URL", "")
    if not url:
        print("ERROR: WEBHOOK_URL not set in .env")
        sys.exit(1)

    # Pick which events to send
    events = sys.argv[1:] if len(sys.argv) > 1 else list(SAMPLE_EVENTS.keys())

    for event_type in events:
        if event_type not in SAMPLE_EVENTS:
            print(f"Unknown event: {event_type}")
            print(f"Available: {', '.join(SAMPLE_EVENTS.keys())}")
            continue

        print(f"Sending {event_type}...", end=" ")
        ok = notify(event_type, SAMPLE_EVENTS[event_type])
        print("OK" if ok else "FAILED")


if __name__ == "__main__":
    main()
