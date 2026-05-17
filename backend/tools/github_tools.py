"""Native GitHub tools — user-centric queries and PR review via REST API.

Supplements the MCP GitHub server with:
- User-level queries (repos, PRs, issues) that always use the correct owner prefix
- Senior-developer PR review with security, architecture, and linting analysis
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .registry import REGISTRY, ToolEntry

logger = logging.getLogger("jarvis.tools")

_BASE = "https://api.github.com"

# Detailed review — posted to GitHub for the developer
_PR_REVIEW_PROMPT = """You are a senior software engineer conducting a thorough code review.
Review the following pull request diff and provide structured feedback covering:

1. **Security** — Any hardcoded secrets, API keys, tokens, passwords, or env vars exposed in code.
   Flag ANY string that looks like a secret (even partial). This is CRITICAL.
   Check for: SQL injection, XSS, CSRF, insecure deserialization, path traversal, OWASP Top 10.

2. **Architecture** — Does the code follow clean architecture? Are concerns properly separated?
   Watch for: God objects, circular dependencies, business logic in wrong layers, tight coupling,
   missing abstractions, violation of SOLID principles.

3. **Code Quality** — Naming conventions, function length, complexity, duplication (DRY violations),
   unused imports/variables, magic numbers/strings, commented-out code left in.

4. **Linting / Style** — Missing type hints (Python), any/implicit types (TS), inconsistent
   formatting, long lines (>120 chars), missing error handling, bare except clauses.

5. **Testing** — Are new functions/endpoints covered by tests? Any test smells?
   Missing edge-case tests, no negative tests, mocking anti-patterns.

6. **Deployment / Config** — Missing migrations, config changes without defaults,
   breaking API changes, dependency version pins removed, env vars not documented.

7. **Performance** — N+1 queries, missing indexes, unnecessary loops, blocking I/O in async code.

8. **Overall verdict** — APPROVE / REQUEST CHANGES / NEEDS DISCUSSION

For each issue, include: file name + line reference if possible, what the problem is, and how to fix it.
If no issues found in a category, write "✅ Clean."
End with a short summary paragraph and the verdict on its own line.
"""

# Brief summary — shown to the user in chat (not posted to GitHub)
_PR_SUMMARY_PROMPT = """You are JARVIS, a senior developer assistant.
Given this PR review analysis, produce a brief summary for the owner:

- 3–5 bullet points: most critical issues only (security flags first, then blockers)
- Skip categories that are clean
- Final line: verdict — APPROVE / REQUEST CHANGES / NEEDS DISCUSSION

