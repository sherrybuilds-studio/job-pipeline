"""The honesty gate on the CV and letter SOURCES. Every CV and letter is rendered
from the profile and the writer's prompt, so a banned claim that slips into
either would reach every application. These tests pin the claim surface: the
profile text, the writer's facts and the CV copy carry nothing on the
profile's banned list, and the writer cites only verified claims."""
import re
from pathlib import Path

import pytest

from pipeline import cv_builder, cv_copy, honesty, kit, profile, writer


def _sources() -> dict[str, str]:
    return {
        "profile_as_text": profile.profile_as_text(),
        # The writer's claim surface. rules_text() is excluded on purpose: it must
        # name the forbidden words ("never write full-time") in order to forbid them.
        "writer.facts_text": writer.facts_text(),
        "cv availability": " · ".join(cv_copy.availability_lines()),
        "cv headline": cv_copy.headline(),
        "cv skill groups": str(cv_copy.skill_groups()),
        "cv_copy.all_text": cv_copy.all_text(),
        "kit closing": profile.get()["letters"]["closing"],
    }


@pytest.mark.parametrize("source", sorted(_sources()))
def test_sources_carry_no_banned_terms(source):
    text = _sources()[source]
    for pattern, why in honesty.banned():
        hit = re.search(pattern, text, flags=re.I)
        context = text[max(0, hit.start() - 40):hit.end() + 40] if hit else ""
        assert hit is None, f"{source} contains banned text ({why}): …{context!r}…"


def test_identity_comes_from_the_profile():
    p = profile.get()
    assert (p["name"], p["email"]) == ("Alex Example", "alex@example.com")
    assert profile.cv_filename() == "Alex_Example_CV.pdf"


def test_availability_is_one_sentence_everywhere():
    sentence = writer.availability_sentence()
    assert sentence == profile.get()["letters"]["availability_sentence"]
    assert sentence in writer.system_prompt()
    assert kit._closing_marker() in sentence  # a kit body that already says it gets no second closing
    assert "up to 20 hours per week" in " ".join(cv_copy.availability_lines())


def test_writer_prompt_cites_only_verified_claims():
    prompt = writer.system_prompt()
    for claim in profile.get()["verified_claims"]:
        assert claim["claim"] in prompt
    assert "NEVER write any other number" in prompt


def test_writer_rules_carry_the_profiles_extra_rules():
    """The prohibitions live in rules_text(), outside the scanned claim surface,
    so pin them here or a rewrite could silently drop them."""
    rules = writer.rules_text()
    for rule in profile.get()["letters"]["rules"]:
        assert rule in rules
    assert "TRUST BOUNDARY" in rules
    assert writer.system_prompt() == f"{writer.rules_text()}\n{writer.facts_text()}"


def test_cv_renders_one_page_with_the_name_in_its_metadata(tmp_path):
    out = cv_builder.build_cv(str(tmp_path / profile.cv_filename()))
    pdf = Path(out).read_bytes()
    assert len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf)) == 1
    assert profile.get()["name"].encode() in pdf  # /Title and /Author carry the name


def test_page_guard_does_not_depend_on_an_optional_package(tmp_path, monkeypatch):
    """Without a PDF library the old page counter returned 1 for every PDF and the
    one-page guard silently stopped working. The guard counts pages on its own."""
    import builtins

    real_import = builtins.__import__

    def no_pypdf(name, *a, **kw):
        if name.lower().startswith(("pypdf", "pypdf2")):
            raise ImportError(name)
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_pypdf)
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    two = tmp_path / "two.pdf"
    c = canvas.Canvas(str(two), pagesize=A4)
    c.drawString(72, 720, "one")
    c.showPage()
    c.drawString(72, 720, "two")
    c.save()
    assert cv_builder.page_count(str(two)) == 2
