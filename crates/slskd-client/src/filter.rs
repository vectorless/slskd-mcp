//! Filtering search results down to actual label releases.
//!
//! Soulseek search is a plain substring match over the whole path, so searching
//! "Planet Rhythm" also returns Leftfield's "Phat Planet" from "Rhythm & Stealth".
//! Observed on the live network 2026-09-08.
//!
//! Releases are near-universally foldered with the label as a bracketed tag —
//! `Artist - Title EP [Planet Rhythm]` — so requiring that pattern separates
//! genuine label releases from coincidental word matches.

/// Lowercase, alphanumeric only. "Planet Rhythm" and "planet-rhythm" both become
/// "planetrhythm", which absorbs the punctuation and spacing variation in folder names.
fn norm(s: &str) -> String {
    s.chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(|c| c.to_lowercase())
        .collect()
}

/// Every substring enclosed in `[]`, `()` or `{}`. Not nesting-aware — folder
/// names don't nest brackets in practice.
fn bracketed(path: &str) -> Vec<&str> {
    let mut out = Vec::new();
    for (open, close) in [('[', ']'), ('(', ')'), ('{', '}')] {
        let mut start: Option<usize> = None;
        for (i, c) in path.char_indices() {
            if c == open {
                start = Some(i + c.len_utf8());
            } else if c == close {
                if let Some(s) = start.take() {
                    if i > s {
                        out.push(&path[s..i]);
                    }
                }
            }
        }
    }
    out
}

/// Does this path carry `label` as a bracketed tag?
///
/// Matches on the normalised form, and accepts a tag that merely *contains* the
/// label so `[Planet Rhythm Records]` and `[PRRUKBLK]`-style suffixes still count.
pub fn has_label_tag(path: &str, label: &str) -> bool {
    let want = norm(label);
    if want.len() < 3 {
        return false; // too short to be meaningful; would match everything
    }
    bracketed(path).into_iter().any(|tag| {
        let t = norm(tag);
        t == want || (t.contains(&want) && t.len() <= want.len() + 12)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_a_real_release_folder() {
        assert!(has_label_tag(
            r"Music\fanon flowers - black hand ep [planet rhythm ]\02-track.mp3",
            "Planet Rhythm"
        ));
        assert!(has_label_tag(
            r"Alfredo Mazzilli - Planet Rhythm 2019 [Planet Rhythm]\cover.jpg",
            "planet rhythm"
        ));
    }

    #[test]
    fn rejects_coincidental_word_matches() {
        // The real false positives seen on the live network.
        assert!(!has_label_tag(
            r"Music\L\Leftfield\Rhythm & Stealth\02 - Phat Planet.mp3",
            "Planet Rhythm"
        ));
        assert!(!has_label_tag(
            r"VA\Planet Soul - Set U Free (Fever Mix) [Strictly Rhythm].mp3",
            "Planet Rhythm"
        ));
    }

    #[test]
    fn tolerates_punctuation_and_case() {
        assert!(has_label_tag("Artist - EP (PLANET-RHYTHM)/a.flac", "Planet Rhythm"));
    }

    #[test]
    fn allows_a_short_suffix_but_not_a_different_label() {
        assert!(has_label_tag("x [Planet Rhythm Records]/a.mp3", "Planet Rhythm"));
        assert!(!has_label_tag("x [Token]/a.mp3", "Planet Rhythm"));
    }

    #[test]
    fn ignores_labels_too_short_to_discriminate() {
        assert!(!has_label_tag("x [ab]/a.mp3", "ab"));
    }
}

/// Shorthands an agent (or a person) is likely to reach for.
fn expand_formats(spec: &str) -> Vec<String> {
    let mut out = Vec::new();
    for token in spec.split(|c| c == ',' || c == ' ' || c == '|') {
        let t = token.trim().trim_start_matches('.').to_lowercase();
        if t.is_empty() {
            continue;
        }
        match t.as_str() {
            "lossless" => out.extend(
                ["flac", "wav", "aiff", "aif", "ape", "alac", "wv"]
                    .iter()
                    .map(|s| s.to_string()),
            ),
            "lossy" => out.extend(
                ["mp3", "m4a", "aac", "ogg", "opus", "wma"]
                    .iter()
                    .map(|s| s.to_string()),
            ),
            other => out.push(other.to_string()),
        }
    }
    out.sort();
    out.dedup();
    out
}

/// Does this file match the requested format spec?
///
/// `spec` accepts extensions and the shorthands `lossless` / `lossy`, in any mix:
/// `"flac"`, `"flac,wav"`, `"lossless"`, `".FLAC, aiff"`.
///
/// Soulseek's `extension` field is frequently empty, so the filename suffix is the
/// source of truth and `extension` is only a fallback.
pub fn matches_format(filename: &str, extension: Option<&str>, spec: &str) -> bool {
    let wanted = expand_formats(spec);
    if wanted.is_empty() {
        return true; // no filter requested
    }
    let from_name = filename
        .rsplit(|c| c == '.' || c == '\\' || c == '/')
        .next()
        .filter(|_| filename.contains('.'))
        .map(|s| s.to_lowercase());
    let ext = from_name
        .filter(|s| !s.is_empty() && s.len() <= 5)
        .or_else(|| extension.map(|e| e.trim_start_matches('.').to_lowercase()));

    match ext {
        Some(e) => wanted.iter().any(|w| *w == e),
        None => false,
    }
}

#[cfg(test)]
mod format_tests {
    use super::*;

    #[test]
    fn plain_extension() {
        assert!(matches_format(r"a\b\track.flac", None, "flac"));
        assert!(!matches_format(r"a\b\track.mp3", None, "flac"));
    }

    #[test]
    fn several_and_punctuated() {
        assert!(matches_format("t.wav", None, "flac, wav"));
        assert!(matches_format("t.FLAC", None, ".flac"));
    }

    #[test]
    fn lossless_shorthand() {
        assert!(matches_format("t.flac", None, "lossless"));
        assert!(matches_format("t.aiff", None, "lossless"));
        assert!(!matches_format("t.mp3", None, "lossless"));
        assert!(matches_format("t.mp3", None, "lossy"));
    }

    #[test]
    fn empty_spec_keeps_everything() {
        assert!(matches_format("t.mp3", None, ""));
    }

    #[test]
    fn falls_back_to_extension_field_when_name_has_none() {
        assert!(matches_format("trackname", Some("flac"), "flac"));
    }

    #[test]
    fn does_not_match_images_in_release_folders() {
        // Real case: cover art sits alongside the audio.
        assert!(!matches_format("Planet Rhythm 2019.jpg", None, "lossless"));
    }
}
