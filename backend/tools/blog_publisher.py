"""Blog Publisher Tool.

Creates a new branch on the portfolio GitHub repo, commits a generated
MDX blog post file, and opens a Pull Request for review.
"""

import os
import base64
from datetime import datetime, timezone
from typing import Any

import requests

GITHUB_API = "https://api.github.com"


def _headers() -> dict[str, str]:
    """Build authenticated GitHub API headers."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN environment variable is not set.")
    return {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {token}",
    }


def _github_request(
    method: str, url: str, json_body: dict | None = None
) -> Any:
    """Make an authenticated GitHub API request."""
    resp = requests.request(
        method, url, headers=_headers(), json=json_body, timeout=30,
    )
    resp.raise_for_status()
    if resp.status_code == 204:
        return {}
    return resp.json()


def publish_blog_post(
    mdx_content: str, slug: str, title: str, excerpt: str,
) -> dict[str, str]:
    """Publish a blog post by creating a branch, committing the MDX file,
    and opening a PR on the portfolio repo.

    Args:
        mdx_content: The complete MDX file content (with frontmatter).
        slug: URL slug for the blog post.
        title: Blog post title (used in PR title/description).
        excerpt: Blog post excerpt (used in PR description).

    Returns:
        Dictionary with 'pr_url', 'branch', and 'file_path'.
    """
    repo = os.environ.get("GITHUB_REPO", "")
    if not repo:
        raise RuntimeError("GITHUB_REPO environment variable is not set.")

    file_path = f"content/blog/{slug}.mdx"
    branch_name = f"blog/{slug}"

    main_ref = _github_request(
        "GET", f"{GITHUB_API}/repos/{repo}/git/ref/heads/main"
    )
    main_sha = main_ref["object"]["sha"]

    try:
        _github_request(
            "POST",
            f"{GITHUB_API}/repos/{repo}/git/refs",
            {"ref": f"refs/heads/{branch_name}", "sha": main_sha},
        )
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 422:
            _github_request(
                "PATCH",
                f"{GITHUB_API}/repos/{repo}/git/refs/heads/{branch_name}",
                {"sha": main_sha, "force": True},
            )
        else:
            raise

    encoded_content = base64.b64encode(mdx_content.encode("utf-8")).decode("utf-8")

    existing_sha = None
    try:
        existing = _github_request(
            "GET",
            f"{GITHUB_API}/repos/{repo}/contents/{file_path}?ref={branch_name}",
        )
        existing_sha = existing.get("sha")
    except requests.exceptions.HTTPError:
        pass

    commit_body: dict[str, Any] = {
        "message": f"blog: add post '{title}'",
        "content": encoded_content,
        "branch": branch_name,
    }
    if existing_sha:
        commit_body["sha"] = existing_sha

    _github_request(
        "PUT",
        f"{GITHUB_API}/repos/{repo}/contents/{file_path}",
        commit_body,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pr_body = (
        f"## 📝 New Blog Post\n\n"
        f"**Title:** {title}\n\n"
        f"**Excerpt:** {excerpt}\n\n"
        f"**File:** `{file_path}`\n\n"
        f"**Generated:** {today} by Blog Writer Agent\n\n"
        f"---\n\n"
        f"Please review the generated content before merging."
    )

    try:
        pr = _github_request(
            "POST",
            f"{GITHUB_API}/repos/{repo}/pulls",
            {
                "title": f"📝 Blog: {title}",
                "body": pr_body,
                "head": branch_name,
                "base": "main",
            },
        )
        pr_url = pr.get("html_url", "")
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 422:
            prs = _github_request(
                "GET",
                f"{GITHUB_API}/repos/{repo}/pulls?head={repo.split('/')[0]}:{branch_name}&state=open",
            )
            if prs:
                pr_url = prs[0].get("html_url", "")
            else:
                pr_url = f"https://github.com/{repo}/compare/main...{branch_name}"
        else:
            raise

    return {
        "pr_url": pr_url,
        "branch": branch_name,
        "file_path": file_path,
    }
