"""Every test runs against the made-up candidate in profile.example.yaml, with a
throwaway data directory and no credentials, so nothing can reach a real model,
tracker, Telegram chat or job board."""
import copy
import os
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PROFILE = ROOT / "profile.example.yaml"

os.environ["JOB_PIPELINE_PROFILE"] = str(EXAMPLE_PROFILE)
os.environ["JOB_PIPELINE_DATA"] = tempfile.mkdtemp(prefix="job-pipeline-test-data-")
for _name in ("ANTHROPIC_API_KEY", "LLM_API_KEY", "LLM_BASE_URL", "LLM_PROVIDER", "LLM_MODEL", "LLM_MAX_TOKENS",
              "SUPABASE_URL", "SUPABASE_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ADZUNA_APP_ID",
              "ADZUNA_APP_KEY", "FIRECRAWL_API_KEY", "IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
    os.environ.pop(_name, None)


def _merge(base: dict, changes: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


@pytest.fixture
def profile_with(tmp_path, monkeypatch):
    """Switch the active profile to the example with some fields changed:
    profile_with({"hard_bars": {"german_c1": False}})."""
    from pipeline import profile

    def activate(changes: dict) -> dict:
        data = _merge(yaml.safe_load(EXAMPLE_PROFILE.read_text(encoding="utf-8")), changes)
        path = tmp_path / "profile.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        monkeypatch.setenv("JOB_PIPELINE_PROFILE", str(path))
        profile.reset()
        return profile.get()

    yield activate
    profile.reset()
