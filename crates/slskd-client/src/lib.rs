//! A small typed client for the slskd HTTP API.
//!
//! Deliberately hand-written rather than generated: slskd's OpenAPI spec has three
//! defects that break codegen — 14 paths carry a literal `v{version}` placeholder,
//! `components.securitySchemes` is empty so auth is never wired up, and the search
//! endpoints declare no response schemas at all.
//!
//! No UI or MCP dependencies, and it compiles for `wasm32` — so it stays usable
//! outside `slskd-mcp` if a front-end is ever wanted. That is parked, not planned.

pub mod filter;
pub mod types;

pub use filter::{has_label_tag, matches_format};
pub use types::*;

use serde::de::DeserializeOwned;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("http: {0}")]
    Http(#[from] reqwest::Error),
    #[error("slskd returned {status}: {body}")]
    Api { status: u16, body: String },
    #[error("{0}")]
    Config(String),
}

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Clone)]
pub struct Client {
    base: String,
    api_key: String,
    http: reqwest::Client,
}

impl Client {
    /// `base_url` e.g. `http://localhost:5030`.
    pub fn new(base_url: impl Into<String>, api_key: impl Into<String>) -> Self {
        let base = base_url.into().trim_end_matches('/').to_string();
        Self {
            base,
            api_key: api_key.into(),
            http: reqwest::Client::new(),
        }
    }

    /// Reads `SLSKD_URL` (default `http://localhost:5030`) and `SLSKD_API_KEY`.
    pub fn from_env() -> Result<Self> {
        let base = std::env::var("SLSKD_URL").unwrap_or_else(|_| "http://localhost:5030".into());
        let key = std::env::var("SLSKD_API_KEY")
            .map_err(|_| Error::Config("SLSKD_API_KEY is not set".into()))?;
        Ok(Self::new(base, key))
    }

    /// Always `v0`. The spec's `v{version}` placeholder is a generation bug.
    fn url(&self, path: &str) -> String {
        format!("{}/api/v0/{}", self.base, path.trim_start_matches('/'))
    }

    async fn send<T: DeserializeOwned>(&self, req: reqwest::RequestBuilder) -> Result<T> {
        let resp = req.header("X-API-Key", &self.api_key).send().await?;
        let status = resp.status();
        let body = resp.text().await?;
        if !status.is_success() {
            return Err(Error::Api {
                status: status.as_u16(),
                body: body.chars().take(400).collect(),
            });
        }
        // Some endpoints return 204/empty; treat that as null for T = ().
        let body = if body.trim().is_empty() { "null".into() } else { body };
        serde_json::from_str(&body).map_err(|e| Error::Api {
            status: status.as_u16(),
            body: format!("could not decode response: {e}"),
        })
    }

    /// Cheap liveness check that doesn't need auth.
    pub async fn health(&self) -> Result<bool> {
        let resp = self.http.get(format!("{}/health", self.base)).send().await?;
        Ok(resp.status().is_success())
    }

    // ---- searches ----

    pub async fn start_search(&self, text: &str) -> Result<Search> {
        let body = SearchRequest {
            search_text: text.to_string(),
            ..Default::default()
        };
        self.send(self.http.post(self.url("searches")).json(&body)).await
    }

    pub async fn searches(&self) -> Result<Vec<Search>> {
        self.send(self.http.get(self.url("searches"))).await
    }

    pub async fn search(&self, id: &str) -> Result<Search> {
        self.send(self.http.get(self.url(&format!("searches/{id}")))).await
    }

    pub async fn search_responses(&self, id: &str) -> Result<Vec<SearchResponse>> {
        self.send(self.http.get(self.url(&format!("searches/{id}/responses")))).await
    }

    pub async fn stop_search(&self, id: &str) -> Result<()> {
        self.send(self.http.put(self.url(&format!("searches/{id}")))).await
    }

    // ---- users ----

    pub async fn browse(&self, username: &str) -> Result<serde_json::Value> {
        self.send(self.http.get(self.url(&format!("users/{username}/browse")))).await
    }

    pub async fn user_info(&self, username: &str) -> Result<serde_json::Value> {
        self.send(self.http.get(self.url(&format!("users/{username}/info")))).await
    }

    // ---- transfers ----

    pub async fn downloads(&self) -> Result<serde_json::Value> {
        self.send(self.http.get(self.url("transfers/downloads"))).await
    }

    /// Queue files from one user. This is the only method that changes the world.
    pub async fn enqueue(&self, username: &str, files: &[QueueDownloadRequest]) -> Result<serde_json::Value> {
        self.send(
            self.http
                .post(self.url(&format!("transfers/downloads/{username}")))
                .json(files),
        )
        .await
    }
}
