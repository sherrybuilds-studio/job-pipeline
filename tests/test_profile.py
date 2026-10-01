"""The profile file is the only source of personal data: it must load, fill
defaults, and refuse a broken file with every problem named."""
from pathlib import Path

import pytest
import yaml

from pipeline import profile
from pipeline.profile import ProfileError

EXAMPLE_PROFILE = Path(__file__).resolve().parents[1] / "profile.example.yaml"


def test_example_profile_loads_with_defaults():
    p = profile.get()
    assert p["name"] == "Alex Example" and p["first_name"] == "Alex"
    assert p["search"]["role_types"] == ["werkstudent"]
    assert all(p["hard_bars"].values())
    assert [proj["key"] for proj in p["projects"]] == ["course assistant", "price tracker", "study bot"]
    assert p["letters"]["sign_off"] == "Kind regards,\nAlex Example"
    assert p["cv"]["skill_groups"][0][0] == "Languages and frameworks"


def test_file_names_come_from_the_name():
    assert profile.cv_filename() == "Alex_Example_CV.pdf"
    assert profile.letter_filename("Acme GmbH (Acme Holding)") == "Alex_Example_Cover_Letter_Acme_GmbH.pdf"
    assert profile.file_token("Jürgen Müller-Lüdenscheidt") == "Jürgen_Müller_Lüdenscheidt"


def test_profile_text_carries_the_facts_models_may_use():
    text = profile.profile_as_text()
    for fact in ("Alex Example", "B.Sc. Computer Science", "Werkstudent (working student)", "German (B1, learning)",
                 "German at C1 or above", "34 of 40", "Price Tracker Pipeline", "Teaching Assistant"):
        assert fact in text, fact


def test_missing_profile_says_how_to_create_one(tmp_path):
    with pytest.raises(ProfileError, match="profile.example.yaml"):
        profile.load(tmp_path / "profile.yaml")


def _broken(tmp_path, change) -> str:
    data = yaml.safe_load(EXAMPLE_PROFILE.read_text(encoding="utf-8"))
    change(data)
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ProfileError) as err:
        profile.load(path)
    return str(err.value)


def test_every_problem_is_named(tmp_path):
    def change(d):
        del d["name"]
        d["search"]["role_types"] = ["full_time"]
        d["honesty"]["banned"].append({"pattern": "([unclosed", "reason": "x"})
        d["cv"]["skill_groups"][0]["skills"].append("Cobol")
        d["search"]["queries"]["linkdin"] = ["typo"]
    message = _broken(tmp_path, change)
    for part in ("name is required", "search.role_types", "honesty.banned", "Cobol", "linkdin"):
        assert part in message, part


def test_project_match_patterns_must_compile(tmp_path):
    def change(d):
        d["projects"][0]["match"]["strong"].append("(?P<broken")
    assert "projects[0].match.strong" in _broken(tmp_path, change)


def test_aliases_must_point_at_real_skills(tmp_path):
    def change(d):
        d["skill_aliases"]["Kubernetes"] = "Kubernetes"
    assert "skill_aliases" in _broken(tmp_path, change)


def test_switching_profiles_rebuilds_derived_values(profile_with):
    assert profile.city() == "Berlin"
    p = profile_with({"search": {"city": "Hamburg"}})
    assert p["search"]["city"] == "Hamburg" and profile.city() == "Hamburg"
