"""Shell and web search tools."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


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


async def web_search(args: dict) -> str:
    """Search the web using DuckDuckGo (no API key needed)."""
    import urllib.parse
    import urllib.request

    query = args.get("query", "")
    if not query:
        return "Error: No search query provided"

    def _do_search() -> str:
        try:
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) PolyglotAI/0.2"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            import re
            results = []
            for m in re.finditer(
                r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                html, re.DOTALL,
            ):
                link = m.group(1)
                title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                snippet = re.sub(r"<[^>]+>", "", m.group(3)).strip()
                if title and snippet:
                    results.append(f"**{title}**\n{link}\n{snippet}")
                if len(results) >= 5:
                    break

            if not results:
                return f"No results found for: {query}"
            return f"Web search results for: {query}\n\n" + "\n\n---\n\n".join(results)
        except Exception as e:
            return f"Search error: {e}"

    return await asyncio.to_thread(_do_search)
