import json

import pytest

from slskd_mcp.wishlist import Entry, Wishlist, home, path


@pytest.fixture
def wishlist_file(tmp_path, monkeypatch):
    p = tmp_path / "wishlist.json"
    monkeypatch.setenv("SLSKD_WISHLIST", str(p))
    return p


def entry(q: str) -> Entry:
    return Entry(query=q, label_filter=True)


def test_rejects_duplicates_case_insensitively():
    w = Wishlist()
    assert w.add(entry("Planet Rhythm"))
    assert not w.add(entry("planet rhythm"))
    assert len(w.entries) == 1


def test_same_query_different_format_is_a_separate_entry():
    w = Wishlist()
    w.add(entry("Token"))
    e = entry("Token")
    e.formats = "lossless"
    assert w.add(e)
    assert len(w.entries) == 2


def test_seen_set_makes_reruns_quiet():
    w = Wishlist()
    assert w.is_new("a.flac")
    assert w.mark_seen(["a.flac", "b.flac"]) == 2
    assert not w.is_new("a.flac")
    # second run: nothing new
    assert w.mark_seen(["a.flac", "b.flac"]) == 0


def test_remove_is_one_based_and_bounds_checked():
    w = Wishlist()
    w.add(entry("A"))
    w.add(entry("B"))
    assert w.remove(0) is None
    assert w.remove(9) is None
    assert w.remove(1).query == "A"
    assert len(w.entries) == 1


def test_round_trips_through_disk(wishlist_file):
    w = Wishlist()
    w.add(Entry("Token", label_filter=True, formats="lossless", added="epoch:1"))
    w.mark_seen(["b.flac", "a.flac"])
    w.save()

    again = Wishlist.load()
    assert len(again.entries) == 1
    assert again.entries[0].formats == "lossless"
    assert again.entries[0].label_filter is True
    assert again.seen == {"a.flac", "b.flac"}


def test_on_disk_shape_matches_the_rust_implementation(wishlist_file):
    w = Wishlist()
    w.add(Entry("Token", label_filter=True, formats=None, added="epoch:1"))
    w.mark_seen(["b.flac", "a.flac"])
    w.save()

    d = json.loads(wishlist_file.read_text())
    assert set(d) == {"entries", "seen"}
    assert set(d["entries"][0]) == {"query", "label_filter", "formats", "added"}
    # sorted, so the file is stable across runs and matches the Rust BTreeSet
    assert d["seen"] == ["a.flac", "b.flac"]


def test_missing_file_loads_as_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("SLSKD_WISHLIST", str(tmp_path / "nope.json"))
    w = Wishlist.load()
    assert w.entries == [] and w.seen == set()


def test_corrupt_file_loads_as_empty_rather_than_crashing(wishlist_file):
    wishlist_file.write_text("{not json")
    assert Wishlist.load().entries == []


def test_home_falls_back_to_userprofile_on_windows(monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", r"C:\Users\paul")
    assert str(home()) == r"C:\Users\paul"


def test_env_override_wins_over_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SLSKD_WISHLIST", str(tmp_path / "custom.json"))
    assert path() == tmp_path / "custom.json"
