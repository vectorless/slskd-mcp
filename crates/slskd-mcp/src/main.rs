//! MCP server exposing a local slskd instance to an AI agent.
//!
//! Reads are always available. `download` actually queues a transfer — it writes
//! files to disk and generates traffic on a P2P network under the operator's
//! account — so it is refused unless downloads are explicitly enabled with
//! `--allow-downloads` or `SLSKD_ALLOW_DOWNLOADS=1`.
//!
//!   SLSKD_URL      default http://localhost:5030
//!   SLSKD_API_KEY  required

use rmcp::{
    handler::server::wrapper::Parameters, schemars, tool, tool_router, transport::stdio, ServiceExt,
};
use slskd_client::{has_label_tag, matches_format, Client, QueueDownloadRequest};
use std::sync::Arc;

mod wishlist;
use wishlist::{Entry, Wishlist};

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct SearchParams {
    /// What to search the Soulseek network for, e.g. "Planet Rhythm"
    query: String,
}

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct LabelParams {
    /// Record label name, e.g. "Planet Rhythm"
    label: String,
    /// Optional format filter. Extensions and/or the shorthands "lossless" and
    /// "lossy": e.g. "flac", "flac,wav", "lossless". Omit for everything.
    #[serde(default)]
    formats: Option<String>,
}

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct IdParams {
    /// The search id returned by `search`
    id: String,
    /// Optional format filter. Extensions and/or the shorthands "lossless" and
    /// "lossy": e.g. "flac", "flac,wav", "lossless". Omit for everything.
    #[serde(default)]
    formats: Option<String>,
}

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct UserParams {
    /// Soulseek username
    username: String,
}

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct DownloadParams {
    /// Soulseek username holding the file
    username: String,
    /// Full remote path, exactly as returned by `search_results`
    filename: String,
    /// File size in bytes, exactly as returned by `search_results`
    size: i64,
}

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct WishAddParams {
    /// Search text, or a label name if label_filter is true
    query: String,
    /// Apply the bracketed-label-tag filter instead of a plain search
    #[serde(default)]
    label_filter: Option<bool>,
    /// Optional format spec, e.g. "lossless" or "flac,wav"
    #[serde(default)]
    formats: Option<String>,
}

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct WishRemoveParams {
    /// 1-based position as shown by wishlist_list
    index: u32,
}

#[derive(Clone)]
struct Slskd {
    client: Arc<Client>,
    allow_downloads: bool,
}

#[tool_router(server_handler)]
impl Slskd {
    /// Start a search. Returns the id to poll with `search_results`.
    #[tool(description = "Search the Soulseek network. Returns a search id; \
                          results arrive asynchronously, so call search_results \
                          a few seconds later.")]
    async fn search(&self, Parameters(SearchParams { query }): Parameters<SearchParams>) -> String {
        match self.client.start_search(&query).await {
            Ok(s) => format!(
                "Search started.\nid: {}\nquery: {}\n\nWait ~5-10s then call search_results with this id.",
                s.id, query
            ),
            Err(e) => format!("Search failed: {e}"),
        }
    }

    /// Fetch results for a search id.
    #[tool(description = "Get results for a search id. Lists peers and their files \
                          with size and bitrate. Use the exact filename and size \
                          when downloading.")]
    async fn search_results(
        &self,
        Parameters(IdParams { id, formats }): Parameters<IdParams>,
    ) -> String {
        let fmt = formats.unwrap_or_default();
        let responses = match self.client.search_responses(&id).await {
            Ok(r) => r,
            Err(e) => return format!("Could not fetch results: {e}"),
        };
        if responses.is_empty() {
            return "No responses yet. Searches take a few seconds; try again shortly.".into();
        }

        let mut responses = responses;
        if !fmt.trim().is_empty() {
            for r in responses.iter_mut() {
                r.files.retain(|f| matches_format(&f.filename, f.extension.as_deref(), &fmt));
            }
            responses.retain(|r| !r.files.is_empty());
            if responses.is_empty() {
                return format!("No files matching format \"{fmt}\" in this search.");
            }
        }

        let mut out = format!(
            "{} peer(s) responded{}.\n",
            responses.len(),
            if fmt.trim().is_empty() { String::new() } else { format!(" (filtered to {fmt})") }
        );
        // Free upload slot and a short queue means a download that actually starts.
        let mut ranked = responses;
        ranked.sort_by_key(|r| (!r.has_free_upload_slot, r.queue_length));

        for r in ranked.iter().take(15) {
            out.push_str(&format!(
                "\n{} — {} file(s), {}kb/s{}{}\n",
                r.username,
                r.files.len(),
                r.upload_speed / 1024,
                if r.has_free_upload_slot { ", free slot" } else { "" },
                if r.queue_length > 0 { format!(", queue {}", r.queue_length) } else { String::new() },
            ));
            for f in r.files.iter().take(8) {
                let q = f.quality();
                out.push_str(&format!(
                    "   {} [{}{}]\n",
                    f.filename,
                    f.size_human(),
                    if q.is_empty() { String::new() } else { format!(", {q}") },
                ));
            }
            if r.files.len() > 8 {
                out.push_str(&format!("   … and {} more\n", r.files.len() - 8));
            }
        }
        out
    }

