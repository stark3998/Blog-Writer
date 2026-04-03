"""Portfolio Deployer Tool — Trigger GitHub Pages rebuild after blog publish.

Dispatches the 'Deploy to GitHub Pages' workflow on the portfolio repo
so that newly published Cosmos DB content is picked up by the static build.
"""

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def _headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN environment variable is required to trigger portfolio deploy")
    return {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {token}",
    }


def _portfolio_repo() -> str:
    repo = os.environ.get("PORTFOLIO_GITHUB_REPO", "stark3998/portfolio").strip()
    return repo


def trigger_deploy(ref: str = "master") -> dict[str, Any]:
    """Trigger the portfolio GitHub Pages deploy workflow.

    Args:
        ref: Branch to run the workflow against. Defaults to 'master'.

    Returns:
        Dict with 'status', 'repo', and 'ref'.

    Raises:
        RuntimeError: If the GitHub API call fails.
    """
    repo = _portfolio_repo()
    url = f"{GITHUB_API}/repos/{repo}/actions/workflows/deploy.yml/dispatches"

    logger.info(f"Triggering portfolio deploy: repo={repo}, ref={ref}")

    resp = requests.post(
        url,
        json={"ref": ref},
        headers=_headers(),
        timeout=30,
    )

    if resp.status_code == 204:
        logger.info(f"Portfolio deploy triggered successfully: {repo}@{ref}")
        return {"status": "triggered", "repo": repo, "ref": ref}

    if resp.status_code == 404:
        raise RuntimeError(
            f"Workflow not found or no access: {repo}. "
            "Ensure GITHUB_TOKEN has 'actions:write' permission on the portfolio repo."
        )

    raise RuntimeError(
        f"Failed to trigger portfolio deploy (HTTP {resp.status_code}): {resp.text[:500]}"
    )
