"""The README states the rules and commands the code has; these checks keep the two in step."""
import re
from pathlib import Path

from pipeline.__main__ import COMMANDS
from pipeline.followup import DUE_AFTER_DAYS

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_followup_rule_is_day_4_in_code_and_readme():
    assert DUE_AFTER_DAYS == 4
    assert re.search(r"day 4 after the send[^.]*Monday", README), "README must state the follow-up rule"
    assert not re.search(r"\b7 days\b|one week after", README)


def test_readme_shows_every_command():
    for name in COMMANDS:
        assert f"python -m pipeline {name}" in README, name


def test_every_environment_variable_is_documented():
    names = re.findall(r"^([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M)
    assert names
    for name in names:
        assert f"`{name}`" in README, name


def test_every_migration_is_named_in_the_readme():
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        assert path.name in README, path.name
