"""Output lint on every generated CV and cover letter.

Runs AFTER generation, on the files on disk, and never regenerates or edits
what the writer produced. A kit with a single hit is marked needs-human-fix in
status.json and gets a report (lint.md and lint.json) with the exact text of
every hit and a proposed replacement; the send gate refuses it until a person
has fixed it.

    python -m pipeline lint                  # every unsent kit under data/applications
    python -m pipeline lint <folder> [...]   # named kits
    python -m pipeline lint --dry            # report only, write nothing

Rules; one hit fails the kit:
  1. slash        no "/" in prose or skill lists ("Python / FastAPI", "AI/ML",
                  "CI/CD" become words). URLs and dates are the only exemptions.
  2. machine-tell "I am thrilled, excited or passionate", leverage, synergy,
                  delve, "in today's fast-paced", three or more em dashes in one
                  paragraph, a stack of three adjectives, and an opening sentence
                  identical to another letter's.
  3. buzzword     a skill claim with no concrete instance from the profile in
                  the same sentence (a project, a verified number, a named
                  artefact); a bare skill list; a skill from the profile's
                  not_my_skills that is not stated as a gap. On the CV the unit is
                  the page: every skill chip must be evidenced by a line on it.
  4. format       placeholders ("[Start year]", TODO, {{…}}) and every pattern on
                  the profile's honesty list.

The rules are deterministic regular expressions over plain text; the CV text
comes from pdftext.extract_text.
"""
import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from pipeline import config, honesty, pdftext, profile
from pipeline.cv_builder import CONTACT_HEADING, SKILLS_HEADING

APPS_DIR = config.APPS_DIR
RULES = ("slash", "machine-tell", "buzzword", "format")
LETTER_FILE, CV_FILE = "cover_letter.txt", "cv.pdf"


@dataclass
class Hit:
    rule: str
    file: str
    text: str
    proposal: str
    where: str = ""


# ─── text helpers ────────────────────────────────────────────────────────────

_GREETING = re.compile(r"^(dear|sehr geehrte|hallo|liebe|hi|hello|guten tag)\b", re.I)
_SIGNOFF = re.compile(r"^(kind regards|best regards|warm regards|regards|sincerely|yours|mit freundlichen|"
                      r"viele grüße|beste grüße|freundliche grüße|herzliche grüße)\b", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ\"'(])")


def letter_parts(text: str) -> tuple[str, list[str], str]:
    """(greeting, body paragraphs, sign-off) of a plain-text letter."""
    lines = (text or "").replace("\r", "").split("\n")
    greeting = ""
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is not None and _GREETING.match(lines[first].strip()):
        greeting = lines[first].strip()
        lines = lines[first + 1:]
    signoff = ""
    for i, ln in enumerate(lines):
        if _SIGNOFF.match(ln.strip()):
            signoff = "\n".join(lines[i:]).strip()
            lines = lines[:i]
            break
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
    return greeting, [" ".join(p.split()) for p in paragraphs], signoff


def sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(paragraph or "") if s.strip()]


def norm_sentence(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9äöüß ]", "", (s or "").lower()).split())


def opening_sentence(text: str) -> str:
    _, paragraphs, _ = letter_parts(text)
    return norm_sentence(sentences(paragraphs[0])[0]) if paragraphs and sentences(paragraphs[0]) else ""


def _group_labels() -> set[str]:
    return {label for label, _ in profile.get()["cv"]["skill_groups"]}


def cv_parts(cv_text: str) -> tuple[str, list[str]]:
    """(main-column prose, skill chips) from extracted CV text. The sidebar
    (CONTACT … TECHNICAL SKILLS) follows the main column in the content stream."""
    lines = [ln.strip() for ln in (cv_text or "").splitlines()]
    cut = lines.index(CONTACT_HEADING) if CONTACT_HEADING in lines else len(lines)
    main = "\n".join(ln for ln in lines[:cut] if ln)
    chips: list[str] = []
    if SKILLS_HEADING in lines:
        labels = _group_labels()
        chips = [ln for ln in lines[lines.index(SKILLS_HEADING) + 1:] if ln and ln not in labels]
    return main, chips