    /// Search for a label and keep only genuine releases on it.
    #[tool(description = "Find releases on a specific record label. Runs a search, waits for \
                          responses, then keeps only files whose folder carries the label as a \
                          bracketed tag (e.g. \"... [Planet Rhythm]\"). Filters out coincidental \
                          word matches, which plain search returns in bulk.")]
    async fn search_label(
        &self,
        Parameters(LabelParams { label, formats }): Parameters<LabelParams>,
    ) -> String {
        let fmt = formats.unwrap_or_default();
        let search = match self.client.start_search(&label).await {
            Ok(s) => s,
            Err(e) => return format!("Search failed: {e}"),
        };

        // Responses trickle in; poll rather than guessing a single sleep.
        let mut responses = Vec::new();
        for _ in 0..8 {
            tokio::time::sleep(std::time::Duration::from_secs(2)).await;
            match self.client.search_responses(&search.id).await {
                Ok(r) if !r.is_empty() => {
                    responses = r;
                    if let Ok(s) = self.client.search(&search.id).await {
                        if s.is_complete() {
                            break;
                        }
                    }
                }
                Ok(_) => {}
                Err(e) => return format!("Could not fetch results: {e}"),
            }
        }

        let total: usize = responses.iter().map(|r| r.files.len()).sum();
        let mut hits = Vec::new();
        for r in &responses {
            let matching: Vec<_> = r
                .files
                .iter()
                .filter(|f| has_label_tag(&f.filename, &label))
                .filter(|f| matches_format(&f.filename, f.extension.as_deref(), &fmt))
                .collect();
            if !matching.is_empty() {
                hits.push((r, matching));
            }
        }

        if hits.is_empty() {
            return format!(
                "Searched \"{label}\"{}: {} peers, {total} files, but nothing matched. Either \
                 nobody shares this label, it is tagged differently, or the format filter \
                 excluded everything.",
                if fmt.trim().is_empty() { String::new() } else { format!(" ({fmt})") },
                responses.len()
            );
        }

        // Free slot and a short queue means a download that actually starts.
        hits.sort_by_key(|(r, _)| (!r.has_free_upload_slot, r.queue_length));

        let kept: usize = hits.iter().map(|(_, f)| f.len()).sum();
        let mut out = format!(
            "\"{label}\"{} — {kept} matching file(s) from {} peer(s), filtered down from {total} \
             raw results across {} responders.\n",
            if fmt.trim().is_empty() { String::new() } else { format!(" [{fmt}]") },
            hits.len(),
            responses.len()
        );
        for (r, files) in hits.iter().take(12) {
            out.push_str(&format!(
                "\n{} — {}kb/s{}{}\n",
                r.username,
                r.upload_speed / 1024,
                if r.has_free_upload_slot { ", free slot" } else { "" },
                if r.queue_length > 0 { format!(", queue {}", r.queue_length) } else { String::new() },
            ));
            for f in files.iter().take(10) {
                let q = f.quality();
                out.push_str(&format!(
                    "   {} [{}{}]\n",
                    f.filename,
                    f.size_human(),
                    if q.is_empty() { String::new() } else { format!(", {q}") },
                ));
            }
            if files.len() > 10 {
                out.push_str(&format!("   … and {} more\n", files.len() - 10));
            }
        }
        out
    }

    /// Run one query and return matching files per peer. Shared by `search_label`
    /// and the wishlist so both behave identically.
    async fn run_query(
        &self,
        query: &str,
        label_filter: bool,
        fmt: &str,
    ) -> Result<Vec<(String, Vec<slskd_client::File>)>, String> {
        let search = self.client.start_search(query).await.map_err(|e| e.to_string())?;
        let mut responses = Vec::new();
        for _ in 0..8 {
            tokio::time::sleep(std::time::Duration::from_secs(2)).await;
            match self.client.search_responses(&search.id).await {
                Ok(r) if !r.is_empty() => {
                    responses = r;
                    if let Ok(s) = self.client.search(&search.id).await {
                        if s.is_complete() {
                            break;
                        }
                    }
                }
                Ok(_) => {}
                Err(e) => return Err(e.to_string()),
            }
        }
        let mut out = Vec::new();
        for r in responses {
            let files: Vec<_> = r
                .files
                .into_iter()
                .filter(|f| !label_filter || has_label_tag(&f.filename, query))
                .filter(|f| matches_format(&f.filename, f.extension.as_deref(), fmt))
                .collect();
            if !files.is_empty() {
                out.push((r.username, files));
            }
        }
        Ok(out)
    }

    /// Add a standing search.
    #[tool(description = "Add a standing search to the wishlist. Wishlist entries are re-run \
                          on a schedule and only NEW results are reported, so you hear about \
                          a record the week it finally appears on the network.")]
    async fn wishlist_add(
        &self,
        Parameters(WishAddParams { query, label_filter, formats }): Parameters<WishAddParams>,
    ) -> String {
        let mut w = Wishlist::load();
        let e = Entry {
            query: query.clone(),
            label_filter: label_filter.unwrap_or(false),
            formats: formats.clone(),
            added: chrono_now(),
        };
        if !w.add(e) {
            return format!("\"{query}\" is already on the wishlist with those settings.");
        }
        match w.save() {
            Ok(_) => format!(
                "Added \"{query}\" to the wishlist{}{}. {} entr(ies) total.",
                if label_filter.unwrap_or(false) { " (label filter)" } else { "" },
                formats.map(|f| format!(" [{f}]")).unwrap_or_default(),
                w.entries.len()
            ),
            Err(e) => format!("Could not save wishlist: {e}"),
        }
    }

    /// Show the wishlist.
    #[tool(description = "List standing wishlist searches")]
    async fn wishlist_list(&self) -> String {
        let w = Wishlist::load();
        if w.entries.is_empty() {
            return "Wishlist is empty.".into();
        }
        let mut out = format!(
            "{} wishlist entr(ies), {} result(s) already reported:\n",
            w.entries.len(),
            w.seen.len()
        );
        for (i, e) in w.entries.iter().enumerate() {
            out.push_str(&format!(
                "{}. {}{}{}\n",
                i + 1,
                e.query,
                if e.label_filter { "  (label)" } else { "" },
                e.formats.as_ref().map(|f| format!("  [{f}]")).unwrap_or_default(),
            ));
        }
        out
    }

    /// Remove an entry.
    #[tool(description = "Remove a wishlist entry by its 1-based index from wishlist_list")]
    async fn wishlist_remove(
        &self,
        Parameters(WishRemoveParams { index }): Parameters<WishRemoveParams>,
    ) -> String {
        let mut w = Wishlist::load();
        match w.remove(index as usize) {
            Some(e) => match w.save() {
                Ok(_) => format!("Removed \"{}\". {} left.", e.query, w.entries.len()),
                Err(err) => format!("Removed in memory but could not save: {err}"),
            },
            None => format!("No entry {index}. Use wishlist_list to see the numbering."),
        }
    }

    /// Run every wishlist entry and report only what hasn't been seen before.
    #[tool(description = "Run all wishlist searches now and report only results not seen \
                          before. Takes ~20s per entry because Soulseek results arrive \
                          asynchronously.")]
    async fn wishlist_check(&self) -> String {
        let mut w = Wishlist::load();
        if w.entries.is_empty() {
            return "Wishlist is empty — add something with wishlist_add.".into();
        }
        let entries = w.entries.clone();
        let mut report = String::new();
        let mut total_new = 0;

        for e in &entries {
            let fmt = e.formats.clone().unwrap_or_default();
            let hits = match self.run_query(&e.query, e.label_filter, &fmt).await {
                Ok(h) => h,
                Err(err) => {
                    report.push_str(&format!("\n{}: search failed — {err}\n", e.query));
                    continue;
                }
            };
            let mut fresh: Vec<(String, Vec<slskd_client::File>)> = Vec::new();
            for (user, files) in hits {
                let nf: Vec<_> = files.into_iter().filter(|f| w.is_new(&f.filename)).collect();
                if !nf.is_empty() {
                    fresh.push((user, nf));
                }
            }
            if fresh.is_empty() {
                continue;
            }
            let n: usize = fresh.iter().map(|(_, f)| f.len()).sum();
            total_new += n;
            report.push_str(&format!("\n=== {} — {n} new file(s)\n", e.query));
            for (user, files) in fresh.iter().take(5) {
                report.push_str(&format!("{user}:\n"));
                for f in files.iter().take(6) {
                    report.push_str(&format!("   {} [{}]\n", f.filename, f.size_human()));
                }
            }
            let names: Vec<String> =
                fresh.iter().flat_map(|(_, f)| f.iter().map(|x| x.filename.clone())).collect();
            w.mark_seen(names.iter().map(|s| s.as_str()));
        }

        if let Err(e) = w.save() {
            report.push_str(&format!("\n(warning: wishlist not saved: {e})"));
        }
        if total_new == 0 {
            format!("Checked {} wishlist entr(ies). Nothing new.", entries.len())
        } else {
            format!("{total_new} new result(s) across {} entr(ies).\n{report}", entries.len())
        }
    }

    /// All searches slskd currently knows about.
    #[tool(description = "List recent searches and their state")]
    async fn searches(&self) -> String {
        match self.client.searches().await {
            Ok(list) if list.is_empty() => "No searches.".into(),
            Ok(list) => list
                .iter()
                .map(|s| {
                    format!(
                        "{}  {}  responses={} files={} {}",
                        s.id,
                        s.search_text.as_deref().unwrap_or("?"),
                        s.response_count,
                        s.file_count,
                        s.state.as_deref().unwrap_or("")
                    )
                })
                .collect::<Vec<_>>()
                .join("\n"),
            Err(e) => format!("Failed: {e}"),
        }
    }

    /// Browse a user's shared files.
    #[tool(description = "Browse everything a Soulseek user is sharing")]
    async fn browse(&self, Parameters(UserParams { username }): Parameters<UserParams>) -> String {
        match self.client.browse(&username).await {
            Ok(v) => {
                let s = serde_json::to_string_pretty(&v).unwrap_or_default();
                s.chars().take(4000).collect()
            }
            Err(e) => format!("Browse failed: {e}"),
        }
    }

    /// Current download state.
    #[tool(description = "Show current downloads and their progress")]
    async fn downloads(&self) -> String {
        match self.client.downloads().await {
            Ok(v) => {
                let s = serde_json::to_string_pretty(&v).unwrap_or_default();
                if s.trim() == "[]" {
                    "No downloads.".into()
                } else {
                    s.chars().take(4000).collect()
                }
            }
            Err(e) => format!("Failed: {e}"),
        }
    }

    /// Queue a download. Gated.
    #[tool(description = "Queue a file for download. Requires the server to have been \
                          started with downloads enabled. Use the exact filename and \
                          size from search_results.")]
    async fn download(
        &self,
        Parameters(DownloadParams { username, filename, size }): Parameters<DownloadParams>,
    ) -> String {
        if !self.allow_downloads {
            return "Downloads are disabled on this MCP server. \
                    Restart it with --allow-downloads (or SLSKD_ALLOW_DOWNLOADS=1) \
                    if the operator wants to permit this."
                .into();
        }
        let files = [QueueDownloadRequest { filename: filename.clone(), size }];
        match self.client.enqueue(&username, &files).await {
            Ok(_) => format!("Queued: {filename}\nfrom {username}\nTrack it with `downloads`."),
            Err(e) => format!("Enqueue failed: {e}"),
        }
    }
}

