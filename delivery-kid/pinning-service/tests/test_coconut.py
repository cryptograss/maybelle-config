"""Tests for app.services.coconut — job config building and quality tiers."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.coconut import submit_to_coconut, save_job, load_job, list_jobs


class TestJobConfigBuilding:
    """Test that submit_to_coconut builds correct Coconut API payloads."""

    @pytest.mark.asyncio
    async def test_default_qualities(self):
        """Default qualities should be 720p and 480p."""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"id": "job-123", "status": "processing"}
        mock_response.raise_for_status = lambda: None

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await submit_to_coconut(
                source_url="https://example.com/video.mp4",
                api_key="test-key",
                webhook_url="https://example.com/webhook",
            )

            call_args = mock_client.post.call_args
            job_config = call_args.kwargs.get("json") or call_args[1].get("json")

            # Should have 720p and 480p outputs plus master
            assert "hls_av1_720p" in job_config["outputs"]
            assert "hls_av1_480p" in job_config["outputs"]
            assert "hls_master" in job_config["outputs"]
            assert len(job_config["outputs"]) == 3

    @pytest.mark.asyncio
    async def test_custom_qualities(self):
        """Custom qualities should produce matching output keys."""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"id": "job-456", "status": "processing"}
        mock_response.raise_for_status = lambda: None

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await submit_to_coconut(
                source_url="https://example.com/video.mp4",
                api_key="test-key",
                webhook_url="https://example.com/webhook",
                qualities=[1080, 720, 480, 360],
            )

            call_args = mock_client.post.call_args
            job_config = call_args.kwargs.get("json") or call_args[1].get("json")

            assert "hls_av1_1080p" in job_config["outputs"]
            assert "hls_av1_720p" in job_config["outputs"]
            assert "hls_av1_480p" in job_config["outputs"]
            assert "hls_av1_360p" in job_config["outputs"]
            assert "hls_master" in job_config["outputs"]

            # Master should list all variants
            master = job_config["outputs"]["hls_master"]
            assert set(master["hls"]["variants"]) == {
                "hls_av1_1080p", "hls_av1_720p", "hls_av1_480p", "hls_av1_360p"
            }

    @pytest.mark.asyncio
    async def test_bitrate_tiers(self):
        """Higher resolutions should get higher bitrates."""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"id": "job-789", "status": "processing"}
        mock_response.raise_for_status = lambda: None

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await submit_to_coconut(
                source_url="https://example.com/video.mp4",
                api_key="test-key",
                webhook_url="https://example.com/webhook",
                qualities=[1080, 720, 480],
            )

            call_args = mock_client.post.call_args
            job_config = call_args.kwargs.get("json") or call_args[1].get("json")

            assert job_config["outputs"]["hls_av1_1080p"]["video"]["bitrate"] == "4000k"
            assert job_config["outputs"]["hls_av1_720p"]["video"]["bitrate"] == "2000k"
            assert job_config["outputs"]["hls_av1_480p"]["video"]["bitrate"] == "1000k"

    @pytest.mark.asyncio
    async def test_all_outputs_use_av1_opus(self):
        """All quality tiers should use AV1 video and Opus audio."""
        mock_response = AsyncMock()
        mock_response.json.return_value = {"id": "job-abc", "status": "processing"}
        mock_response.raise_for_status = lambda: None

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await submit_to_coconut(
                source_url="https://example.com/video.mp4",
                api_key="test-key",
                webhook_url="https://example.com/webhook",
                qualities=[720, 480],
            )

            call_args = mock_client.post.call_args
            job_config = call_args.kwargs.get("json") or call_args[1].get("json")

            for key, output in job_config["outputs"].items():
                if key == "hls_master":
                    continue
                assert output["video"]["codec"] == "av1", f"{key} should use av1"
                assert output["audio"]["codec"] == "opus", f"{key} should use opus"


class TestJobPersistence:
    """Test job save/load/list operations."""

    def test_save_and_load(self, tmp_path):
        job_data = {"id": "test-job", "status": "processing", "source_cid": "bafytest"}
        save_job(tmp_path, "test-job", job_data)

        loaded = load_job(tmp_path, "test-job")
        assert loaded == job_data

    def test_load_nonexistent(self, tmp_path):
        assert load_job(tmp_path, "nonexistent") is None

    def test_list_jobs(self, tmp_path):
        for i in range(3):
            save_job(tmp_path, f"job-{i}", {"id": f"job-{i}", "index": i})

        jobs = list_jobs(tmp_path)
        assert len(jobs) == 3

    def test_list_jobs_limit(self, tmp_path):
        for i in range(5):
            save_job(tmp_path, f"job-{i}", {"id": f"job-{i}"})

        jobs = list_jobs(tmp_path, limit=2)
        assert len(jobs) == 2


class TestContentFinalizeRequest:
    """Test the transcoding_qualities field on ContentFinalizeRequest."""

    def test_default_qualities_is_none(self):
        from app.models.content import ContentFinalizeRequest
        req = ContentFinalizeRequest()
        assert req.transcoding_qualities is None

    def test_custom_qualities(self):
        from app.models.content import ContentFinalizeRequest
        req = ContentFinalizeRequest(transcoding_qualities=[1080, 720])
        assert req.transcoding_qualities == [1080, 720]

    def test_default_strategy_is_auto(self):
        from app.models.content import ContentFinalizeRequest
        req = ContentFinalizeRequest()
        assert req.transcoding_strategy == "auto"
