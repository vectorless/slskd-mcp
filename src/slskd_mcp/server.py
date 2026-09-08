"""MCP server exposing a local slskd instance to an AI agent.

Reads are always available. ``download`` actually queues a transfer -- it writes
files to disk and generates traffic on a P2P network under the operator's
account -- so it is refused unless downloads are explicitly enabled with
``--allow-downloads`` or ``SLSKD_ALLOW_DOWNLOADS=1``.

  SLSKD_URL      default http://localhost:5030
  SLSKD_API_KEY  required
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp.server.mcpserver import MCPServer

from .client import Client, SlskdError
from .files import describe, peer_line, rank_key, size_human
from .filter import has_label_tag, matches_format
from .wishlist import Entry, Wishlist, now_stamp

mcp = MCPServer(
    name="slskd",
    instructions=(
        "Search and browse the Soulseek network through a local slskd daemon. "
        "Searches are asynchronous: `search` returns an id, and results arrive "
        "over the following few seconds. Prefer `search_label` when looking for "
        "releases on a particular record label -- it filters out the "
        "coincidental word matches that plain search returns in bulk."
    ),
)

# Created lazily on first use so the AsyncClient belongs to the running loop.
_client: Client | None = None
_allow_downloads = False

# Responses trickle in, so poll rather than guessing a single sleep.
_POLL_ROUNDS = 8
_POLL_INTERVAL = 2.0


def client() -> Client:
    global _client
    if _client is None:
        _client = Client.from_env()
    return _client


async def _await_responses(search_id: str) -> list[dict]:
    """Poll until the search completes or we run out of patience."""
    responses: list[dict] = []
    for _ in range(_POLL_ROUNDS):
        await asyncio.sleep(_POLL_INTERVAL)
        r = await client().search_responses(search_id)
        if r:
            responses = r
            state = await client().search(search_id)
            if "Completed" in (state.get("state") or ""):
                break
    return responses


async def _run_query(
    query: str, label_filter: bool, fmt: str
) -> list[tuple[str, list[dict]]]:
    """Run one query and return matching files per peer.

    Shared by `search_label` and the wishlist so both behave identically.
    """
    search = await client().start_search(query)
    responses = await _await_responses(search["id"])

    out: list[tuple[str, list[dict]]] = []
    for r in responses:
        files = [
            f
            for f in r.get("files", [])
            if (not label_filter or has_label_tag(f.get("filename", ""), query))
            and matches_format(f.get("filename", ""), f.get("extension"), fmt)
        ]
        if files:
            out.append((r.get("username", "?"), files))
    return out


@mcp.tool(
    description=(
        "Search the Soulseek network. Returns a search id; results arrive "
        "asynchronously, so call search_results a few seconds later."
    )
)
async def search(query: str) -> str:
    """Start a search.

    Args:
        query: What to search the Soulseek network for, e.g. "Planet Rhythm"
    """
    try:
        s = await client().start_search(query)
    except SlskdError as e:
        return f"Search failed: {e}"
    return (
        f"Search started.\nid: {s['id']}\nquery: {query}\n\n"
        "Wait ~5-10s then call search_results with this id."
    )


@mcp.tool(
    description=(
        "Get results for a search id. Lists peers and their files with size and "
        "bitrate. Use the exact filename and size when downloading."
    )
)
async def search_results(id: str, formats: str = "") -> str:
    """Fetch results for a search id.

    Args:
        id: The search id returned by `search`
        formats: Optional format filter. Extensions and/or the shorthands
            "lossless" and "lossy": e.g. "flac", "flac,wav". Omit for everything.
    """
    try:
        responses = await client().search_responses(id)
    except SlskdError as e:
        return f"Could not fetch results: {e}"
    if not responses:
        return "No responses yet. Searches take a few seconds; try again shortly."

    fmt = (formats or "").strip()
    if fmt:
        kept = []
        for r in responses:
            files = [
                f
                for f in r.get("files", [])
                if matches_format(f.get("filename", ""), f.get("extension"), fmt)
            ]
            if files:
                kept.append({**r, "files": files})
        responses = kept
        if not responses:
            return f'No files matching format "{fmt}" in this search.'

    responses.sort(key=rank_key)
    suffix = f" (filtered to {fmt})" if fmt else ""
    out = [f"{len(responses)} peer(s) responded{suffix}."]
    for r in responses[:15]:
        out.append(peer_line(r))
        files = r.get("files", [])
        out.extend(describe(f) for f in files[:8])
        if len(files) > 8:
            out.append(f"   … and {len(files) - 8} more")
    return "\n".join(out)


@mcp.tool(
    description=(
        "Find releases on a specific record label. Runs a search, waits for "
        "responses, then keeps only files whose folder carries the label as a "
        'bracketed tag (e.g. "... [Planet Rhythm]"). Filters out coincidental '
        "word matches, which plain search returns in bulk."
    )
)
async def search_label(label: str, formats: str = "") -> str:
    """Search for a label and keep only genuine releases on it.

    Args:
        label: Record label name, e.g. "Planet Rhythm"
        formats: Optional format filter, e.g. "flac", "flac,wav", "lossless".
    """
    fmt = (formats or "").strip()
    try:
        search_state = await client().start_search(label)
        responses = await _await_responses(search_state["id"])
    except SlskdError as e:
        return f"Search failed: {e}"

    total = sum(len(r.get("files", [])) for r in responses)
    hits = []
    for r in responses:
        matching = [
            f
            for f in r.get("files", [])
            if has_label_tag(f.get("filename", ""), label)
            and matches_format(f.get("filename", ""), f.get("extension"), fmt)
        ]
        if matching:
            hits.append((r, matching))

    if not hits:
        note = f" ({fmt})" if fmt else ""
        return (
            f'Searched "{label}"{note}: {len(responses)} peers, {total} files, but '
            "nothing matched. Either nobody shares this label, it is tagged "
            "differently, or the format filter excluded everything."
        )

    hits.sort(key=lambda h: rank_key(h[0]))
    kept = sum(len(f) for _, f in hits)
    note = f" [{fmt}]" if fmt else ""
    out = [
        f'"{label}"{note} — {kept} matching file(s) from {len(hits)} peer(s), '
        f"filtered down from {total} raw results across {len(responses)} responders."
    ]
    for r, files in hits[:12]:
        out.append(peer_line(r, file_count=len(files)))
        out.extend(describe(f) for f in files[:10])
        if len(files) > 10:
            out.append(f"   … and {len(files) - 10} more")
    return "\n".join(out)


@mcp.tool(
    description=(
        "Add a standing search to the wishlist. Wishlist entries are re-run on a "
        "schedule and only NEW results are reported, so you hear about a record "
        "the week it finally appears on the network."
    )
)
async def wishlist_add(
    query: str, label_filter: bool = False, formats: str | None = None
) -> str:
    """Add a standing search.

    Args:
        query: Search text, or a label name if label_filter is true
        label_filter: Apply the bracketed-label-tag filter instead of a plain search
        formats: Optional format spec, e.g. "lossless" or "flac,wav"
    """
    w = Wishlist.load()
    if not w.add(Entry(query, label_filter, formats, now_stamp())):
        return f'"{query}" is already on the wishlist with those settings.'
    try:
        w.save()
    except OSError as e:
        return f"Could not save wishlist: {e}"
    extra = " (label filter)" if label_filter else ""
    extra += f" [{formats}]" if formats else ""
    return f'Added "{query}" to the wishlist{extra}. {len(w.entries)} entr(ies) total.'


@mcp.tool(description="List standing wishlist searches")
async def wishlist_list() -> str:
    """Show the wishlist."""
    w = Wishlist.load()
    if not w.entries:
        return "Wishlist is empty."
    out = [
        f"{len(w.entries)} wishlist entr(ies), {len(w.seen)} result(s) already reported:"
    ]
    for i, e in enumerate(w.entries, 1):
        bits = "  (label)" if e.label_filter else ""
        bits += f"  [{e.formats}]" if e.formats else ""
        out.append(f"{i}. {e.query}{bits}")
    return "\n".join(out)


@mcp.tool(description="Remove a wishlist entry by its 1-based index from wishlist_list")
async def wishlist_remove(index: int) -> str:
    """Remove an entry.

    Args:
        index: 1-based position as shown by wishlist_list
    """
    w = Wishlist.load()
    e = w.remove(index)
    if e is None:
        return f"No entry {index}. Use wishlist_list to see the numbering."
    try:
        w.save()
    except OSError as err:
        return f"Removed in memory but could not save: {err}"
    return f'Removed "{e.query}". {len(w.entries)} left.'


@mcp.tool(
    description=(
        "Run all wishlist searches now and report only results not seen before. "
        "Takes ~20s per entry because Soulseek results arrive asynchronously."
    )
)
async def wishlist_check() -> str:
    """Run every wishlist entry and report only what hasn't been seen before."""
    w = Wishlist.load()
    if not w.entries:
        return "Wishlist is empty — add something with wishlist_add."

    report: list[str] = []
    total_new = 0
    for e in list(w.entries):
        try:
            hits = await _run_query(e.query, e.label_filter, e.formats or "")
        except SlskdError as err:
            report.append(f"\n{e.query}: search failed — {err}")
            continue

        fresh = []
        for user, files in hits:
            new_files = [f for f in files if w.is_new(f.get("filename", ""))]
            if new_files:
                fresh.append((user, new_files))
        if not fresh:
            continue

        n = sum(len(f) for _, f in fresh)
        total_new += n
        report.append(f"\n=== {e.query} — {n} new file(s)")
        for user, files in fresh[:5]:
            report.append(f"{user}:")
            report.extend(
                f"   {f.get('filename', '?')} [{size_human(f.get('size'))}]"
                for f in files[:6]
            )
        w.mark_seen(f.get("filename", "") for _, files in fresh for f in files)

    try:
        w.save()
    except OSError as err:
        report.append(f"\n(warning: wishlist not saved: {err})")

    if total_new == 0:
        return f"Checked {len(w.entries)} wishlist entr(ies). Nothing new."
    body = "\n".join(report)
    return f"{total_new} new result(s) across {len(w.entries)} entr(ies).\n{body}"