/// Cheap ISO-ish timestamp without pulling in a date crate.
fn chrono_now() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("epoch:{secs}")
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let allow_downloads = std::env::args().any(|a| a == "--allow-downloads")
        || std::env::var("SLSKD_ALLOW_DOWNLOADS").map(|v| v == "1").unwrap_or(false);

    let client = Client::from_env()?;

    // Non-MCP mode for cron: run the wishlist, print anything new, exit.
    if std::env::args().any(|a| a == "--wishlist-run") {
        let s = Slskd { client: Arc::new(client), allow_downloads: false };
        let out = s.wishlist_check().await;
        if !out.starts_with("Checked") && !out.starts_with("Wishlist is empty") {
            println!("{out}");
        }
        return Ok(());
    }

    // Fail loudly at startup rather than on the first tool call.
    match client.health().await {
        Ok(true) => {}
        Ok(false) => eprintln!("warning: slskd responded to /health but not with success"),
        Err(e) => eprintln!("warning: could not reach slskd ({e}); tools will fail until it is up"),
    }

    eprintln!(
        "slskd-mcp starting (downloads {})",
        if allow_downloads { "ENABLED" } else { "disabled" }
    );

    let service = Slskd { client: Arc::new(client), allow_downloads }
        .serve(stdio())
        .await?;
    service.waiting().await?;
    Ok(())
}