def _context(text: str, pos: int, width: int = 40) -> str:
    return "…" + " ".join((text or "")[max(0, pos - width):pos + width].split()) + "…"


# ─── rule 1: slashes ─────────────────────────────────────────────────────────

_EXEMPT = re.compile(
    r"(?:https?://|www\.)\S+"                                             # URLs
    r"|\b[\w.-]+\.(?:com|de|io|ai|org|net|dev|app|co|eu)(?:/[^\s)\]]*)?"  # bare domains with paths
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}/\d{4}\b|\b(?:19|20)\d{2}/(?:19|20)?\d{2}\b",  # dates
    re.I)
_SLASH_TERM = re.compile(r"[^\s/()\[\],;:]+(?:\s*/\s*[^\s/()\[\],;:]+)+")
_KNOWN = {
    "ci/cd": "continuous integration and delivery", "ai/ml": "AI and machine learning", "and/or": "or",
    "typescript/next.js": "TypeScript and Next.js", "git/github": "Git and GitHub", "ux/ui": "UX and UI",
    "b2b/b2c": "B2B and B2C", "supabase/postgres": "Supabase and Postgres", "w/": "with", "n/a": "not applicable",
    "24/7": "remove: an availability claim nobody measured", "asr→llm→tts": "speech to text, model, text to speech",
    "werkstudent/working student": "Werkstudent", "werkstudent / working student": "Werkstudent",
    "h/week": "hours per week", "h/woche": "Stunden pro Woche", "std./woche": "Stunden pro Woche",
    "werkstudent/working": "Werkstudent (working student)",
}
_GENDER = re.compile(r"^(?:[mwfdxhgn]\s*/\s*)+[mwfdxhgn]$", re.I)
_PER_UNIT = re.compile(r"(.+?)\s*/\s*(week|wk|month|day|hour|hr|year|woche|monat|tag|stunde|jahr|call|message|msg|"
                       r"run|user|request)s?\.?$", re.I)


def propose_slash(term: str) -> str:
    key = " ".join(term.lower().split())
    compact = key.replace(" ", "")
    if compact in _KNOWN:
        return _KNOWN[compact]
    if key in _KNOWN:
        return _KNOWN[key]
    if _GENDER.match(compact):
        return "drop the gender marker"
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", compact)
    if m:
        return f"{m.group(1)} of {m.group(2)}"
    m = re.fullmatch(r"(\d+)\s*(?:h|hrs|hours)?\s*/\s*(?:week|woche|wk)", compact, re.I)
    if m:
        return f"{m.group(1)} hours per week"
    if re.search(r"/(?:hr|hour|h|std|stunde)$", compact, re.I):
        return re.sub(r"\s*/\s*(?:hr|hour|h|std|stunde)$", " per hour", term, flags=re.I)
    m = _PER_UNIT.fullmatch(term.strip())
    if m:
        left = {"hrs": "hours", "h": "hours", "std": "Stunden"}.get(m.group(1).lower(), m.group(1))
        return f"{left} per {m.group(2).lower()}"
    parts = [p.strip() for p in term.split("/") if p.strip()]
    return " and ".join(parts) if len(parts) > 1 else term


def lint_slashes(text: str, file: str) -> list[Hit]:
    masked = _EXEMPT.sub(lambda m: " " * len(m.group(0)), text or "")
    hits: list[Hit] = []
    for m in _SLASH_TERM.finditer(masked):
        if "/" not in m.group(0):
            continue
        original = (text or "")[m.start():m.end()].rstrip(".!?\"'")
        hits.append(Hit("slash", file, original, propose_slash(original), _context(text, m.start())))
    return hits


# ─── rule 2: machine tells ───────────────────────────────────────────────────

