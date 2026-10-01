"""The rule-based scorer and its gates. _score_job is pure (no network, no model,
no environment), so it is the cheapest part to pin, and the daily ranking
depends on it."""
from pipeline import matcher
from pipeline.matcher import _location_tier, _passes_entry_gate, _score_job


def test_relevant_role_scores_positive():
    result = _score_job({"title": "Working Student AI Engineer", "description": "Python, FastAPI, RAG, ChromaDB."})
    assert result["score"] > 0
    assert {"Python", "RAG and vectors", "APIs"} <= set(result["matched_skills"])


def test_german_required_is_penalised():
    english = _score_job({"title": "AI Engineer", "description": "Python, FastAPI. English team."})
    german = _score_job({"title": "AI Engineer", "description": "Python, FastAPI. Deutsch C1 zwingend erforderlich."})
    assert german["score"] < english["score"]
    assert german["red_flags"] == ["German C1 or C2 required"]


def test_score_never_negative():
    assert _score_job({"title": "Steuerberater", "description": "Deutsch C2 erforderlich, Buchhaltung."})["score"] >= 0


def test_a_skill_the_profile_lacks_in_the_title_costs_points():
    plain = _score_job({"title": "Working Student Backend", "description": "Python services."})
    kube = _score_job({"title": "Working Student Kubernetes Backend", "description": "Python services."})
    assert kube["score"] == plain["score"] - 15


def test_max_score_is_the_sum_of_the_profile_weights_and_bonuses():
    from pipeline import profile
    s = profile.get()["scoring"]
    assert matcher.max_score() == sum(w for _, w, _ in s["skill_weights"]) + 30
    everything = " ".join(kw for kws, _, _ in s["skill_weights"] for kw in kws)
    best = _score_job({"title": "Werkstudent AI", "description": f"{everything} in Berlin", "location": "Berlin"})
    assert best["score"] == matcher.max_score()


def _job(title, desc="", location="Berlin"):
    return {"title": title, "description": desc, "location": location}


def test_gate_passes_working_student_intern_and_part_time():
    assert _passes_entry_gate(_job("Werkstudent AI Automation (m/w/d)"))
    assert _passes_entry_gate(_job("Working Student Backend"))
    assert _passes_entry_gate(_job("Python Developer", "Part-time, 20 hours per week"))
    assert _passes_entry_gate(_job("Praktikum Softwareentwicklung"))


def test_gate_rejects_full_time_junior_and_senior_titles():
    assert not _passes_entry_gate(_job("Junior Python Developer (m/f/d)", "Full-time, 40h"))
    assert not _passes_entry_gate(_job("Software Engineer (2026 Graduate)", "Full-time"))
    assert not _passes_entry_gate(_job("AI Engineer", "Berlin office, permanent"))
    assert not _passes_entry_gate(_job("Senior Werkstudent Lead"))


def test_location_tiers_follow_the_profile_city(profile_with):
    assert _location_tier(_job("Werkstudent", location="Berlin, Germany"))[1] == "Berlin"
    assert _location_tier(_job("Werkstudent", "Remote", "Germany")) == (6, "Remote (Germany)")
    assert _location_tier(_job("Werkstudent", location="München")) is None  # onsite elsewhere
    assert _location_tier(_job("Werkstudent", location="USA only")) is None
    profile_with({"search": {"city": "Munich"}})
    assert _location_tier(_job("Werkstudent", location="München"))[1] == "Munich"
    assert _location_tier(_job("Werkstudent", location="Berlin")) is None


def test_match_jobs_returns_the_best_first():
    jobs = [_job("Werkstudent Python", "Python und SQL in Berlin. 20 Stunden."),
            _job("Werkstudent AI Engineer", "Python, LLM, RAG, FastAPI, Docker and ETL pipelines. 20 hours.")]
    for j, n in zip(jobs, (1, 2), strict=True):
        j.update(company=f"C{n}", url=f"https://c{n}.example/j")
    ranked = matcher.match_jobs(jobs, min_score=0)
    assert [j["company"] for j in ranked] == ["C2", "C1"]
