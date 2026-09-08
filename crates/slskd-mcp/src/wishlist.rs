//! Standing searches that re-run on a schedule and report only what's new.
//!
//! Soulseek is a live network of people's hard drives — the thing you want may
//! simply not be shared today and appear next week. A wishlist turns "search and
//! get nothing" into "tell me when it shows up".
//!
//! Results already reported are remembered, so a scheduled run is silent unless
//! something genuinely new has appeared. Silence is the point; a watcher that
//! reports the same twelve files every hour gets muted.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Entry {
    /// Search text, or the label name when `label_filter` is set.
    pub query: String,
    /// Apply the bracketed-label-tag filter rather than a plain search.
    #[serde(default)]
    pub label_filter: bool,
    /// Optional format spec, e.g. "lossless" or "flac,wav".
    #[serde(default)]
    pub formats: Option<String>,
    #[serde(default)]
    pub added: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct Wishlist {
    #[serde(default)]
    pub entries: Vec<Entry>,
    /// Filenames already reported. Keeps scheduled runs quiet.
    #[serde(default)]
    pub seen: BTreeSet<String>,
}

pub fn path() -> PathBuf {
    std::env::var("SLSKD_WISHLIST")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let mut p = dirs_home();
            p.push("slskd");
            p.push("wishlist.json");
            p
        })
}

/// Home directory across platforms: `HOME` on Unix and macOS, `USERPROFILE` on
/// Windows. Falls back to the working directory rather than failing, so the
/// wishlist still works somewhere sensible.
fn dirs_home() -> PathBuf {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

impl Wishlist {
    pub fn load() -> Self {
        std::fs::read_to_string(path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    pub fn save(&self) -> std::io::Result<()> {
        let p = path();
        if let Some(dir) = p.parent() {
            std::fs::create_dir_all(dir)?;
        }
        std::fs::write(p, serde_json::to_string_pretty(self)?)
    }

    /// Returns false if an equivalent entry is already present.
    pub fn add(&mut self, e: Entry) -> bool {
        let dup = self.entries.iter().any(|x| {
            x.query.eq_ignore_ascii_case(&e.query)
                && x.label_filter == e.label_filter
                && x.formats == e.formats
        });
        if !dup {
            self.entries.push(e);
        }
        !dup
    }

    /// Removes by 1-based index as shown to the user. Returns the entry removed.
    pub fn remove(&mut self, index: usize) -> Option<Entry> {
        if index == 0 || index > self.entries.len() {
            return None;
        }
        Some(self.entries.remove(index - 1))
    }

    /// Marks filenames as reported; returns how many were genuinely new.
    pub fn mark_seen<'a>(&mut self, files: impl Iterator<Item = &'a str>) -> usize {
        let mut n = 0;
        for f in files {
            if self.seen.insert(f.to_string()) {
                n += 1;
            }
        }
        n
    }

    pub fn is_new(&self, filename: &str) -> bool {
        !self.seen.contains(filename)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(q: &str) -> Entry {
        Entry { query: q.into(), label_filter: true, formats: None, added: String::new() }
    }

    #[test]
    fn rejects_duplicates_case_insensitively() {
        let mut w = Wishlist::default();
        assert!(w.add(entry("Planet Rhythm")));
        assert!(!w.add(entry("planet rhythm")));
        assert_eq!(w.entries.len(), 1);
    }

    #[test]
    fn same_query_different_format_is_a_separate_entry() {
        let mut w = Wishlist::default();
        w.add(entry("Token"));
        let mut e = entry("Token");
        e.formats = Some("lossless".into());
        assert!(w.add(e));
        assert_eq!(w.entries.len(), 2);
    }

    #[test]
    fn seen_set_makes_reruns_quiet() {
        let mut w = Wishlist::default();
        assert!(w.is_new("a.flac"));
        assert_eq!(w.mark_seen(["a.flac", "b.flac"].into_iter()), 2);
        assert!(!w.is_new("a.flac"));
        // second run: nothing new
        assert_eq!(w.mark_seen(["a.flac", "b.flac"].into_iter()), 0);
    }

    #[test]
    fn remove_is_one_based_and_bounds_checked() {
        let mut w = Wishlist::default();
        w.add(entry("A"));
        w.add(entry("B"));
        assert!(w.remove(0).is_none());
        assert!(w.remove(9).is_none());
        assert_eq!(w.remove(1).unwrap().query, "A");
        assert_eq!(w.entries.len(), 1);
    }
}
