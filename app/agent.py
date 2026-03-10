"""Blog Writer Agent — Core Agent Class.

Uses Microsoft Agent Framework to orchestrate the blog generation pipeline:
1. Detect URL type (GitHub repo vs. webpage)
2. Analyze the source content
3. Generate a blog post via GPT-4o
4. Publish as a PR to the portfolio repo
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_framework import BaseAgent
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from app.tools.github_analyzer import analyze_github_repo
from app.tools.webpage_analyzer import analyze_webpage
from app.tools.blog_publisher import publish_blog_post


SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"


def _load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _is_github_url(url: str) -> bool:
    """Check if the URL points to a GitHub repository."""
    return bool(re.match(r"https?://(www\.)?github\.com/[^/]+/[^/]+", url.strip()))


def _build_analysis_context(analysis: dict[str, Any], source_type: str) -> str:
    """Format the analysis data into a context string for the LLM."""
    if source_type == "github":
        parts = [
            f"## Source: GitHub Repository",
            f"**URL:** {analysis.get('repo_url', '')}",
            f"**Name:** {analysis.get('full_name', '')}",
            f"**Description:** {analysis.get('description', 'No description')}",
            f"**Primary Language:** {analysis.get('primary_language', 'Unknown')}",
            f"**Stars:** {analysis.get('stars', 0)} | **Forks:** {analysis.get('forks', 0)}",
            f"**License:** {analysis.get('license', 'None')}",
            f"**Topics:** {', '.join(analysis.get('topics', []))}",
            "",
            "### Language Breakdown",
        ]
        for lang, pct in analysis.get("languages", {}).items():
            parts.append(f"- {lang}: {pct}")

        parts.append("")
        parts.append("### File Tree (key files)")
        tree = analysis.get("file_tree", [])
        for fp in tree[:100]:
            parts.append(f"- {fp}")
        if len(tree) > 100:
            parts.append(f"- ... and {len(tree) - 100} more files")

        parts.append("")
        parts.append("### README")
        parts.append(analysis.get("readme", "(No README found)"))

        key_files = analysis.get("key_files", {})
        if key_files:
            parts.append("")
            parts.append("### Key Files")
            for fp, content in key_files.items():
                parts.append(f"\n#### {fp}\n```\n{content}\n```")

        return "\n".join(parts)
    else:
        parts = [
            f"## Source: Webpage",
            f"**URL:** {analysis.get('url', '')}",
            f"**Title:** {analysis.get('title', '')}",
            f"**Description:** {analysis.get('description', '')}",
            "",
            "### Headings Structure",
        ]
        for h in analysis.get("headings", []):
            indent = "  " * (int(h["level"][1]) - 1)
            parts.append(f"{indent}- [{h['level']}] {h['text']}")

        parts.append("")
        parts.append("### Main Content")
        parts.append(analysis.get("content", "(No content extracted)"))

        code_blocks = analysis.get("code_blocks", [])
        if code_blocks:
            parts.append("")
            parts.append("### Code Blocks Found")
            for i, block in enumerate(code_blocks, 1):
                lang = block.get("language", "")
                parts.append(f"\n#### Code Block {i}" + (f" ({lang})" if lang else ""))
                parts.append(f"```{lang}\n{block['code']}\n```")

        return "\n".join(parts)


class BlogWriterAgent(BaseAgent):
    """Agent that generates blog posts from GitHub repos or webpages."""

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt = _load_system_prompt()

        # Initialize Azure OpenAI client
        endpoint = os.environ.get("PROJECT_ENDPOINT", "")
        model = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o")

        # Use token provider for Azure AD auth
        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )

        self.openai_client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=token_provider,
            api_version="2024-12-01-preview",
        )
        self.model = model

    @activity
    def analyze_source(self, url: str) -> dict[str, Any]:
        """Analyze a URL — auto-detects GitHub repos vs. general webpages.

        Args:
            url: A GitHub repository URL or any webpage URL.

        Returns:
            Analysis data dictionary with source type.
        """
        url = url.strip()
        if _is_github_url(url):
            analysis = analyze_github_repo(url)
            analysis["_source_type"] = "github"
        else:
            analysis = analyze_webpage(url)
            analysis["_source_type"] = "webpage"
        return analysis

    @activity
    def generate_blog_post(self, analysis: dict[str, Any]) -> dict[str, str]:
        """Generate a blog post from analysis data using GPT-4o.

        Args:
            analysis: The structured analysis data from analyze_source.

        Returns:
            Dictionary with 'mdx_content', 'slug', 'title', and 'excerpt'.
        """
        source_type = analysis.pop("_source_type", "webpage")
        context = _build_analysis_context(analysis, source_type)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        user_message = (
            f"Today's date is {today}.\n\n"
            f"Please analyze the following source material and generate a complete blog post.\n\n"
            f"{context}\n\n"
            f"---\n\n"
            f"Generate the complete MDX blog post now. After the MDX content, on a new line, "
            f'output the slug in the format: SLUG: your-slug-here'
        )

        response = self.openai_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.7,
            max_tokens=4096,
        )

        full_response = response.choices[0].message.content or ""

        # Parse out the slug
        slug_match = re.search(r"SLUG:\s*(.+?)(?:\s*$)", full_response, re.MULTILINE)
        if slug_match:
            slug = slug_match.group(1).strip()
            mdx_content = full_response[: slug_match.start()].strip()
        else:
            # Fallback: generate slug from first line
            slug = "generated-post-" + today
            mdx_content = full_response.strip()

        # Remove any markdown code fences wrapping the entire MDX
        mdx_content = re.sub(r"^```mdx?\s*\n", "", mdx_content)
        mdx_content = re.sub(r"\n```\s*$", "", mdx_content)

        # Extract title and excerpt from frontmatter for the PR
        title_match = re.search(r'^title:\s*"(.+?)"', mdx_content, re.MULTILINE)
        excerpt_match = re.search(r'^excerpt:\s*"(.+?)"', mdx_content, re.MULTILINE)

        title = title_match.group(1) if title_match else "Generated Blog Post"
        excerpt = excerpt_match.group(1) if excerpt_match else ""

        return {
            "mdx_content": mdx_content,
            "slug": slug,
            "title": title,
            "excerpt": excerpt,
        }

    @activity
    def publish(self, blog_data: dict[str, str]) -> dict[str, str]:
        """Publish the generated blog post as a PR.

        Args:
            blog_data: Output from generate_blog_post.

        Returns:
            Dictionary with PR URL and metadata.
        """
        return publish_blog_post(
            mdx_content=blog_data["mdx_content"],
            slug=blog_data["slug"],
            title=blog_data["title"],
            excerpt=blog_data["excerpt"],
        )

    async def run(self, input_text: str) -> str:
        """Main agent execution: analyze → generate → publish.

        Args:
            input_text: User message containing a URL to analyze.

        Returns:
            Summary of the generated blog post and PR link.
        """
        # Extract URL from user message
        url_match = re.search(r"https?://[^\s<>\"']+", input_text)
        if not url_match:
            return (
                "I couldn't find a URL in your message. Please provide a GitHub "
                "repository URL or a webpage URL, and I'll generate a blog post from it."
            )

        url = url_match.group(0).rstrip(".,;:!?)")

        # Step 1: Analyze the source
        source_type = "GitHub repository" if _is_github_url(url) else "webpage"
        analysis = self.analyze_source(url)

        # Step 2: Generate the blog post
        blog_data = self.generate_blog_post(analysis)

        # Step 3: Publish as a PR
        result = self.publish(blog_data)

        return (
            f"✅ **Blog post generated and published!**\n\n"
            f"**Title:** {blog_data['title']}\n"
            f"**Slug:** {blog_data['slug']}\n"
            f"**Source:** {source_type} — {url}\n\n"
            f"**Pull Request:** {result['pr_url']}\n"
            f"**Branch:** `{result['branch']}`\n"
            f"**File:** `{result['file_path']}`\n\n"
            f"The PR is ready for your review. Once you approve and merge it, "
            f"the blog post will be automatically deployed to your portfolio."
        )

    async def run_stream(self, input_text: str):
        """Streaming version — yields the final result as a single chunk."""
        result = await self.run(input_text)
        yield result