_TELLS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bI(?:'m| am| was| would be|'d be)\s+(?:\w+\s+){0,2}(?:thrilled|excited|passionate)\b", re.I),
     "state the fact instead: what you built that matches the posting"),
    (re.compile(r"\bleverag(?:e|es|ed|ing)\b", re.I), "use, build on, run"),
    (re.compile(r"\bsynerg\w*", re.I), "say what the two things do together, concretely"),
    (re.compile(r"\bdelv(?:e|es|ed|ing)\b", re.I), "read, look into, work through"),
    (re.compile(r"\bin today'?s fast[- ]paced\b", re.I), "delete the phrase"),
    (re.compile(r"\b(?:thrive|thrives|thriving) in (?:a )?fast[- ]paced\b", re.I), "delete the phrase"),
]
_ADJ_LEXICON = {
    "strong", "robust", "reliable", "scalable", "efficient", "seamless", "dynamic", "innovative", "proactive",
    "motivated", "dedicated", "passionate", "driven", "hands-on", "fast", "clean", "modern", "secure", "flexible",
    "agile", "curious", "eager", "keen", "quick", "smart", "solid", "deep", "broad", "practical", "technical",
    "creative", "analytical", "structured", "independent", "thorough", "precise", "pragmatic", "resilient",
    "ambitious", "collaborative", "communicative", "open", "honest", "direct", "hungry", "humble", "adaptable",
    "versatile", "detail-oriented", "results-driven", "self-motivated", "cutting-edge", "state-of-the-art",
    "end-to-end", "full-stack", "high-quality", "real-world", "production-ready", "user-friendly",
    "cost-effective", "data-driven", "well-tested", "well-documented", "new", "good", "great", "big", "small",
    "simple", "complex", "safe", "stable", "maintainable", "testable", "readable", "responsive", "accurate",
}
_ADJ_SUFFIX = re.compile(r"(?:ive|ous|ful|able|ible|ical|ic|al|ent|ant|ary|less|ish|ed)$")
_STACK = re.compile(r"\b([a-zäöü][\w-]+), ([a-zäöü][\w-]+),? and ([a-zäöü][\w-]+)\b")


def _is_adjective(word: str) -> bool:
    w = word.lower()
    return w in _ADJ_LEXICON or (len(w) > 4 and bool(_ADJ_SUFFIX.search(w)))


def lint_machine_tells(text: str, file: str, other_openings: dict[str, list[str]] | None = None, *,
                       em_dash: bool = True) -> list[Hit]:
    """em_dash=False for CV text, which has no paragraphs (one page of wrapped lines)."""
    hits: list[Hit] = []
    _, paragraphs, _ = letter_parts(text)
    for para in paragraphs:
        for pat, proposal in _TELLS:
            for m in pat.finditer(para):
                hits.append(Hit("machine-tell", file, m.group(0), proposal, _context(para, m.start())))
        dashes = para.count("—") if em_dash else 0
        if dashes >= 3:
            hits.append(Hit("machine-tell", file, f"{dashes} em dashes in one paragraph",
                            "split into sentences; at most two dashes per paragraph", para[:80] + "…"))
        for m in _STACK.finditer(para):
            if all(_is_adjective(w) for w in m.groups()):
                hits.append(Hit("machine-tell", file, m.group(0),
                                "keep the one adjective that is true, or use a number", _context(para, m.start())))
    if other_openings and paragraphs:
        opening = opening_sentence(text)
        twins = other_openings.get(opening) if opening else None
        if twins:
            hits.append(Hit("machine-tell", file, sentences(paragraphs[0])[0],
                            f"identical opening sentence in: {', '.join(sorted(twins))}; open with this posting's gap",
                            "opening sentence"))
    return hits


# ─── rule 3: empty buzzwords ─────────────────────────────────────────────────

def _anchors() -> re.Pattern:
    """Anything that ties a claim to something real: the profile's evidence
    anchors, project names and keys, roles, employers and the school."""
    def build(p: dict) -> re.Pattern:
        words = list(p["evidence_anchors"])
        for proj in p["projects"]:
            words += [proj["name"], proj["key"]] + ([proj["repo"]] if proj["repo"] else [])
        for e in p["experience"]:
            words += [e["role"], e["company"]]
        words.append(p["education"]["school"])
        words = sorted({w for w in words if w}, key=len, reverse=True)
        return re.compile("|".join(re.escape(w) for w in words), re.I)
    return profile.derived("lint.anchors", build)


