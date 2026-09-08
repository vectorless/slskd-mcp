from slskd_mcp.filter import expand_formats, has_label_tag, matches_format


def test_matches_a_real_release_folder():
    assert has_label_tag(
        r"Music\fanon flowers - black hand ep [planet rhythm ]\02-track.mp3",
        "Planet Rhythm",
    )
    assert has_label_tag(
        r"Alfredo Mazzilli - Planet Rhythm 2019 [Planet Rhythm]\cover.jpg",
        "planet rhythm",
    )


def test_rejects_coincidental_word_matches():
    # The real false positives seen on the live network.
    assert not has_label_tag(
        r"Music\L\Leftfield\Rhythm & Stealth\02 - Phat Planet.mp3", "Planet Rhythm"
    )
    assert not has_label_tag(
        r"VA\Planet Soul - Set U Free (Fever Mix) [Strictly Rhythm].mp3",
        "Planet Rhythm",
    )


def test_tolerates_punctuation_and_case():
    assert has_label_tag("Artist - EP (PLANET-RHYTHM)/a.flac", "Planet Rhythm")


def test_allows_a_short_suffix_but_not_a_different_label():
    assert has_label_tag("x [Planet Rhythm Records]/a.mp3", "Planet Rhythm")
    assert not has_label_tag("x [Token]/a.mp3", "Planet Rhythm")


def test_ignores_labels_too_short_to_discriminate():
    assert not has_label_tag("x [ab]/a.mp3", "ab")


def test_plain_extension():
    assert matches_format(r"a\b\track.flac", None, "flac")
    assert not matches_format(r"a\b\track.mp3", None, "flac")


def test_several_and_punctuated():
    assert matches_format("t.wav", None, "flac, wav")
    assert matches_format("t.FLAC", None, ".flac")


def test_lossless_shorthand():
    assert matches_format("t.flac", None, "lossless")
    assert matches_format("t.aiff", None, "lossless")
    assert not matches_format("t.mp3", None, "lossless")
    assert matches_format("t.mp3", None, "lossy")


def test_empty_spec_keeps_everything():
    assert matches_format("t.mp3", None, "")


def test_falls_back_to_extension_field_when_name_has_none():
    assert matches_format("trackname", "flac", "flac")


def test_does_not_match_images_in_release_folders():
    # Real case: cover art sits alongside the audio.
    assert not matches_format("Planet Rhythm 2019.jpg", None, "lossless")


def test_expand_formats_dedupes_and_sorts():
    assert expand_formats("flac, flac wav|flac") == ["flac", "wav"]
    assert expand_formats("") == []


def test_windows_paths_with_dots_in_directory_names():
    # Soulseek paths are backslash-separated whatever the peer's OS.
    assert matches_format(r"D:\music\vol.2\track.flac", None, "flac")
    assert not matches_format(r"D:\music\vol.2\readme", None, "flac")
