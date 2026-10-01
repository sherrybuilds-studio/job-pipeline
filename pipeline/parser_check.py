"""ATS self-check: does the generated CV survive text extraction?

A CV can look right to a person and still come out garbled in an applicant
tracking system. This reads the PDF back as text and checks that the skills it
was tailored for, the name, the e-mail and the phone number all come out.
"""
import logging
import os

from pipeline.pdftext import extract_text

logger = logging.getLogger(__name__)


def check_cv(pdf_path: str, keywords: list[str], candidate: dict) -> dict:
    """{ok, missing_keywords, parse_errors, found_contact} for one CV."""
    result = {"ok": True, "missing_keywords": [], "parse_errors": [], "found_contact": False}
    if not os.path.exists(pdf_path):
        result["ok"] = False
        result["parse_errors"].append(f"file not found: {pdf_path}")
        return result
    text = extract_text(pdf_path)
    if not text.strip():
        result["ok"] = False
        result["parse_errors"].append("no text extracted: the CV may be image-only or corrupted")
        return result

    low = text.lower()
    result["missing_keywords"] = [kw for kw in keywords if kw.lower() not in low]
    name = candidate.get("name", "").lower()
    email = candidate.get("email", "").lower()
    phone = candidate.get("phone", "")
    if name and name not in low:
        result["parse_errors"].append("name not found in the extracted text")
    if email and email not in low:
        result["parse_errors"].append("e-mail not found in the extracted text")
    if phone and phone.replace(" ", "") not in text.replace(" ", ""):
        result["parse_errors"].append("phone not found in the extracted text")
    result["found_contact"] = bool(name and email and name in low and email in low)
    result["ok"] = not result["missing_keywords"] and not result["parse_errors"]
    if result["ok"]:
        logger.info(f"parser check passed: {len(keywords)} keywords found, contact details readable")
    else:
        logger.warning(f"parser check failed: missing {result['missing_keywords']}, errors {result['parse_errors']}")
    return result
