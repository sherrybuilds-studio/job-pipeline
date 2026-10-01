"""The CV's presentation layer states nothing the profile does not, has no
slashes, and still fits one readable page whatever the tailoring."""
import re
from pathlib import Path

import pytest

from pipeline import cv_builder, cv_copy, kit, profile
from pipeline.honesty import gate
from pipeline.pdftext import builtin_extract
from pipeline.reviewer import number_audit


def test_copy_has_no_slash_and_no_em_dash():
    text = cv_copy.all_text()
    assert "/" not in text
    assert "—" not in text


def test_copy_passes_the_honesty_gate():
    assert gate(cv_copy.all_text()) == []


def test_every_number_in_the_copy_is_in_the_profile():
    assert number_audit(cv_copy.all_text(), profile.profile_as_text()) == []


def test_every_skill_on_the_cv_is_a_profile_skill():
    skills = set(profile.get()["core_skills"])
    assert {s for _, names in cv_copy.skill_groups() for s in names} <= skills


def test_no_absolute_claims():
    assert not re.search(r"\bevery user\b|\balways\b|\bnever fails\b|\bguarantee", cv_copy.all_text(), re.I)


STACKS = [[], ["Python", "REST", "Git", "SQL", "CI/CD", "Docker"], ["Kubernetes", "CI/CD", "Docker", "Linux", "Git"],
          ["Telegram", "GitHub Actions"], ["RAG", "Vector DB", "OpenAI", "FastAPI"], ["Playwright", "pandas", "SQL"]]


@pytest.mark.parametrize("stack", STACKS)
def test_rendered_cv_is_one_readable_page_without_slashes(tmp_path, stack):
    out = cv_builder.build(str(tmp_path / "cv.pdf"), kit.cv_keywords(stack))
    assert cv_builder.page_count(out) == 1
    text = builtin_extract(Path(out))
    assert "alex@example.com" in text and "Alex Example" in text
    stripped = text.replace(profile.get()["code_url"], "")
    assert "/" not in stripped, [line for line in stripped.splitlines() if "/" in line]
    assert gate(text) == []


def test_tailoring_moves_the_matching_project_first(tmp_path):
    out = cv_builder.build(str(tmp_path / "cv.pdf"), kit.cv_keywords(["Telegram", "GitHub Actions"]))
    text = builtin_extract(Path(out))
    assert text.index("Study Group Bot") < text.index("Course Q&A Assistant")


def test_type_stays_readable(tmp_path, monkeypatch):
    """The one-page guard may shrink type, but the example copy must fit at 94% or better."""
    monkeypatch.setattr(cv_builder, "_MIN_SCALE", 0.94)
    for stack in STACKS:
        cv_builder.build(str(tmp_path / "cv.pdf"), kit.cv_keywords(stack))


def test_a_profile_too_long_for_one_page_fails_loudly(tmp_path, profile_with):
    long_text = "A sentence that goes on for a while to fill the page. " * 80
    projects = profile.get()["projects"]
    profile_with({"projects": [
        {"name": p["name"], "key": p["key"], "description": p["description"], "stack": p["stack"],
         "cv": {"text": long_text, "short": long_text, "badge": p["cv"]["badge"]}} for p in projects]})
    with pytest.raises(RuntimeError, match="one page"):
        cv_builder.build(str(tmp_path / "cv.pdf"), [])
