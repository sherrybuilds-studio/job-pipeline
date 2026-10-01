"""roletype: one employment-type rule shared by the sourcing pass and the send
gate. Werkstudent beats internship beats full-time; a posting that states no
student role is unspecified; a navigation item on the employer page is never
evidence; only types the profile accepts pass."""
from pipeline import roletype
from pipeline.roletype import FULL_TIME, INTERNSHIP, UNSPECIFIED, WERKSTUDENT, classify


def test_werkstudent_markers_in_a_title_or_a_content_line():
    assert classify("", title="Werkstudent (m/w/d) Softwareentwicklung") == WERKSTUDENT
    assert classify("", title="Working Student AI Engineering") == WERKSTUDENT
    assert classify("", title="Studentische Hilfskraft (d/w/m) Data") == WERKSTUDENT
    assert classify("Du unterstützt uns als Werkstudent:in mit 20 Stunden pro Woche im Backend-Team.",
                    title="Software Engineer (m/w/d)") == WERKSTUDENT
    assert classify("Student (m/w/d) für 20 Std./Woche neben dem Studium gesucht.") == WERKSTUDENT
    assert classify("", title="Studentische Aushilfe (d/m/w) Schwerpunkt Datenanalyse") == WERKSTUDENT
    assert classify("", title="Workstudent R&D (m/f/d)") == WERKSTUDENT


def test_internship_and_full_time_and_unspecified():
    assert classify("Sechsmonatiges Praktikum in Vollzeit im AI-Team.", title="Praktikum AI Engineering") == INTERNSHIP
    assert classify("", title="Engineering Intern") == INTERNSHIP
    assert classify("Wir suchen dich in Vollzeit (40h), unbefristet.", title="AI Engineer (m/w/d)") == FULL_TIME
    assert classify("Python, SQL, 20 hours per week, hybrid in Berlin.", title="Junior Data Engineer") == UNSPECIFIED
    assert classify("") == UNSPECIFIED


def test_werkstudent_wins_when_a_posting_offers_both():
    assert classify("", title="Software Engineer Intern / Working Student") == WERKSTUDENT
    assert classify("Praktikum oder Werkstudententätigkeit möglich, 15–20 Stunden pro Woche.",
                    title="Data Engineering (m/w/d)") == WERKSTUDENT


def test_title_decides_when_it_carries_a_marker():
    # an internship title stays an internship even when boilerplate mentions working students
    body = "Wir bieten außerdem Werkstudentenstellen in allen Bereichen an, sprich uns gern darauf an."
    assert classify(body, title="Praktikum Softwareentwicklung (m/w/d)") == INTERNSHIP
    assert classify("Als Werkstudent:in unterstützt du das Team an 20 Stunden pro Woche.",
                    title="AI Engineer (Vollzeit)") == FULL_TIME


def test_navigation_items_on_an_employer_page_are_not_evidence():
    page = "Karriere\nJobs finden\nWerkstudenten\nPraktikum\nVollzeit\nAI Engineer (m/w/d)\nDu arbeitest in Vollzeit."
    assert classify(page) == FULL_TIME
    # the same word on a real content line is evidence
    page2 = page + "\nDie Stelle ist auch als Werkstudent:in mit 20 Stunden pro Woche möglich."
    assert classify(page2) == WERKSTUDENT
    assert roletype.drop_reason(INTERNSHIP).startswith("internship")
    assert roletype.drop_reason(UNSPECIFIED).startswith("no Werkstudent")


def test_accepted_types_come_from_the_profile(profile_with):
    assert roletype.is_accepted(WERKSTUDENT) and not roletype.is_accepted(INTERNSHIP)
    profile_with({"search": {"role_types": ["werkstudent", "internship"]}})
    assert roletype.is_accepted(INTERNSHIP) and not roletype.is_accepted(FULL_TIME)
    assert not roletype.is_accepted(UNSPECIFIED)
