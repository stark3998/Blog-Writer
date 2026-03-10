"""GitHub Repository Analyzer Tool.

Fetches and analyzes a GitHub repository to extract structured data
for blog post generation: README, file tree, language breakdown,
and key code snippets.
"""

import os
import re
import base64
from typing import Any

import requests

GITHUB_API = "https://api.github.com"

# File patterns worth reading for technical analysis
KEY_FILE_PATTERNS = [
    r"^README\.md$",
    r"^Dockerfile$",
    r"^docker-compose\.ya?ml$",
    r"^\.github/workflows/.*\.ya?ml$",
    r"^Makefile$",
    r"^package\.json$",
    r"^requirements\.txt$",
    r"^pyproject\.toml$",
    r"^Cargo\.toml$",
    r"^go\.mod$",
    r"^pom\.xml$",
    r"^build\.gradle$",
    r"^terraform/.*\.tf$",
    r"^infra/.*\.(bicep|tf)$",
    r"^azure\.ya?ml$",
    r"^app\.(py|ts|js)$",
    r"^main\.(py|ts|js|go|rs)$",
    r"^index\.(ts|js)$",
    r"^src/main\.(py|ts|js|go|rs)$",
    r"^src/index\.(ts|js)$",
    r"^src/app\.(py|ts|js)$",
]

# Maximum file size to read (in bytes)
MAX_FILE_SIZE = 50_000
# Maximum number of key files to fetch
MAX_KEY_FILES = 10
# Maximum tree entries to include
MAX_TREE_ENTRIES = 200


def _headers() -> dict[str, str]:
    """Build request headers with optional GitHub token."""
    h = {"Accept": "application/vnd.github.v3+json"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _parse_repo_url(url: str) -> tuple[str, str]:
    """Extract owner and repo name from a GitHub URL.

    Supports:
        https://github.com/owner/repo
        https://github.com/owner/repo.git
        https://github.com/owner/repo/tree/main/...
        github.com/owner/repo
    """
    url = url.strip().rstrip("/")
    # Remove .git suffix
    url = re.sub(r"\.git$", "", url)
    # Remove tree/branch/path suffixes
    url = re.sub(r"/tree/.*$", "", url)
    url = re.sub(r"/blob/.*$", "", url)

    match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
    if not match:
        raise ValueError(f"Could not parse GitHub URL: {url}")
    return match.group(1), match.group(2)


def _is_key_file(path: str) -> bool:
    """Check if a file path matches our key file patterns."""
    return any(re.match(p, path, re.IGNORECASE) for p in KEY_FILE_PATTERNS)


def _get_json(url: str) -> Any:
    """Make a GET request and return JSON."""
    resp = requests.get(url, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def analyze_github_repo(url: str) -> dict[str, Any]:
    """Analyze a GitHub repository and return structured data.

    Args:
        url: GitHub repository URL (e.g., https://github.com/owner/repo)

    Returns:
        Dictionary with repo metadata, file tree, README content,
        language breakdown, and key file contents.
    """
    owner, repo = _parse_repo_url(url)

    # Fetch repo metadata
    repo_data = _get_json(f"{GITHUB_API}/repos/{owner}/{repo}")

    result: dict[str, Any] = {
        "repo_url": url,
        "full_name": repo_data.get("full_name", f"{owner}/{repo}"),
        "description": repo_data.get("description", ""),
        "stars": repo_data.get("stargazers_count", 0),
        "forks": repo_data.get("forks_count", 0),
        "primary_language": repo_data.get("language", "Unknown"),
        "topics": repo_data.get("topics", []),
        "created_at": repo_data.get("created_at", ""),
        "updated_at": repo_data.get("updated_at", ""),
        "license": (repo_data.get("license") or {}).get("spdx_id", "None"),
        "default_branch": repo_data.get("default_branch", "main"),
    }

    # Fetch language breakdown
    try:
        languages = _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/languages")
        total = sum(languages.values()) if languages else 1
        result["languages"] = {
            lang: f"{(bytes_count / total) * 100:.1f}%"
            for lang, bytes_count in languages.items()
        }
    except Exception:
        result["languages"] = {}

    # Fetch file tree
    default_branch = result["default_branch"]
    try:
        tree_data = _get_json(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1"
        )
        all_entries = tree_data.get("tree", [])
        file_entries = [
            e["path"] for e in all_entries
            if e["type"] == "blob"
        ]
        result["file_tree"] = file_entries[:MAX_TREE_ENTRIES]
        result["total_files"] = len(file_entries)
    except Exception:
        result["file_tree"] = []
        result["total_files"] = 0

    # Fetch README
    try:
        readme_data = _get_json(f"{GITHUB_API}/repos/{owner}/{repo}/readme")
        readme_content = base64.b64decode(readme_data["content"]).decode("utf-8")
        # Truncate very long READMEs
        if len(readme_content) > 15_000:
            readme_content = readme_content[:15_000] + "\n\n... (truncated)"
        result["readme"] = readme_content
    except Exception:
        result["readme"] = ""

    # Fetch key files
    key_files: dict[str, str] = {}
    fetched = 0
    for file_path in result.get("file_tree", []):
        if fetched >= MAX_KEY_FILES:
            break
        if _is_key_file(file_path):
            try:
                file_data = _get_json(
                    f"{GITHUB_API}/repos/{owner}/{repo}/contents/{file_path}"
                    f"?ref={default_branch}"
                )
                if file_data.get("size", 0) <= MAX_FILE_SIZE:
                    content = base64.b64decode(file_data["content"]).decode("utf-8")
                    key_files[file_path] = content
                    fetched += 1
            except Exception:
                continue
    result["key_files"] = key_files

    return result
