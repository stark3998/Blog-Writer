"""Local test script for the Blog Writer Agent.

Usage:
    python test_local.py "https://github.com/owner/repo"
    python test_local.py "https://some-article-url.com/page"
    python test_local.py --analyze-only "https://github.com/owner/repo"
    python test_local.py --no-publish "https://github.com/owner/repo"
"""

import argparse
import asyncio
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from app.tools.github_analyzer import analyze_github_repo
from app.tools.webpage_analyzer import analyze_webpage
from app.agent import BlogWriterAgent, _is_github_url


def test_analyze_only(url: str) -> None:
    """Test just the analysis step — no LLM, no GitHub PR."""
    print(f"\n{'='*60}")
    print(f"  Analyzing: {url}")
    print(f"{'='*60}\n")

    if _is_github_url(url):
        print("[*] Detected: GitHub repository")
        result = analyze_github_repo(url)
        print(f"\n  Name:        {result.get('full_name')}")
        print(f"  Description: {result.get('description')}")
        print(f"  Language:    {result.get('primary_language')}")
        print(f"  Stars:       {result.get('stars')}")
        print(f"  Topics:      {', '.join(result.get('topics', []))}")
        print(f"  Files:       {result.get('total_files')} total")
        print(f"  Languages:   {json.dumps(result.get('languages', {}), indent=2)}")
        print(f"\n  README preview ({len(result.get('readme', ''))} chars):")
        readme = result.get("readme", "")
        print(f"  {readme[:500]}..." if len(readme) > 500 else f"  {readme}")
        print(f"\n  Key files fetched: {list(result.get('key_files', {}).keys())}")
    else:
        print("[*] Detected: Webpage")
        result = analyze_webpage(url)
        print(f"\n  Title:       {result.get('title')}")
        print(f"  Description: {result.get('description')}")
        print(f"  Headings:    {len(result.get('headings', []))} found")
        for h in result.get("headings", [])[:10]:
            print(f"    {h['level']}: {h['text']}")
        content = result.get("content", "")
        print(f"\n  Content preview ({len(content)} chars):")
        print(f"  {content[:500]}..." if len(content) > 500 else f"  {content}")
        print(f"\n  Code blocks: {len(result.get('code_blocks', []))}")

    print(f"\n{'='*60}")
    print("  Analysis complete!")
    print(f"{'='*60}")


async def test_generate_no_publish(url: str) -> None:
    """Test analysis + blog generation, but skip the PR step."""
    print(f"\n{'='*60}")
    print(f"  Generating blog post (no publish): {url}")
    print(f"{'='*60}\n")

    agent = BlogWriterAgent()

    # Step 1: Analyze
    print("[1/2] Analyzing source...")
    analysis = agent.analyze_source(url)
    source_type = analysis.get("_source_type", "webpage")
    print(f"  Source type: {source_type}")

    # Step 2: Generate
    print("[2/2] Generating blog post with GPT-4o...")
    blog_data = agent.generate_blog_post(analysis)

    print(f"\n{'='*60}")
    print(f"  GENERATED BLOG POST")
    print(f"{'='*60}")
    print(f"\n  Title:   {blog_data['title']}")
    print(f"  Slug:    {blog_data['slug']}")
    print(f"  Excerpt: {blog_data['excerpt']}")
    print(f"\n{'—'*60}")
    print(blog_data["mdx_content"])
    print(f"{'—'*60}")

    # Save to a local file for preview
    preview_path = f"preview_{blog_data['slug']}.mdx"
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(blog_data["mdx_content"])
    print(f"\n  Saved preview to: {preview_path}")
    print(f"{'='*60}")


async def test_full_pipeline(url: str) -> None:
    """Test the full pipeline: analyze → generate → publish PR."""
    print(f"\n{'='*60}")
    print(f"  Full pipeline: {url}")
    print(f"{'='*60}\n")

    agent = BlogWriterAgent()
    result = await agent.run(f"Write a blog post about {url}")
    print(result)


def main():
    parser = argparse.ArgumentParser(description="Test the Blog Writer Agent locally")
    parser.add_argument("url", help="GitHub repo URL or webpage URL to analyze")
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Only run the analysis step (no LLM, no GitHub API needed)",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Run analysis + generation but skip the PR publish step",
    )
    args = parser.parse_args()

    if args.analyze_only:
        test_analyze_only(args.url)
    elif args.no_publish:
        asyncio.run(test_generate_no_publish(args.url))
    else:
        asyncio.run(test_full_pipeline(args.url))


if __name__ == "__main__":
    main()
