"""Paths and process setup shared by every entry point.

The repository root holds profile.yaml, .env and the data directory. Two
shell environment variables move them (read at import time, so set them in
the shell or the cron line, not in .env):

  JOB_PIPELINE_HOME   repository root (default: the directory above this package)
  JOB_PIPELINE_DATA   data directory (default: <home>/data)

JOB_PIPELINE_PROFILE (see profile.py) points at a different profile file.
"""
import logging
import os
from pathlib import Path

HOME = Path(os.getenv("JOB_PIPELINE_HOME") or Path(__file__).resolve().parent.parent)
DATA_DIR = Path(os.getenv("JOB_PIPELINE_DATA") or HOME / "data")
APPS_DIR = DATA_DIR / "applications"      # one folder per application kit
SOURCING_DIR = DATA_DIR / "sourcing"      # sourcing reports, one .md and one .json per day
SOURCES_DIR = DATA_DIR / "sources"        # the ATS board registry
STATE_DIR = DATA_DIR / "state"            # follow-up schedule
OUTPUT_DIR = HOME / "output"              # the master CV


def load_env() -> None:
    """Load <home>/.env into the process. Values already in the environment win."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is a declared dependency; tolerate a bare checkout
        return
    load_dotenv(HOME / ".env")


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    # httpx logs every request URL at INFO, and a Telegram URL carries the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