def _not_mine() -> re.Pattern | None:
    """The profile's not_my_skills; short names ("Go", "R") match case-sensitively,
    so "results go to the database" is not a claim about Go."""
    def build(p: dict) -> re.Pattern | None:
        skills = p["not_my_skills"]
        if not skills:
            return None
        long_ = [rf"\b{re.escape(s)}\b" for s in skills if len(s) > 3]
        short = [rf"(?<![\w+])(?-i:{re.escape(s)})(?![\w+])" for s in skills if len(s) <= 3]
        return re.compile("|".join(long_ + short), re.I)
    return profile.derived("lint.not_mine", build)


_BUZZ = [
    "ai", "machine learning", "ml", "llm", "llms", "automation", "automations", "cloud", "devops",
    "data engineering", "scalable", "scalability", "end-to-end", "full-stack", "microservices", "agentic",
    "ai agents", "genai", "generative ai", "innovation", "innovative", "cutting-edge", "state-of-the-art",
    "best practices", "digital transformation", "big data", "data-driven", "problem-solving", "team player",
    "fast learner", "self-starter", "communication skills", "hands-on", "prompt engineering", "vector database",
    "sql", "postgres", "postgresql", "docker", "kubernetes", "aws", "azure", "gcp", "python", "fastapi",
    "typescript", "next.js", "react", "playwright", "n8n", "zapier", "rag", "chromadb", "supabase", "linux",
    "ci/cd", "github actions", "apis", "api", "rest", "etl", "pipelines", "pipeline", "backend", "frontend",
    "vps", "telegram", "claude", "openai", "langchain", "embeddings", "semantic search",
]
_BUZZ_RE = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(b) for b in sorted(_BUZZ, key=len, reverse=True)) +
                      r")(?![\w-])", re.I)
_NEGATED = re.compile(r"\b(?:not|no|never|haven'?t|have not|without|lack|gap|yet|nor)\b", re.I)
_FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|I'd|my|me|mine|we|our)\b")
_STOP = {"and", "the", "for", "with", "api", "bot", "of"}
_LEGAL_WORDS = {"gmbh", "ag", "se", "kg", "co", "kgaa", "inc", "ltd", "group", "the", "und", "&", "e.v.", "ug"}


def _company_names(company: str) -> list[str]:
    """Lower-case name tokens a letter would use for the employer ("Acme Tech GmbH" → acme, tech)."""
    words = [w for w in re.split(r"[\s(),/]+", (company or "").lower()) if w]
    return [w for w in words if w not in _LEGAL_WORDS and len(w) >= 3][:2]


def lint_buzzwords_prose(text: str, file: str, company: str = "") -> list[Hit]:
    """Letters: a sentence that names a skill or buzzword must carry a concrete
    instance from the profile in the same sentence. A claim is a sentence in the
    first person, or a list of three or more terms; a sentence about the
    company's product is not a claim, nor is one that names the company (the
    "why this company" line quotes their stack)."""
    hits: list[Hit] = []
    names = _company_names(company)
    anchors = _anchors()
    not_mine = _not_mine()
    _, paragraphs, _ = letter_parts(text)
    for para in paragraphs:
        for sent in sentences(para):
            terms = sorted({m.group(0) for m in _BUZZ_RE.finditer(sent)}, key=str.lower)
            about_them = bool(names) and any(n in sent.lower() for n in names)
            gap = bool(_NEGATED.search(sent))  # "I have not used X" names a gap and claims nothing
            claim = bool(terms) and not about_them and not gap and (bool(_FIRST_PERSON.search(sent)) or len(terms) >= 3)
            if claim and not anchors.search(sent):
                hits.append(Hit("buzzword", file, sent, "name the project where you used it (and a verified number), "
                                                       "or cut the sentence", f"bare: {', '.join(terms)}"))
            if not_mine is not None:
                for m in not_mine.finditer(sent):
                    if not _NEGATED.search(sent):
                        hits.append(Hit("buzzword", file, sent, f"{m.group(0)} is not a skill in the profile; "
                                                               "remove it or state it as a gap",
                                        f"not-my-skill: {m.group(0)}"))
    return hits


