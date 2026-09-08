//! Types mirroring slskd's API.
//!
//! Field names were taken from slskd's own source rather than its OpenAPI spec —
//! the spec declares no response schemas for the search endpoints. See
//! `src/slskd/Search/Types/{Search,Response}.cs` upstream.

use serde::{Deserialize, Serialize};

/// A file offered by a peer.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct File {
    pub filename: String,
    pub size: i64,
    #[serde(default)]
    pub extension: Option<String>,
    #[serde(default)]
    pub bit_rate: Option<i32>,
    #[serde(default)]
    pub bit_depth: Option<i32>,
    #[serde(default)]
    pub sample_rate: Option<i32>,
    /// Duration in seconds.
    #[serde(default)]
    pub length: Option<i32>,
    #[serde(default)]
    pub is_variable_bit_rate: Option<bool>,
}

impl File {
    /// Human-readable size, because agents shouldn't be doing arithmetic on bytes.
    pub fn size_human(&self) -> String {
        let b = self.size as f64;
        match b {
            b if b >= 1e9 => format!("{:.2} GB", b / 1e9),
            b if b >= 1e6 => format!("{:.1} MB", b / 1e6),
            b if b >= 1e3 => format!("{:.0} KB", b / 1e3),
            _ => format!("{} B", self.size),
        }
    }

    /// e.g. "320kbps 4:56"
    pub fn quality(&self) -> String {
        let mut parts = Vec::new();
        if let Some(br) = self.bit_rate {
            parts.push(format!("{br}kbps"));
        }
        if let Some(len) = self.length {
            parts.push(format!("{}:{:02}", len / 60, len % 60));
        }
        parts.join(" ")
    }
}

/// One peer's answer to a search.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SearchResponse {
    pub username: String,
    #[serde(default)]
    pub file_count: i32,
    #[serde(default)]
    pub files: Vec<File>,
    #[serde(default)]
    pub locked_file_count: i32,
    #[serde(default)]
    pub locked_files: Vec<File>,
    #[serde(default)]
    pub has_free_upload_slot: bool,
    #[serde(default)]
    pub queue_length: i64,
    #[serde(default)]
    pub upload_speed: i64,
}

/// A search, as slskd tracks it.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Search {
    pub id: String,
    #[serde(default)]
    pub search_text: Option<String>,
    #[serde(default)]
    pub state: Option<String>,
    #[serde(default)]
    pub file_count: i32,
    #[serde(default)]
    pub locked_file_count: i32,
    #[serde(default)]
    pub response_count: i32,
    #[serde(default)]
    pub started_at: Option<String>,
    #[serde(default)]
    pub ended_at: Option<String>,
    #[serde(default)]
    pub responses: Vec<SearchResponse>,
}

impl Search {
    /// slskd reports states like "Completed, TimedOut".
    pub fn is_complete(&self) -> bool {
        self.state
            .as_deref()
            .map(|s| s.contains("Completed"))
            .unwrap_or(false)
    }
}

/// POST body for starting a search.
#[derive(Debug, Clone, Serialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct SearchRequest {
    pub search_text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file_limit: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub filter_responses: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub response_limit: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub search_timeout: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub minimum_response_file_count: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub minimum_peer_upload_speed: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub maximum_peer_queue_length: Option<i32>,
}

/// One file to enqueue. POST /transfers/downloads/{username} takes an array of these.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QueueDownloadRequest {
    pub filename: String,
    pub size: i64,
}

/// A download or upload in progress.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Transfer {
    pub id: String,
    pub username: String,
    pub filename: String,
    #[serde(default)]
    pub direction: Option<String>,
    #[serde(default)]
    pub size: i64,
    #[serde(default)]
    pub state: Option<String>,
    #[serde(default)]
    pub bytes_transferred: i64,
    #[serde(default)]
    pub percent_complete: f64,
    #[serde(default)]
    pub average_speed: f64,
    #[serde(default)]
    pub place_in_queue: Option<i32>,
    #[serde(default)]
    pub exception: Option<String>,
}

/// Downloads are returned grouped by user.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UserTransfers {
    pub username: String,
    #[serde(default)]
    pub directories: serde_json::Value,
}
