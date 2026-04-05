"""Shell and web search tools."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) PolyglotAI/0.2"}


def _is_safe_url(url: str) -> bool:
    """Check that a URL is safe to fetch (no localhost/private IPs)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname or ""
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
    except ValueError:
        pass  # hostname, not IP — fine
    return True


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that validates each redirect target against SSRF checks."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_safe_url(newurl):
            raise urllib.error.URLError(f"Redirect to disallowed URL blocked: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


async def shell_exec(sandbox, args: dict) -> str:
    command = args.get("command", "")
    workdir = args.get("workdir")

    allowed, reason = sandbox.validate_command(command)
    if not allowed:
        return f"Command blocked: {reason}"

    output, returncode = await sandbox.exec_command(command, workdir)
    result = output if output else "(no output)"
    if returncode != 0:
        result += f"\n[exit code: {returncode}]"
    return result


def _extract_real_url(ddg_url: str) -> str:
    """Extract the actual URL from DuckDuckGo redirect links."""
    if "uddg=" in ddg_url:
        parsed = urllib.parse.urlparse(ddg_url)
        params = urllib.parse.parse_qs(parsed.query)
        if "uddg" in params:
            return params["uddg"][0]
    if ddg_url.startswith("//"):
        return "https:" + ddg_url
    return ddg_url


def _fetch_page_text(url: str, max_chars: int = 6000) -> str:
    """Fetch a URL and extract readable text content.

    Validates the URL against SSRF checks before fetching, and uses a
    redirect handler that re-validates each redirect target.
    """
    if not _is_safe_url(url):
        return "(Blocked: URL targets a private/local network)"
    try:
        opener = urllib.request.build_opener(_SafeRedirectHandler)
        req = urllib.request.Request(url, headers=_HEADERS)
        with opener.open(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Remove script, style, nav, header, footer
        html = re.sub(
            r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # Remove HTML tags
        text = re.sub(r"<[^>]+>", " ", html)
        # Clean up whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n", "\n", text)
        text = text.strip()

        if len(text) > max_chars:
            text = text[:max_chars] + "..."
        return text
    except Exception as e:
        return f"(Could not fetch page: {e})"


async def web_search(args: dict) -> str:
    """Search the web using DuckDuckGo and fetch top result content."""
    query = args.get("query", "")
    if not query:
        return "Error: No search query provided"

    def _do_search() -> str:
        try:
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            results = []
            urls = []
            for m in re.finditer(
                r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                html,
                re.DOTALL,
            ):
                link = _extract_real_url(m.group(1))
                title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                snippet = re.sub(r"<[^>]+>", "", m.group(3)).strip()
                if title and snippet:
                    results.append(f"- {title}: {snippet}")
                    urls.append(link)
                if len(results) >= 5:
                    break

            if not results:
                return f"No results found for: {query}"

            # Fetch content from top 1-2 results for actual data
            # Only fetch http/https URLs — reject private/local networks
            fetched_content = []
            for page_url in urls[:2]:
                if not _is_safe_url(page_url):
                    continue
                content = _fetch_page_text(page_url, max_chars=4000)
                if content and not content.startswith("(Could not"):
                    fetched_content.append(f"Content from {page_url}:\n{content}")

            parts = [f"Search results for '{query}':\n"]
            parts.extend(results)
            if fetched_content:
                parts.append("\n--- Page content ---\n")
                parts.extend(fetched_content)

            return "\n".join(parts)
        except Exception as e:
            return f"Search error: {e}"

    return await asyncio.to_thread(_do_search)