def lint_cv_skills(cv_text: str, file: str) -> list[Hit]:
    """CV: a skill chip with no evidence line on the page is an empty claim."""
    main, chips = cv_parts(cv_text)
    hay = main.lower()
    hits: list[Hit] = []
    for chip in chips:
        tokens = [t.strip(".,;:()").lower() for t in re.split(r"[\s,]+", chip.strip()) if t]
        tokens = [t for t in tokens if t and t not in _STOP and (len(t) >= 3 or t.isupper() or len(t) == 2)]
        if tokens and not any(re.search(rf"(?<![\w.]){re.escape(t)}(?![\w])", hay) for t in tokens):
            hits.append(Hit("buzzword", file, chip, "add the line that evidences it (which project, what it did) "
                                                     "or drop the chip", "skill chip with no instance on the page"))
    not_mine = _not_mine()
    if not_mine is not None:
        for m in not_mine.finditer(main):
            sent = _context(main, m.start())
            if not _NEGATED.search(sent):
                hits.append(Hit("buzzword", file, m.group(0), "not a skill in the profile; remove it", sent))
    return hits


# ─── rule 4: format and honesty ──────────────────────────────────────────────

_PLACEHOLDER = re.compile(r"\[[^\]\n]{1,40}\]|\{\{[^}\n]*\}\}|\bTODO\b|\bTBD\b|\bXXX\b|lorem ipsum|"
                          r"<(?:name|company|date|year|role|title|position)[^>]*>|\[?insert [^\]\n]{1,30}\]?", re.I)


def lint_format(text: str, file: str) -> list[Hit]:
    hits = [Hit("format", file, m.group(0), "fill in the real value", _context(text, m.start()))
            for m in _PLACEHOLDER.finditer(text or "")]
    for reason in honesty.gate(text or ""):
        why, _, ctx = reason.partition(": ")
        hits.append(Hit("format", file, ctx.strip("…'\" "), f"remove: {why} (honesty list)", why))
    return hits


# ─── per file and per kit ────────────────────────────────────────────────────

def lint_letter(text: str, other_openings: dict[str, list[str]] | None = None, file: str = LETTER_FILE,
                company: str = "") -> list[Hit]:
    return (lint_slashes(text, file) + lint_machine_tells(text, file, other_openings)
            + lint_buzzwords_prose(text, file, company) + lint_format(text, file))


def lint_cv_text(cv_text: str, file: str = CV_FILE) -> list[Hit]:
    main, _ = cv_parts(cv_text)
    return (lint_slashes(cv_text, file) + lint_machine_tells(main, file, em_dash=False)
            + lint_cv_skills(cv_text, file) + lint_format(cv_text, file))


