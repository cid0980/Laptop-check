"""
grading.py -- turns a pile of CheckResults into one honest number and grade.

Scoring rules
-------------
* Only checks with a numeric score take part in the weighted average.
  A component that is NOT FITTED ("na") is excluded completely - a laptop
  without Bluetooth is not penalised for not having Bluetooth.
* A required check that was never run ("skipped") is NOT silently ignored:
  it is listed under `pending`, it lowers the COMPLETENESS score, and the
  report prints a big "INCOMPLETE INSPECTION" banner.
* Certain failures CAP the grade, whatever the arithmetic says. A machine
  with a dead drive or a tampered keyboard test can never be graded A.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.core import CHECK_INDEX, CHECKS, CheckResult, GUIDED_IDS, STATUS_LABEL

GRADE_TABLE = [
    (95.0, "A+", "Excellent"),
    (88.0, "A", "Very good"),
    (78.0, "B", "Good"),
    (68.0, "C", "Fair - visible wear or one real fault"),
    (55.0, "D", "Poor - needs repair before resale"),
    (0.0, "E", "Faulty / parts only"),
]

# a failure here caps the final grade no matter how good everything else is
CRITICAL = {
    "storage": ("C", "Storage is failing - replace the drive before selling"),
    "screen_colours": ("C", "Display defects were confirmed by eye"),
    "keyboard": ("C", "Keyboard incomplete, faulty or tampered with"),
    "os_license": ("B", "Windows licence problem"),
    "battery": ("B", "Battery needs replacement"),
    "cpu_stress": ("B", "CPU throttles or errors under load"),
    "memory_test": ("C", "Memory errors detected"),
    "events": ("B", "Hardware errors in the event log"),
    "camera_capture": ("B", "Camera does not work"),
    "microphone": ("B", "Microphone does not work"),
    "speakers": ("B", "Speaker problem"),
    "cosmetic": ("C", "Cosmetic grade is poor"),
    "final_check": ("C", "Inspection was not signed off"),
}

# a WARNING here also caps the grade - these are the things a buyer always
# notices or always asks about, so the grade must not say "like new".
WARN_CAPS = {
    "battery": "B", "cosmetic": "B", "screen_colours": "B", "storage": "B",
    "keyboard": "B", "memory_test": "B", "cpu_stress": "B", "os_license": "B",
}

GRADE_ORDER = ["A+", "A", "B", "C", "D", "E"]


def grade_from_score(score: float) -> Tuple[str, str]:
    for threshold, grade, meaning in GRADE_TABLE:
        if score >= threshold:
            return grade, meaning
    return "E", "Faulty / parts only"


def cap_grade(grade: str, cap: str) -> str:
    """Return whichever grade is worse."""
    if grade not in GRADE_ORDER or cap not in GRADE_ORDER:
        return grade
    return grade if GRADE_ORDER.index(grade) >= GRADE_ORDER.index(cap) else cap


def grade_all(results: Dict[str, CheckResult], ctx: Dict[str, Any]) -> Dict[str, Any]:
    scored: List[Tuple[str, float, float]] = []
    pending: List[str] = []
    not_fitted: List[str] = []
    errors: List[str] = []
    groups: Dict[str, Dict[str, Any]] = {}
    all_flags: List[Tuple[str, str]] = []

    for cid, name, group, weight, tech in CHECKS:
        res = results.get(cid)
        g = groups.setdefault(group, {"group": group, "items": [], "weight": 0.0,
                                      "score_sum": 0.0, "score_n": 0,
                                      "fail": 0, "warn": 0, "pass": 0, "na": 0})
        if res is None:
            pending.append(name)
            g["items"].append({"cid": cid, "name": name, "status": "missing",
                               "summary": "Not run", "score": None, "tech": tech})
            continue
        g["items"].append({"cid": cid, "name": name, "status": res.status,
                           "summary": res.summary, "score": res.score, "tech": tech})
        for flag in res.flags:
            if isinstance(flag, (list, tuple)):
                all_flags.append((str(flag[0]), str(flag[1])))
            else:
                all_flags.append((str(flag), "warn"))
        if res.status == "na":
            not_fitted.append("%s - %s" % (name, res.summary))
            g["na"] += 1
            continue
        if res.status == "skipped" or res.score is None:
            if res.required:
                pending.append("%s (%s)" % (name, res.summary or "not run"))
            if res.error:
                errors.append("%s: %s" % (name, res.error))
            continue
        scored.append((cid, float(res.weight), float(res.score)))
        g["weight"] += float(res.weight)
        g["score_sum"] += float(res.weight) * float(res.score)
        g["score_n"] += 1
        g[res.status if res.status in ("pass", "warn", "fail") else "warn"] += 1

    # ---- headline number -------------------------------------------------
    total_w = sum(w for _c, w, _s in scored)
    score = (sum(w * s for _c, w, s in scored) / total_w) if total_w else 0.0
    grade, meaning = grade_from_score(score)

    # ---- caps ------------------------------------------------------------
    caps: List[str] = []
    for cid, (cap, why) in CRITICAL.items():
        res = results.get(cid)
        if res is None:
            continue
        if res.status == "fail":
            caps.append(cap)
            all_flags.append((why, "fail"))
        elif res.status == "warn" and cid in WARN_CAPS:
            caps.append(WARN_CAPS[cid])
    # safety items always cap, whatever the arithmetic says
    cos = results.get("cosmetic")
    if cos is not None:
        for mark in cos.marks:
            if isinstance(mark, dict) and mark.get("mark") == "no" and mark.get("id") in (
                    "liquid", "battery_phys"):
                caps.append("D")
                all_flags.append(("SAFETY: %s" % mark.get("name"), "fail"))
    # "A+" must mean "nothing at all is wrong with it"
    n_warn = sum(1 for _t, lvl in all_flags if lvl == "warn")
    if n_warn:
        caps.append("A")
    if n_warn >= 3:
        caps.append("B")
    for cap in caps:
        grade = cap_grade(grade, cap)
    if caps and grade != grade_from_score(score)[0]:
        meaning += " (capped from %s: %s)" % (grade_from_score(score)[0],
                                              "critical fault" if any(
                                                  c in ("C", "D", "E") for c in caps)
                                              else "%d warning(s)" % n_warn)

    # ---- completeness ----------------------------------------------------
    required_total = len([c for c in CHECKS if c[0] in results or True])
    completeness = 100.0 * (len(scored) + len(not_fitted)) / max(1, required_total)

    # ---- integrity -------------------------------------------------------
    integrity: List[str] = []
    kb = results.get("keyboard")
    if kb is not None:
        for text, level in kb.flags:
            if isinstance(text, str) and ("REJECTED" in text or "injected" in text or
                                          "OVERRIDE" in text or "Raw Input capture was unavailable" in text):
                integrity.append(text)
    if not ctx.get("signed_off", False):
        integrity.append("The inspection was not signed off as complete.")
    if pending:
        integrity.append("%d required test(s) were never completed." % len(pending))
    if not ctx.get("admin", True):
        integrity.append("The tool did not run as Administrator, so some hardware data could not be read.")

    # ---- flags sorted ----------------------------------------------------
    order = {"fail": 0, "warn": 1, "info": 2}
    all_flags = sorted({(t, l) for t, l in all_flags}, key=lambda x: (order.get(x[1], 3), x[0]))

    # ---- group summaries -------------------------------------------------
    group_rows: List[Dict[str, Any]] = []
    for g in groups.values():
        gscore = (g["score_sum"] / g["weight"]) if g["weight"] else None
        group_rows.append({
            "group": g["group"], "score": round(gscore, 1) if gscore is not None else None,
            "pass": g["pass"], "warn": g["warn"], "fail": g["fail"], "na": g["na"],
            "items": g["items"],
        })
    group_rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))

    # ---- one-line verdict ------------------------------------------------
    if grade in ("A+", "A"):
        verdict = "Retail-ready. Sell as grade %s with a warranty." % grade
    elif grade == "B":
        verdict = "Good condition. Light refurbishment (clean, maybe a new battery) and it sells well."
    elif grade == "C":
        verdict = "Sellable at a discount, or refurbish first. Disclose the faults listed below."
    elif grade == "D":
        verdict = "Needs repair before resale. Price it as a project or part it out."
    else:
        verdict = "Not worth reselling as a working machine. Use for parts."

    return {
        "score": round(score, 1),
        "grade": grade,
        "meaning": meaning,
        "verdict": verdict,
        "caps": caps,
        "scored_items": len(scored),
        "pending": pending,
        "not_fitted": not_fitted,
        "errors": errors,
        "flags": all_flags,
        "integrity": integrity,
        "completeness": round(completeness, 1),
        "groups": group_rows,
        "fail_count": sum(1 for _c, w, s in scored if s < 40),
        "warn_count": sum(g["warn"] for g in groups.values()),
    }