@mcp.tool(description="List recent searches and their state")
async def searches() -> str:
    """All searches slskd currently knows about."""
    try:
        items = await client().searches()
    except SlskdError as e:
        return f"Failed: {e}"
    if not items:
        return "No searches."
    return "\n".join(
        f"{s.get('id')}  {s.get('searchText', '?')}  "
        f"responses={s.get('responseCount', 0)} files={s.get('fileCount', 0)} "
        f"{s.get('state', '')}"
        for s in items
    )


@mcp.tool(description="Browse everything a Soulseek user is sharing")
async def browse(username: str) -> str:
    """Browse a user's shared files.

    Args:
        username: Soulseek username
    """
    try:
        v = await client().browse(username)
    except SlskdError as e:
        return f"Browse failed: {e}"
    return json.dumps(v, indent=2)[:4000]


@mcp.tool(description="Show current downloads and their progress")
async def downloads() -> str:
    """Current download state."""
    try:
        v = await client().downloads()
    except Exception as e:
        return f"Failed: {e}"
    if not v:
        return "No downloads."
    return json.dumps(v, indent=2)[:4000]


@mcp.tool(
    description=(
        "Queue a file for download. Requires the server to have been started "
        "with downloads enabled. Use the exact filename and size from "
        "search_results."
    )
)
async def download(username: str, filename: str, size: int) -> str:
    """Queue a download. Gated.

    Args:
        username: Soulseek username holding the file
        filename: Full remote path, exactly as returned by `search_results`
        size: File size in bytes, exactly as returned by `search_results`
    """
    if not _allow_downloads:
        return (
            "Downloads are disabled on this MCP server. Restart it with "
            "--allow-downloads (or SLSKD_ALLOW_DOWNLOADS=1) if the operator "
            "wants to permit this."
        )
    try:
        await client().enqueue(username, [{"filename": filename, "size": size}])
    except SlskdError as e:
        return f"Enqueue failed: {e}"
    return f"Queued: {filename}\nfrom {username}\nTrack it with `downloads`."


async def _probe() -> bool:
    """Startup liveness check; closes the client so main() starts clean."""
    c = client()
    try:
        return await c.health()
    finally:
        global _client
        await c.aclose()
        _client = None


def main() -> None:
    global _allow_downloads
    _allow_downloads = "--allow-downloads" in sys.argv or (
        os.environ.get("SLSKD_ALLOW_DOWNLOADS") == "1"
    )

    # Non-MCP mode for scheduled runs: check the wishlist, print anything new, exit.
    if "--wishlist-run" in sys.argv:
        _allow_downloads = False
        out = asyncio.run(wishlist_check())
        if not out.startswith(("Checked", "Wishlist is empty")):
            print(out)
        return

    # Fail loudly at startup rather than on the first tool call.
    try:
        if not asyncio.run(_probe()):
            print(
                "warning: slskd did not answer /health; tools will fail until it is up",
                file=sys.stderr,
            )
    except SlskdError as e:
        print(f"warning: {e}", file=sys.stderr)

    state = "ENABLED" if _allow_downloads else "disabled"
    print(f"slskd-mcp starting (downloads {state})", file=sys.stderr)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
