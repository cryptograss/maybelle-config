"""Tests for app.config — commit provenance and settings."""

import os
from unittest.mock import patch

from app.config import get_commit


class TestGetCommit:
    def test_returns_env_var_when_set(self):
        with patch.dict(os.environ, {"GIT_COMMIT": "abc123f"}):
            assert get_commit() == "abc123f"

    def test_returns_unknown_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            # GIT_COMMIT might be set in the real env, so explicitly remove it
            env = os.environ.copy()
            env.pop("GIT_COMMIT", None)
            with patch.dict(os.environ, env, clear=True):
                assert get_commit() == "unknown"
