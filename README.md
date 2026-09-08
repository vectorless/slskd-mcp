# slskd-mcp

> **⚠️ Work in progress.** Works against the live Soulseek network, but APIs will
> change without warning. No releases, no stability promises.

An [MCP](https://modelcontextprotocol.io) server that gives an AI agent structured access to
the [Soulseek](https://www.slsknet.org/) network, via a local
[slskd](https://github.com/slskd/slskd) daemon.

Searching Soulseek by hand is fine. Searching it *systematically* — say, working through a list
of three hundred record labels and asking which have anything shared in lossless — is exactly
the sort of tedious, repetitive lookup an agent should do for you. That's what this is for.

Two crates:

- **`slskd-mcp`** — the MCP server. 11 tools.
- **`slskd-client`** — a small typed HTTP client for the slskd API, usable on its own.

## What makes it more than an API wrapper

**A label filter that actually works.** Soulseek search is a plain substring match over the
whole path, so searching `Planet Rhythm` returns Leftfield's *Phat Planet* from *Rhythm &
Stealth*, plus a pile of *Strictly Rhythm* house records. The words match; the label doesn't.

Releases are near-universally foldered with the label as a bracketed tag —
`Artist - Title EP [Planet Rhythm]` — so `search_label` keeps only paths carrying that tag.
Measured against the live network:

```
plain search:   250 responders, 1,912 files
label filter:    29 responders,   846 files
+ lossless:       5 responders,   180 files
```

Matching uses a normalised form (lowercase, alphanumeric only), so `[planet rhythm ]`,
`[PLANET-RHYTHM]` and `[Planet Rhythm Records]` all match while `[Strictly Rhythm]` does not.

**A wishlist that stays quiet.** Standing searches re-run and report *only* results not seen
before. Soulseek is a live network of people's hard drives — the record you want may simply not
be shared today and may appear next week. A watcher that reported the same eighty-five files
every morning would be muted within a week, so it doesn't.

**Format filtering** with `lossless` / `lossy` shorthands, because "is this on the network in
FLAC" is the question that usually matters.

## Tools

| tool | kind | what it does |
|---|---|---|
| `search` | read | start a search, return its id |
| `search_results` | read | results for an id, optional format filter |
| `search_label` | read | search a label, keep only genuine releases on it |
| `searches` | read | list recent searches |
| `browse` | read | browse a user's shares |
| `downloads` | read | current transfer state |
| `wishlist_add` | read | add a standing search |
| `wishlist_list` | read | show the wishlist |
| `wishlist_remove` | read | drop an entry |
| `wishlist_check` | read | run all entries, report only what's new |
| `download` | **write** | queue a transfer — **disabled by default** |

### Downloads are gated on purpose

`download` writes files to disk and generates traffic on a P2P network under the operator's
account. That is categorically different from every read tool, so the server refuses it unless
started with `--allow-downloads` (or `SLSKD_ALLOW_DOWNLOADS=1`).

Handing an agent the ability to pull files is a decision someone should make deliberately, not
a default nobody reviewed.

## Usage

```
export SLSKD_URL=http://localhost:5030
export SLSKD_API_KEY=…            # from slskd.yml → web.authentication.api_keys

slskd-mcp                         # stdio MCP server, downloads disabled
slskd-mcp --allow-downloads       # opt in to queuing transfers
slskd-mcp --wishlist-run          # cron mode: prints new results, silent otherwise
```

An API key in slskd's `slskd.yml`:

```yaml
web:
  authentication:
    api_keys:
      mcp:
        key: <16-255 chars>
        role: administrator
        cidr: 127.0.0.1/32,::1/128
```

Wishlist state lives at `~/slskd/wishlist.json`, overridable with `SLSKD_WISHLIST`.

## Build

```
cargo build
cargo test          # 15 tests, no network required
```

`reqwest` uses `rustls-tls` rather than native TLS — no OpenSSL, no `pkg-config`, no system
dependencies. Deliberate; please don't switch it back.

## Notes on slskd's OpenAPI spec

Four findings, recorded so they don't cost anyone else the time they cost here:

1. **The spec is at `/swagger/v0/swagger.json`** — `v0`, not `v1`. Enable the `swagger` feature.
2. **14 paths contain a literal `v{version}` placeholder** — the ASP.NET route template isn't
   substituted during generation. It affects `searches` and `transfers`, so a generated client
   emits URLs that 404. They resolve to `v0` at runtime.
3. **`components.securitySchemes` is empty**, so a generated client won't attach `X-API-Key`
   or a bearer token.
4. **The search endpoints declare no response schemas at all.**

The types here were therefore modelled from slskd's own C# source
(`src/slskd/Search/Types/{Search,Response}.cs`) and then verified against live payloads —
every field deserialised without error. Hand-writing the client was less work than fighting
codegen. None of this is a complaint about slskd, which is excellent; it's just what's true of
the generated spec.

## Background: why there's no browser client

This began as "can Soulseek be rewritten in WebAssembly and run in the browser?" It can't, and
the reason is worth recording.

**WebAssembly has no syscalls of its own.** It inherits whatever the host offers, and in a
browser that host is the JS sandbox: fetch, WebSocket, WebRTC, WebTransport — never a raw TCP
socket. Soulseek needs raw TCP twice: to the server (`server.slsknet.org:2242`, a bespoke
binary protocol) and directly between peers for searches, browsing and every transfer.

The peer half is decisive. Browsers cannot accept inbound connections, and the existing client
population speaks TCP rather than WebRTC — a WebRTC mesh would be a new network with no users.

So a browser client needs a local process holding the sockets, which is what slskd already is.
A web front-end is **parked**, not planned; `slskd-client` is a plain HTTP client with no MCP
dependencies and compiles for `wasm32`, so it stays available if that ever changes.

## Roadmap

- [ ] Group results by release folder rather than listing files
- [ ] Typed models for `browse` and `downloads` (currently raw JSON)
- [ ] Better handling of short, generic label names, which collide with unrelated tags

## Licence

[MIT](LICENSE).

Note that this project only *talks to* [slskd](https://github.com/slskd/slskd), which is
AGPL-3.0. Nothing from slskd is vendored or linked here — `slskd-client` is an independent
HTTP client speaking to its public API over the network.