def other_openings(apps_dir: Path, exclude: Path | None = None) -> dict[str, list[str]]:
    """Normalised opening sentence → kit folders that use it (all letters on disk)."""
    out: dict[str, list[str]] = {}
    for letter in Path(apps_dir).glob(f"*/{LETTER_FILE}"):
        if exclude is not None and letter.parent.resolve() == Path(exclude).resolve():
            continue
        try:
            opening = opening_sentence(letter.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if opening:
            out.setdefault(opening, []).append(letter.parent.name)
    return out


def is_sent(folder: Path) -> bool:
    try:
        st = json.loads((Path(folder) / "status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return st.get("status") == "sent" or str(st.get("response_notes") or "").startswith("sent ")


def lint_kit(folder: Path, apps_dir: Path | None = None, *, write: bool = True,
             openings: dict[str, list[str]] | None = None) -> dict:
    folder = Path(folder)
    apps_dir = Path(apps_dir) if apps_dir is not None else APPS_DIR
    hits: list[Hit] = []
    files: dict[str, str] = {}
    letter_path, cv_path = folder / LETTER_FILE, folder / CV_FILE
    if letter_path.exists():
        text = letter_path.read_text(encoding="utf-8", errors="replace")
        if openings is not None:  # a shared corpus includes this kit: drop its own entry
            corpus = {k: [f for f in v if f != folder.name] for k, v in openings.items()}
            corpus = {k: v for k, v in corpus.items() if v}
        else:
            corpus = other_openings(apps_dir, exclude=folder)
        try:
            company = json.loads((folder / "job.json").read_text(encoding="utf-8")).get("company", "")
        except (OSError, json.JSONDecodeError):
            company = ""
        hits += lint_letter(text, corpus, company=company)
        files[LETTER_FILE] = "linted"
    else:
        files[LETTER_FILE] = "missing"
        hits.append(Hit("format", LETTER_FILE, "cover_letter.txt missing", "generate the letter", ""))
    if cv_path.exists():
        cv_text = pdftext.extract_text(cv_path)
        if cv_text.strip():
            hits += lint_cv_text(cv_text)
            files[CV_FILE] = "linted"
        else:
            files[CV_FILE] = "unreadable"
            hits.append(Hit("format", CV_FILE, "cv.pdf yields no text", "rebuild the CV (an image-only PDF fails ATS "
                                                                        "parsing)", ""))
    else:
        files[CV_FILE] = "missing"
        hits.append(Hit("format", CV_FILE, "cv.pdf missing", "build the tailored CV", ""))

    by_rule = {r: sum(1 for h in hits if h.rule == r) for r in RULES}
    report = {"folder": folder.name, "status": "fail" if hits else "pass", "at": _now(), "files": files,
              "total": len(hits), "by_rule": by_rule, "hits": [asdict(h) for h in hits]}
    if write:
        _write_report(folder, report)
    return report


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write_report(folder: Path, report: dict) -> None:
    (folder / "lint.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "lint.md").write_text(render_md(report), encoding="utf-8")
    status_path = folder / "status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    except json.JSONDecodeError:
        status = {}
    status["lint"] = {"status": report["status"], "at": report["at"], "hits": report["total"],
                      "by_rule": report["by_rule"]}
    if report["status"] == "fail" and status.get("status") in (None, "pending"):
        status["status"] = "needs-human-fix"
    elif report["status"] == "pass" and status.get("status") == "needs-human-fix":
        status["status"] = "pending"
    status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")


def render_md(report: dict) -> str:
    lines = [f"# Lint: {report['folder']}: **{report['status'].upper()}** ({report['total']} hits) · {report['at']}",
             "", "Files: " + ", ".join(f"`{k}` {v}" for k, v in report["files"].items()), "",
             "Fail and report: nothing was regenerated. Fix the text, rebuild the file, then run "
             "`python -m pipeline lint <folder>` again.", ""]
    for rule in RULES:
        rule_hits = [h for h in report["hits"] if h["rule"] == rule]
        if not rule_hits:
            continue
        lines += [f"## {rule} ({len(rule_hits)})", ""]
        for h in rule_hits:
            where = f" _({h['where']})_" if h.get("where") else ""
            lines.append(f"- `{h['file']}`: \"{h['text']}\" → **{h['proposal']}**{where}")
        lines.append("")
    if not report["hits"]:
        lines += ["No hits. The kit may be sent once the send gate passes "
                  "(`python -m pipeline tracker sent <company>`).", ""]
    return "\n".join(lines)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lint generated CVs and cover letters: fail and report, never regenerate")
    ap.add_argument("folders", nargs="*", help="kit folders (default: every unsent kit)")
    ap.add_argument("--apps-dir", default=str(APPS_DIR))
    ap.add_argument("--all", action="store_true", help="include kits already stamped sent")
    ap.add_argument("--dry", action="store_true", help="print the report, write no files")
    args = ap.parse_args(argv)
    apps_dir = Path(args.apps_dir)
    if args.folders:
        folders = [Path(f) for f in args.folders]
    elif apps_dir.exists():
        folders = [d for d in sorted(apps_dir.iterdir())
                   if d.is_dir() and (d / "job.json").exists() and (args.all or not is_sent(d))]
    else:
        folders = []
    corpus = other_openings(apps_dir)
    totals = dict.fromkeys(RULES, 0)
    failed = 0
    for folder in folders:
        report = lint_kit(folder, apps_dir, write=not args.dry, openings=corpus)
        failed += report["status"] == "fail"
        for r in RULES:
            totals[r] += report["by_rule"][r]
        rules = " ".join(f"{r}={n}" for r, n in report["by_rule"].items() if n)
        print(f"{report['status']:4s} {report['total']:3d}  {folder.name[:58]:58s} {rules}")
    print(f"\n{len(folders)} kits linted: {failed} fail, {len(folders) - failed} pass | hits by rule: "
          + ", ".join(f"{r}={n}" for r, n in totals.items()))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