Be concise. No section headers. Bullets + verdict only.
"""


def _headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _username() -> str:
    return os.environ.get("GITHUB_USERNAME", "").strip()


async def _gh_get(path: str, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{_BASE}{path}", headers=_headers(), params=params)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# List repositories
# ---------------------------------------------------------------------------

async def _github_list_repos(args: dict[str, Any]) -> str:
    max_repos = min(int(args.get("max_results", 20) or 20), 100)
    sort      = str(args.get("sort", "updated") or "updated")
    try:
        repos = await _gh_get("/user/repos", {
            "sort": sort, "per_page": max_repos, "affiliation": "owner",
        })
        if not repos:
            return "No repositories found."
        lines = [f"Your GitHub repositories ({len(repos)} shown, sorted by {sort}):"]
        for r in repos:
            vis   = "🔒" if r.get("private") else "🌐"
            stars = r.get("stargazers_count", 0)
            lang  = r.get("language") or "—"
            lines.append(f"{vis} {r['name']} — {lang} — ⭐{stars} — {r.get('description') or ''}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("github_list_repos failed")
        return f"GitHub error: {exc}"


# ---------------------------------------------------------------------------
# My open pull requests (across all repos)
# ---------------------------------------------------------------------------

async def _github_my_prs(args: dict[str, Any]) -> str:
    state = str(args.get("state", "open") or "open")
    max_r = min(int(args.get("max_results", 10) or 10), 30)
    try:
        username = _username()
        q = f"type:pr state:{state} author:{username}" if username else f"type:pr state:{state}"
        data = await _gh_get("/search/issues", {"q": q, "per_page": max_r, "sort": "updated"})
        items = data.get("items", [])
        total = data.get("total_count", 0)
        if not items:
            return f"No {state} pull requests found."
        lines = [f"{total} {state} PR(s) (showing {len(items)}):"]
        for pr in items:
            repo = pr.get("repository_url", "").split("/repos/")[-1]
            lines.append(f"• [{repo}] #{pr['number']} — {pr['title']} ({pr.get('created_at','')[:10]})")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("github_my_prs failed")
        return f"GitHub error: {exc}"


# ---------------------------------------------------------------------------
# My open issues
# ---------------------------------------------------------------------------

async def _github_my_issues(args: dict[str, Any]) -> str:
    state = str(args.get("state", "open") or "open")
    max_r = min(int(args.get("max_results", 10) or 10), 30)
    try:
        username = _username()
        q = f"type:issue state:{state} assignee:{username}" if username else f"type:issue state:{state}"
        data = await _gh_get("/search/issues", {"q": q, "per_page": max_r, "sort": "updated"})
        items = data.get("items", [])
        total = data.get("total_count", 0)
        if not items:
            return f"No {state} issues assigned to you."
        lines = [f"{total} {state} issue(s) assigned to you (showing {len(items)}):"]
        for issue in items:
            repo = issue.get("repository_url", "").split("/repos/")[-1]
            lines.append(f"• [{repo}] #{issue['number']} — {issue['title']}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("github_my_issues failed")
        return f"GitHub error: {exc}"


# ---------------------------------------------------------------------------
# GitHub profile / account summary
# ---------------------------------------------------------------------------

async def _github_profile(_args: dict[str, Any]) -> str:
    try:
        u = await _gh_get("/user")
        pub  = u.get("public_repos", 0)
        priv = u.get("total_private_repos", 0)
        fol  = u.get("followers", 0)
        lines = [
            f"GitHub account: {u.get('name')} (@{u.get('login')})",
            f"Repositories: {pub} public, {priv} private ({pub+priv} total)",
            f"Followers: {fol}  |  Following: {u.get('following',0)}",
            f"Member since: {str(u.get('created_at',''))[:10]}",
        ]
        if u.get("bio"):
            lines.append(f"Bio: {u['bio']}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("github_profile failed")
        return f"GitHub error: {exc}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "github_list_repos",
            "description": (
                "List the authenticated user's own GitHub repositories. "
                "Use this for 'how many repos', 'show my repos', 'list my GitHub projects'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "description": "Max repos to return (default 20)"},
                    "sort":        {"type": "string",  "description": "Sort by: updated, created, pushed, full_name (default: updated)"},
                },
                "required": [],
            },
        },
    },
    handler=_github_list_repos,
    thinking_label="Fetching your GitHub repos…",
    terminal=True,
    help_hint="lists user's own repos via REST API",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "github_my_prs",
            "description": (
                "List the user's own pull requests across all repositories. "
                "Use for 'my open PRs', 'any pull requests', 'what PRs do I have'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "state":       {"type": "string",  "description": "open, closed, or merged (default: open)"},
                    "max_results": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": [],
            },
        },
    },
    handler=_github_my_prs,
    thinking_label="Checking your pull requests…",
    terminal=True,
    help_hint="lists user's PRs across all repos",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "github_my_issues",
            "description": (
                "List GitHub issues assigned to the user across all repositories. "
                "Use for 'my issues', 'assigned issues', 'what issues do I have'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "state":       {"type": "string",  "description": "open or closed (default: open)"},
                    "max_results": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": [],
            },
        },
    },
    handler=_github_my_issues,
    thinking_label="Checking your GitHub issues…",
    terminal=True,
    help_hint="lists issues assigned to the user",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "github_profile",
            "description": (
                "Get the user's GitHub profile summary: repo count, followers, account age. "
                "Use for 'my GitHub profile', 'how many repos do I have', 'my GitHub stats'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    handler=_github_profile,
    thinking_label="Loading your GitHub profile…",
    terminal=True,
    help_hint="returns user's own GitHub profile stats",
))


# ---------------------------------------------------------------------------
# List commits on a branch/repo (always uses correct owner prefix)
# ---------------------------------------------------------------------------

async def _github_list_commits(args: dict[str, Any]) -> str:
    repo   = str(args.get("repo", "")).strip()
    branch = str(args.get("branch", "") or "").strip()  # empty = repo's default branch
    max_r  = min(int(args.get("max_results", 10) or 10), 30)
    if not repo:
        return "Provide a repo name."
    owner = _username() or "unknown"
    if "/" not in repo:
        repo = f"{owner}/{repo}"
    params: dict[str, Any] = {"per_page": max_r}
    if branch:
        params["sha"] = branch
    try:
        commits = await _gh_get(f"/repos/{repo}/commits", params)
        if not commits:
            return f"No commits found on {repo}."
        label = branch or "default branch"
        lines = [f"Last {len(commits)} commit(s) on {repo} ({label}):"]
        for c in commits:
            sha_short = c.get("sha", "")[:7]
            msg    = (c.get("commit", {}).get("message") or "").splitlines()[0][:80]
            author = c.get("commit", {}).get("author", {}).get("name", "?")
            date   = (c.get("commit", {}).get("author", {}).get("date") or "")[:10]
            lines.append(f"• {sha_short} [{date}] {author}: {msg}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("github_list_commits failed")
        return f"GitHub error: {exc}"


# ---------------------------------------------------------------------------
# Get repo info (always uses correct owner prefix)
# ---------------------------------------------------------------------------

async def _github_get_repo(args: dict[str, Any]) -> str:
    repo = str(args.get("repo", "")).strip()
    if not repo:
        return "Provide a repo name."
    owner = _username() or "unknown"
    if "/" not in repo:
        repo = f"{owner}/{repo}"
    try:
        r = await _gh_get(f"/repos/{repo}")
        lines = [
            f"Repo: {r.get('full_name')}",
            f"Description: {r.get('description') or '—'}",
            f"Language: {r.get('language') or '—'}  |  Stars: {r.get('stargazers_count', 0)}  |  Forks: {r.get('forks_count', 0)}",
            f"Default branch: {r.get('default_branch', 'main')}",
            f"Open issues: {r.get('open_issues_count', 0)}",
            f"Created: {str(r.get('created_at',''))[:10]}  |  Updated: {str(r.get('updated_at',''))[:10]}",
            f"URL: {r.get('html_url', '')}",
        ]
        if r.get("topics"):
            lines.append(f"Topics: {', '.join(r['topics'])}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("github_get_repo failed")
        return f"GitHub error: {exc}"


# ---------------------------------------------------------------------------
# PR review helpers
# ---------------------------------------------------------------------------

async def _fetch_both(client: httpx.AsyncClient, path1: str, path2: str) -> tuple[Any, Any]:
    import asyncio
    r1, r2 = await asyncio.gather(
        client.get(f"{_BASE}{path1}", headers=_headers()),
        client.get(f"{_BASE}{path2}", headers=_headers()),
    )
    r1.raise_for_status()
    r2.raise_for_status()
    return r1.json(), r2.json()


async def _build_pr_payload(repo: str, pr_number: str) -> tuple[str, str, str]:
    """Fetch PR metadata + diff and return (pr_text_for_llm, title, base_branch)."""
    async with httpx.AsyncClient(timeout=20) as client:
        pr, files = await _fetch_both(
            client,
            f"/repos/{repo}/pulls/{pr_number}",
            f"/repos/{repo}/pulls/{pr_number}/files",
        )
    title     = pr.get("title", "")
    body      = pr.get("body") or "(no description)"
    author    = pr.get("user", {}).get("login", "?")
    base      = pr.get("base", {}).get("ref", "main")
    head      = pr.get("head", {}).get("ref", "?")
    changed   = pr.get("changed_files", len(files))
    additions = pr.get("additions", 0)
    deletions = pr.get("deletions", 0)

    diff_parts: list[str] = []
    for f in files:
        diff_parts.append(f"### {f.get('filename', '')}  [{f.get('status', '')}]")
        diff_parts.append(f.get("patch") or "(binary or too large)")
    diff_text = "\n".join(diff_parts)
    if len(diff_text) > 12_000:
        diff_text = diff_text[:12_000] + "\n\n… (diff truncated)"

    pr_text = (
        f"PR #{pr_number}: {title}\n"
        f"Author: {author}  |  {head} → {base}\n"
        f"Files changed: {changed}  (+{additions} / -{deletions})\n\n"
        f"Description:\n{body[:1000]}\n\n"
        f"Diff:\n{diff_text}"
    )
    return pr_text, title, base


async def _llm_call(system_prompt: str, user_text: str, max_tokens: int = 2000) -> str:
    api_key  = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model    = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    payload  = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_text},
        ],
        "stream": False,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{base_url}/v1/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
    return (resp.json().get("choices", [{}])[0].get("message") or {}).get("content", "")


def _parse_verdict(review_text: str) -> str:
    """Map review text verdict to GitHub review event type."""
    upper = review_text.upper()
    if "REQUEST CHANGES" in upper:
        return "REQUEST_CHANGES"
    if "APPROVE" in upper:
        return "APPROVE"
    return "COMMENT"


# ---------------------------------------------------------------------------
# PR review — brief summary shown to the user
# ---------------------------------------------------------------------------

async def _github_review_pr(args: dict[str, Any]) -> str:
    repo      = str(args.get("repo", "")).strip()
    pr_number = str(args.get("pr_number", "")).strip()
    owner     = str(args.get("owner", "")).strip() or _username()

    if not repo or not pr_number:
        return "Provide both repo and pr_number."
    if "/" not in repo:
        repo = f"{owner}/{repo}"
    try:
        pr_text, title, _ = await _build_pr_payload(repo, pr_number)
        summary = await _llm_call(_PR_SUMMARY_PROMPT, pr_text, max_tokens=600)
        return (
            f"PR #{pr_number} — {title}\n\n"
            f"{summary}\n\n"
            f"(This is your summary. Say 'post the review' to send the full detailed version to the developer on GitHub.)"
        )
    except Exception as exc:
        logger.exception("github_review_pr failed")
        return f"GitHub PR review error: {exc}"


# ---------------------------------------------------------------------------
# Post detailed PR review to GitHub
# ---------------------------------------------------------------------------

async def _github_post_pr_review(args: dict[str, Any]) -> str:
    repo      = str(args.get("repo", "")).strip()
    pr_number = str(args.get("pr_number", "")).strip()
    owner     = str(args.get("owner", "")).strip() or _username()

    if not repo or not pr_number:
        return "Provide both repo and pr_number."
    if "/" not in repo:
        repo = f"{owner}/{repo}"
    try:
        pr_text, title, _ = await _build_pr_payload(repo, pr_number)
        detailed = await _llm_call(_PR_REVIEW_PROMPT, pr_text, max_tokens=3000)
        event    = _parse_verdict(detailed)

        async with httpx.AsyncClient(timeout=20) as client:
            # Try official PR review first (requires collaborator/write access)
            review_resp = await client.post(
                f"{_BASE}/repos/{repo}/pulls/{pr_number}/reviews",
                headers=_headers(),
                json={"body": detailed, "event": event},
            )

            if review_resp.status_code == 422:
                # PR may be merged/closed, or token lacks write access — fall back to issue comment
                logger.warning(
                    "PR reviews API 422 for %s #%s (merged or no write access) — falling back to comment",
                    repo, pr_number,
                )
                comment_resp = await client.post(
                    f"{_BASE}/repos/{repo}/issues/{pr_number}/comments",
                    headers=_headers(),
                    json={"body": f"**Code Review**\n\n{detailed}"},
                )
                comment_resp.raise_for_status()
                return f"Review posted as a comment on PR #{pr_number} ({title}) — the developer can see it now."

            review_resp.raise_for_status()

        verdict_label = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES REQUESTED"}.get(event, "COMMENTED")
        return f"Review posted to PR #{pr_number} ({title}) — verdict: {verdict_label}."
    except Exception as exc:
        logger.exception("github_post_pr_review failed")
        return f"GitHub post review error: {exc}"


# ---------------------------------------------------------------------------
# Registry — new tools
# ---------------------------------------------------------------------------

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "github_list_commits",
            "description": (
                "List recent commits on a GitHub repository. "
                "Branch is OPTIONAL — leave it empty and the repo's default branch is used automatically. "
                "Do NOT call github_get_repo first to discover the branch — just call this directly. "
                "Use for 'show commits on jarvis', 'how many commits', 'commit history'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo":        {"type": "string",  "description": "Repo name (e.g. 'jarvis' or 'owner/jarvis')"},
                    "branch":      {"type": "string",  "description": "Branch name — leave empty to use the repo's default branch"},
                    "max_results": {"type": "integer", "description": "Max commits to return (default 10)"},
                },
                "required": ["repo"],
            },
        },
    },
    handler=_github_list_commits,
    thinking_label="Fetching commits…",
    terminal=True,
    help_hint="lists commits on a repo branch, auto-prepends username",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "github_get_repo",
            "description": (
                "Get details about a GitHub repository: description, language, stars, open issues. "
                "Use for 'tell me about the jarvis repo', 'what language is repo X', 'repo stats'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repo name (e.g. 'jarvis' or 'owner/repo')"},
                },
                "required": ["repo"],
            },
        },
    },
    handler=_github_get_repo,
    thinking_label="Fetching repo info…",
    terminal=False,
    help_hint="returns repo metadata, auto-prepends username",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "github_review_pr",
            "description": (
                "READ-ONLY: Fetch a pull request diff and analyse it locally like a senior developer. "
                "Checks security (hardcoded secrets/env exposure), architecture, code quality, linting, "
                "test coverage, and deployment risks. Returns APPROVE / REQUEST CHANGES / NEEDS DISCUSSION verdict. "
                "This tool does NOT post anything to GitHub — it only returns the analysis to the user. "
                "Use for 'review PR #5', 'check the open PR', 'senior dev review of pull request'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo":      {"type": "string",  "description": "Repo name (e.g. 'jarvis' or 'owner/jarvis')"},
                    "pr_number": {"type": "integer", "description": "Pull request number"},
                    "owner":     {"type": "string",  "description": "Repo owner (default: your GitHub username)"},
                },
                "required": ["repo", "pr_number"],
            },
        },
    },
    handler=_github_review_pr,
    thinking_label="Reviewing pull request…",
    terminal=True,
    help_hint="senior-dev PR review: security, arch, linting, deployment",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "github_post_pr_review",
            "description": (
                "Generate a full detailed senior-developer review and POST it as an official review "
                "on a GitHub pull request. The developer will see it. Automatically sets verdict to "
                "APPROVE, REQUEST_CHANGES, or COMMENT based on findings. "
                "Use when the user says 'post the review', 'send it to the developer', 'submit the review'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo":      {"type": "string",  "description": "Repo name (e.g. 'edu-phoria' or 'owner/edu-phoria')"},
                    "pr_number": {"type": "integer", "description": "Pull request number"},
                    "owner":     {"type": "string",  "description": "Repo owner (default: your GitHub username)"},
                },
                "required": ["repo", "pr_number"],
            },
        },
    },
    handler=_github_post_pr_review,
    thinking_label="Posting PR review to GitHub…",
    terminal=True,
    help_hint="generates detailed review and posts it to GitHub PR",
))
