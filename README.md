# soulseek-wasm

> **⚠️ Work in progress.** The MCP server works against the live network. The web
> front-end is not started. APIs will change without warning. No releases, no
> stability promises.

Rust tooling for [Soulseek](https://www.slsknet.org/), built on top of
[slskd](https://github.com/slskd/slskd):

- **`slskd-client`** — a small typed HTTP client for the slskd API
- **`slskd-mcp`** — an [MCP](https://modelcontextprotocol.io) server exposing Soulseek
  search to AI agents, with a label filter, a format filter and a wishlist
- **`web/`** *(not started)* — a Rust → WebAssembly browser front-end

## Why not "Soulseek compiled to WASM"?

The project began as "can Soulseek be rewritten in WebAssembly and run in the browser?"
It can't, and the reason is worth writing down.

**WebAssembly has no syscalls of its own.** It inherits whatever the host provides, and in a
browser that host is the JS sandbox: fetch, WebSocket, WebRTC, WebTransport. Never a raw TCP
socket. Soulseek needs raw TCP twice:

- **client → server** — `server.slsknet.org:2242`, a bespoke binary protocol
- **peer → peer** — searches, browsing and every file transfer are direct TCP between clients

The peer half is decisive. Browsers cannot accept inbound connections, and the existing client
population speaks TCP rather than WebRTC — a WebRTC mesh would be a new network with no users.

So a browser client needs a local process holding the sockets. Rather than write one, this
project talks to **slskd**, which already implements the protocol properly and exposes an HTTP
API. A hand-written protocol crate remains a possible future direction, but it isn't this.

## Status

| | |
|---|---|
| `slskd-client` | working — verified against a live slskd instance |
| `slskd-mcp` | working — 11 tools, tested end to end on the live network |
| Wishlist | working — standing searches, only new results reported |
| `web/` front-end | **not started** |

## MCP tools

| tool | kind | what it does |
|---|---|---|
| `search` | read | start a search, return its id |
| `search_results` | read | results for an id, optional format filter |
| `search_label` | read | search a label and keep only genuine releases on it |
| `searches` | read | list recent searches |
| `browse` | read | browse a user's shares |
| `downloads` | read | current transfer state |
| `wishlist_add` / `_list` / `_remove` / `_check` | read | standing searches |
| `download` | **write** | queue a transfer — **disabled by default** |

### Downloads are gated deliberately

`download` writes files to disk and generates traffic on a P2P network under the operator's
account. It is categorically different from the read tools, so the server refuses it unless
started with `--allow-downloads` (or `SLSKD_ALLOW_DOWNLOADS=1`). Enabling it should be a
decision someone made on purpose, not a default nobody reviewed.

## The label filter

Soulseek search is a plain substring match over the whole path. Searching `Planet Rhythm`
returns Leftfield's *Phat Planet* from *Rhythm & Stealth*, and a pile of *Strictly Rhythm*
house records — the words match, the label doesn't.

Releases are near-universally foldered with the label as a bracketed tag —
`Artist - Title EP [Planet Rhythm]` — so `search_label` keeps only paths carrying that tag.
Measured on the live network:

```
plain search:   250 responders, 1,912 files
label filter:    29 responders,   846 files
+ lossless:       5 responders,   180 files
```

Matching is done on a normalised form (lowercase, alphanumeric only) so `[planet rhythm ]`,
`[PLANET-RHYTHM]` and `[Planet Rhythm Records]` all match, while `[Strictly Rhythm]` does not.

## Format filter

`formats` accepts extensions, comma lists, or the shorthands `lossless`
(flac, wav, aiff, aif, ape, alac, wv) and `lossy` (mp3, m4a, aac, ogg, opus, wma):

```json
{ "label": "Planet Rhythm", "formats": "lossless" }
{ "id": "…", "formats": "flac,wav" }
```

The filename suffix is the source of truth — slskd's `extension` field is frequently empty.

## Wishlist

Standing searches that re-run and report **only what hasn't been seen before**, so a scheduled
run is silent unless something genuinely new appeared. Soulseek is a live network of people's
hard drives; the record you want may simply not be shared today.

```
slskd-mcp --wishlist-run     # prints new results, silent otherwise — cron-shaped
```

Stored at `~/slskd/wishlist.json`, overridable with `SLSKD_WISHLIST`.

## Notes on slskd's OpenAPI spec

Three findings that cost time, recorded so they don't cost anyone else any:

1. **The spec is at `/swagger/v0/swagger.json`** — `v0`, not `v1`. Enable the `swagger` feature.
2. **14 paths contain a literal `v{version}` placeholder** — the ASP.NET route template isn't
   substituted during generation. It affects `searches` and `transfers`, so generated clients
   emit URLs that 404. They resolve to `v0` at runtime.
3. **`components.securitySchemes` is empty**, so a generated client won't attach `X-API-Key`
   or a bearer token.

Additionally, **the search endpoints declare no response schemas at all**. The types in
`slskd-client` were therefore modelled from slskd's own C# source
(`src/slskd/Search/Types/{Search,Response}.cs`) and then verified against live payloads. Every
field deserialised without error.

Given all that, hand-writing the client was less work than fighting codegen.

## Build

Needs a Rust toolchain and a running slskd instance.

```
cargo build
cargo test          # 15 tests, no network required
```

`reqwest` is configured with `rustls-tls` rather than native TLS — no OpenSSL, no
`pkg-config`, no system dependencies, and it cross-compiles cleanly for `wasm32`. This is
deliberate; please don't switch it back.

## Run

```
export SLSKD_URL=http://localhost:5030
export SLSKD_API_KEY=…            # from slskd.yml → web.authentication.api_keys
slskd-mcp                         # stdio MCP server, downloads disabled
slskd-mcp --allow-downloads       # opt in to queuing transfers
```

Configure an API key in slskd's `slskd.yml`:

```yaml
web:
  authentication:
    api_keys:
      mcp:
        key: <16-255 chars>
        role: administrator
        cidr: 127.0.0.1/32,::1/128
```

## Roadmap

- [ ] Group results by release folder rather than listing files
- [ ] Typed models for `browse` and `downloads` (currently raw JSON)
- [ ] Web front-end — Dioxus or Leptos, undecided
- [ ] Better handling of short/generic label names, which collide with unrelated tags

## Licence

Not yet chosen. Until one is added, no permissions are granted beyond reading the code.
