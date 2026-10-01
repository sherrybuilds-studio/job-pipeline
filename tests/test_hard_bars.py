"""Hard bars: disqualifiers checked BEFORE anything is scored, surfaced or kitted.

With the example profile all three are on: German at C1 or above required, a
running or finished master's required, any finished degree required. A
preference ("C1 von Vorteil", "Master wünschenswert") is not a bar, and
anything that also accepts a bachelor ("Bachelor- oder Masterstudium") is not a
master's bar.
"""
from pipeline.matcher import hard_bar_reason, match_jobs


def job(description: str, title: str = "Werkstudent AI Engineer") -> dict:
    return {"title": title, "company": "Test GmbH", "url": "https://x.test/1", "location": "Berlin",
            "description": description}


# ── language bar ─────────────────────────────────────────────────────────────

def test_german_c1_requirement_is_a_bar():
    r = hard_bar_reason(job("Du hast Deutschkenntnisse C1 und arbeitest mit Python."))
    assert r and r.startswith("german-c1")


def test_german_c2_and_english_phrasings_are_bars():
    for text in ("German C2 required", "Deutsch C1 zwingend", "verhandlungssicheres Deutsch",
                 "muttersprachliche Deutschkenntnisse", "fluent German is required", "Du sprichst fließend Deutsch"):
        assert hard_bar_reason(job(text)), text


def test_the_explicit_cannot_consider_wording_is_a_bar():
    r = hard_bar_reason(job("Deutsch sprichst du mindestens auf C1-Niveau. Ohne dieses Niveau können wir "
                            "keine Bewerbung berücksichtigen."))
    assert r and r.startswith("german-c1")


def test_c1_as_a_preference_is_not_a_bar():
    for text in ("Deutschkenntnisse auf C1-Niveau sind von Vorteil", "Kein Muss, aber ein Plus: Deutsch C1",
                 "German at C1 level is a plus", "Deutsch C1 wünschenswert"):
        assert hard_bar_reason(job(text)) is None, text


def test_sehr_gute_deutschkenntnisse_is_not_a_bar():
    """A deliberate boundary: "very good German" is kept and flagged, not dropped."""
    assert hard_bar_reason(job("Sehr gute Deutschkenntnisse runden dein Profil ab.")) is None


def test_plain_german_listing_without_a_level_is_not_a_bar():
    assert hard_bar_reason(job("Wir suchen dich als Werkstudent für unser Team in Berlin.")) is None


# ── degree bars ──────────────────────────────────────────────────────────────

def test_running_masters_requirement_is_a_bar():
    r = hard_bar_reason(job("Laufendes Master-Studium der Wirtschaftsinformatik oder Data Science"))
    assert r and r.startswith("masters-degree")


def test_completed_degree_requirement_is_a_bar():
    for text in ("Abgeschlossenes Studium der Informatik", "abgeschlossenes Hochschulstudium",
                 "A completed Master's degree in Computer Science", "Master degree required"):
        assert hard_bar_reason(job(text)), text
    assert hard_bar_reason(job("Abgeschlossenes Studium der Informatik")).startswith("completed-degree")


def test_bachelor_or_master_is_not_a_bar():
    for text in ("Sehr gute Studienleistungen im fortgeschrittenen Bachelor- oder im Masterstudium",
                 "Aktuelles Studium (Bachelor oder Master) mit Schwerpunkt Daten",
                 "You are pursuing a Bachelor's or Master's degree"):
        assert hard_bar_reason(job(text)) is None, text


def test_masters_as_a_preference_is_not_a_bar():
    assert hard_bar_reason(job("Masterstudium von Vorteil, Bachelor genügt")) is None


def test_enrolled_student_wording_is_not_a_bar():
    assert hard_bar_reason(job("Du bist immatrikulierte:r Student:in einer Hochschule")) is None


def test_completed_bachelor_is_still_a_bar_even_though_bachelor_appears():
    """For someone still studying, a FINISHED bachelor is out of reach too, so the
    bachelor rescue must not apply to "abgeschlossen" wording."""
    assert hard_bar_reason(job("Abgeschlossenes Bachelorstudium der Informatik"))


# ── both bars, the title, and the gate's place in match_jobs ─────────────────

def test_both_bars_report_both_reasons():
    r = hard_bar_reason(job("Laufendes Masterstudium. Deutschkenntnisse C1 erforderlich."))
    assert "german-c1" in r and "masters-degree" in r


def test_bar_in_the_title_counts():
    assert hard_bar_reason(job("Python", title="Werkstudent (abgeschlossenes Studium) AI"))


def _passing_job(extra: str = "") -> dict:
    return job("Werkstudent für AI und Automatisierung in Berlin. Du arbeitest mit Python, LLMs, FastAPI und "
               "Playwright an RAG-Pipelines und Docker-Deployments. " + extra)


def test_match_jobs_keeps_a_clean_listing():
    assert len(match_jobs([_passing_job()], min_score=0)) == 1


def test_match_jobs_drops_a_c1_listing_before_scoring():
    assert match_jobs([_passing_job("Deutschkenntnisse C1 erforderlich.")], min_score=0) == []


def test_match_jobs_drops_a_masters_listing_before_scoring():
    assert match_jobs([_passing_job("Laufendes Masterstudium erforderlich.")], min_score=0) == []


def test_match_jobs_keeps_a_preference_listing():
    assert len(match_jobs([_passing_job("Deutsch C1 von Vorteil.")], min_score=0)) == 1


# ── regressions from real listings ───────────────────────────────────────────

def test_react_native_next_to_the_country_name_is_not_a_language_bar():
    """"React Native" next to "Berlin, Germany" once read as "native German".
    The country name is not the language."""
    assert hard_bar_reason(job(
        "Our other open positions Mobile Engineer (React Native) Berlin, Germany "
        "Employee Software Development React Native Developer Berlin, Germany Employee")) is None


def test_fluent_english_in_a_german_country_line_is_not_a_bar():
    assert hard_bar_reason(job("Fluent English required. Office in Berlin, Germany.")) is None


def test_a_preference_in_a_previous_sentence_does_not_rescue_a_real_bar():
    """"If you live in Berlin that is a plus" sat one sentence before a C1 gate
    and once suppressed it. A preference only counts in the same sentence."""
    r = hard_bar_reason(job(
        "Kein Muss, aber ein Plus: erste eigene Projekte. "
        "Wenn du in Berlin wohnst, ist das ein Plus, und du studierst noch mindestens drei Semester. "
        "Deine Deutschkenntnisse liegen mindestens auf C1-Niveau; Englisch sprichst du ebenfalls. "
        "Ohne C1 können wir keine Bewerbung berücksichtigen."))
    assert r and r.startswith("german-c1")


# ── the bars follow the profile ──────────────────────────────────────────────

def test_bars_switched_off_in_the_profile_do_not_apply(profile_with):
    profile_with({"hard_bars": {"german_c1": False, "masters_degree": True, "completed_degree": False}})
    assert hard_bar_reason(job("Deutschkenntnisse C1 erforderlich.")) is None
    assert hard_bar_reason(job("Abgeschlossenes Studium der Informatik")) is None
    assert hard_bar_reason(job("Laufendes Masterstudium erforderlich.")).startswith("masters-degree")
