"""Tests for app.services.url_fetch — URL safety, metadata projection, file pick."""

import socket
from unittest.mock import patch

import pytest

from app.services.url_fetch import (
    UrlNotAllowed,
    _build_command,
    _pick_media_file,
    source_metadata,
    validate_fetchable_url,
)


def _resolves_to(*addresses: str):
    """Patch getaddrinfo so URL validation never depends on live DNS."""
    infos = []
    for addr in addresses:
        family = socket.AF_INET6 if ":" in addr else socket.AF_INET
        infos.append((family, socket.SOCK_STREAM, 6, "", (addr, 0)))
    return patch("socket.getaddrinfo", return_value=infos)


class TestValidateFetchableUrl:
    def test_accepts_public_https(self):
        with _resolves_to("142.250.72.14"):
            assert validate_fetchable_url("https://example.com/watch?v=abc") == \
                "https://example.com/watch?v=abc"

    def test_accepts_public_http(self):
        with _resolves_to("142.250.72.14"):
            assert validate_fetchable_url("http://example.com/v.mp4")

    def test_strips_surrounding_whitespace(self):
        with _resolves_to("142.250.72.14"):
            assert validate_fetchable_url("  https://example.com/x  ") == \
                "https://example.com/x"

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/x.mp4",
        "ytsearch:bluegrass",
        "/etc/passwd",
    ])
    def test_rejects_non_http_schemes(self, url):
        with pytest.raises(UrlNotAllowed):
            validate_fetchable_url(url)

    def test_rejects_missing_host(self):
        with pytest.raises(UrlNotAllowed, match="no host"):
            validate_fetchable_url("http:///justapath")

    @pytest.mark.parametrize("addr", [
        "127.0.0.1",        # loopback
        "10.0.0.2",         # private — maybelle
        "192.168.1.10",     # private
        "172.16.4.4",       # private
        "169.254.169.254",  # link-local, the classic metadata endpoint
        "0.0.0.0",          # unspecified
        "::1",              # IPv6 loopback
        "fe80::1",          # IPv6 link-local
        "fc00::1",          # IPv6 unique-local
    ])
    def test_rejects_internal_addresses(self, addr):
        with _resolves_to(addr):
            with pytest.raises(UrlNotAllowed, match="non-public"):
                validate_fetchable_url("https://sneaky.example.com/x")

    def test_rejects_ipv4_mapped_ipv6(self):
        # ::ffff:10.0.0.2 is not "private" as an IPv6Address; it has to be
        # judged on the embedded v4 address or it slips straight through.
        with _resolves_to("::ffff:10.0.0.2"):
            with pytest.raises(UrlNotAllowed, match="non-public"):
                validate_fetchable_url("https://sneaky.example.com/x")

    def test_rejects_when_any_resolved_address_is_internal(self):
        # A host answering with one public and one private address must not
        # pass on the strength of the public one.
        with _resolves_to("142.250.72.14", "127.0.0.1"):
            with pytest.raises(UrlNotAllowed, match="non-public"):
                validate_fetchable_url("https://split.example.com/x")

    def test_rejects_unresolvable_host(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            with pytest.raises(UrlNotAllowed, match="Could not resolve"):
                validate_fetchable_url("https://nx.example.com/x")


class TestBuildCommand:
    def test_disables_playlist_expansion(self, tmp_path):
        # A URL carrying a list= parameter would otherwise drag the whole
        # playlist into a draft meant to hold one video.
        assert "--no-playlist" in _build_command("https://x/y", tmp_path, 100)

    def test_ignores_user_config(self, tmp_path):
        cmd = _build_command("https://x/y", tmp_path, 100)
        assert "--ignore-config" in cmd
        assert "--no-config-locations" in cmd

    def test_passes_size_cap_and_url_last(self, tmp_path):
        cmd = _build_command("https://x/y", tmp_path, 250)
        assert cmd[cmd.index("--max-filesize") + 1] == "250M"
        # `--` then the URL, so a URL starting with a dash can't become a flag.
        assert cmd[-2:] == ["--", "https://x/y"]


class TestPickMediaFile:
    def test_returns_none_when_empty(self, tmp_path):
        assert _pick_media_file(tmp_path) is None

    def test_ignores_info_json_sidecar(self, tmp_path):
        (tmp_path / "clip.info.json").write_text("{}")
        assert _pick_media_file(tmp_path) is None

    def test_picks_largest_media_file(self, tmp_path):
        (tmp_path / "small.mp4").write_bytes(b"x" * 10)
        (tmp_path / "big.mkv").write_bytes(b"x" * 500)
        (tmp_path / "clip.info.json").write_text("{}")
        assert _pick_media_file(tmp_path).name == "big.mkv"

    def test_accepts_audio_only_fetch(self, tmp_path):
        (tmp_path / "tune.m4a").write_bytes(b"x" * 10)
        assert _pick_media_file(tmp_path).name == "tune.m4a"


class TestSourceMetadata:
    def test_namespaces_and_reformats_date(self):
        out = source_metadata({
            "webpage_url": "https://example.com/watch?v=abc",
            "title": "Flatpicking at Station Inn",
            "uploader": "Justin Holmes",
            "upload_date": "20260614",
            "duration": 212,
            "extractor_key": "Youtube",
        })
        assert out["source_url"] == "https://example.com/watch?v=abc"
        assert out["source_title"] == "Flatpicking at Station Inn"
        assert out["source_uploader"] == "Justin Holmes"
        assert out["source_upload_date"] == "2026-06-14"
        assert out["source_duration_seconds"] == 212
        assert out["source_extractor"] == "Youtube"
        # Every key namespaced, so a merge can't clobber user-entered fields.
        assert all(k.startswith("source_") for k in out)

    def test_drops_empty_and_missing_fields(self):
        out = source_metadata({"title": "", "description": None, "uploader": "RJ"})
        assert "source_title" not in out
        assert "source_description" not in out
        assert out["source_uploader"] == "RJ"

    def test_leaves_malformed_date_alone(self):
        assert source_metadata({"upload_date": "June 2026"})["source_upload_date"] == \
            "June 2026"

    def test_falls_back_to_channel_and_original_url(self):
        out = source_metadata({"channel": "Cryptograss", "original_url": "https://x/y"})
        assert out["source_uploader"] == "Cryptograss"
        assert out["source_url"] == "https://x/y"

    def test_empty_info_yields_empty_dict(self):
        assert source_metadata({}) == {}
