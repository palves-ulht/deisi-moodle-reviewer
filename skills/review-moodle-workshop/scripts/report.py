"""Compare peer assessments with Claude's review and render the results file.

Everything here is offline: it reads the export written by `workshop.py
export` and the per-submission reviews Claude wrote under `claude/`.

Two grades are computed per student:

- submission grade: Moodle's own, the weighted mean of the peer assessments
  that were filled in, with Claude's review added as one more assessment with
  a heavier weight.
- review grade: how close each of the student's assessments is to Claude's,
  criterion by criterion. Moodle's grading grade measures agreement with the
  other reviewers instead, and so rewards whoever sides with a generous
  majority.

For reference, the review grade is also computed with Moodle's own method
("comparison with the best assessment"), with Claude's review added to every
submission as one more assessment with a heavier weight. That method squeezes
most reviewers into a narrow band, so a poor reviewer loses little.
"""
import datetime
import html
import json
import re
import statistics
import unicodedata
from pathlib import Path

from moodle import MoodleError, assessment_grade, strip_html

CONFIDENCE = ("high", "medium", "low")


# ------------------------------------------------------------------ loading

def load_claude_reviews(folder, dims):
    """{submissionid: review} for every valid file in claude/. A malformed
    file is an error rather than a silent skip: it means the grading step
    went wrong and the report would quietly lose that submission.
    """
    reviews = {}
    for path in sorted(Path(folder).glob("*.json")):
        review = json.loads(path.read_text())
        where = f"{path.name}:"
        grades, comments = review.get("grades"), review.get("comments")
        if not isinstance(grades, list) or len(grades) != len(dims):
            raise MoodleError(f"{where} expected {len(dims)} grades")
        if not isinstance(comments, list) or len(comments) != len(dims):
            raise MoodleError(f"{where} expected {len(dims)} comments")
        for g, d in zip(grades, dims):
            if not isinstance(g, (int, float)) or not 0 <= g <= d["max"]:
                raise MoodleError(f"{where} idx {d['idx']}: {g!r} outside 0..{d['max']:g}")
        if review.get("confidence") not in CONFIDENCE:
            raise MoodleError(f"{where} confidence must be one of {CONFIDENCE}")
        review["grade"] = assessment_grade(grades, dims)
        reviews[int(review["submissionid"])] = review
    return reviews


# Most a peer assessment's review score can gain for its comments, in points
# out of 100. Claude awards it only for comments that are correct against the
# submission.
BONUS_MAX = 10


def load_bonus(folder):
    """{assessmentid: judgement} for every file in bonus/, each one Claude's
    judgement of one peer assessment's comments. Malformed files are errors,
    as in load_claude_reviews().
    """
    bonus = {}
    for path in sorted(Path(folder).glob("*.json")):
        judgement = json.loads(path.read_text())
        where = f"{path.name}:"
        value = judgement.get("bonus")
        if (not isinstance(value, int) or isinstance(value, bool)
                or not 0 <= value <= BONUS_MAX):
            raise MoodleError(f"{where} bonus must be a whole number from 0 "
                              f"to {BONUS_MAX}")
        if not str(judgement.get("reason") or "").strip():
            raise MoodleError(f"{where} reason is missing")
        bonus[int(judgement["assessmentid"])] = judgement
    return bonus


# -------------------------------------------------------------- computation

def review_quality(given, reference, dims, strictness):
    """0-100 score of one assessment against Claude's.

    The error is the weighted mean, over the criteria, of the absolute
    difference as a fraction of that criterion's maximum, weighted like the
    grade itself. strictness scales it: at 2, an assessment that is off by
    half the scale on every criterion scores 0.
    """
    total = sum(d["weight"] for d in dims)
    error = sum(d["weight"] * abs(g - r) / d["max"]
                for g, r, d in zip(given, reference, dims)) / total
    return max(0.0, 100 * (1 - strictness * error)), error


COMPARISON_LEVELS = (1, 3, 5, 7, 9)
DEFAULT_COMPARISON = 5   # Moodle's default for workshopeval_best


def best_grading_grades(assessments, dims, comparison):
    """Grading grades, 0-100, of the assessments of one submission, as
    workshopeval_best::process_assessments() computes them in Moodle 4.5.

    assessments: {key: (weight, [raw grade per criterion])}. Every grade is
    normalised to 0-100, the weighted mean assessment is found, the
    assessment closest to it is "the best", and each assessment scores 100
    minus its distance to the best. comparison is the workshop's "comparison
    of assessments" level, 1 (very strict) to 9 (very lax).
    """
    norm = {k: (w, [100 * g / d["max"] for g, d in zip(grades, dims)])
            for k, (w, grades) in assessments.items()}
    total = sum(w for w, _ in norm.values())
    if not norm or total == 0:
        return {}
    average = [sum(w * g[i] for w, g in norm.values()) / total
               for i in range(len(dims))]

    # Weighted incremental variance per criterion, as in weighted_variance().
    variance = []
    for i in range(len(dims)):
        n = s = sumweight = mean = 0
        for w, g in norm.values():
            if w == 0:
                continue
            if n == 0:
                n, mean, s, sumweight = 1, g[i], 0, w
            else:
                n += 1
                temp = w + sumweight
                q = g[i] - mean
                r = q * w / temp
                s += sumweight * q * r
                mean += r
                sumweight = temp
        variance.append(s / sumweight if sumweight > 0 and n > 1 else 0)

    def distance(a, ref):
        dist = n = 0
        for x, y, var, d in zip(a, ref, variance, dims):
            n += d["weight"]
            if x != y:
                dist += (abs(x - y) * (x - y) ** 2
                         / (comparison * max(var, 0.01)) * d["weight"])
        return round(dist / n, 4) if n else None

    first = {k: distance(g, average) for k, (_, g) in norm.items()}
    best = [k for k, v in first.items() if v == min(first.values())]
    distances = {}
    for b in best:
        for k, (_, g) in norm.items():
            dist = distance(g, norm[b][1])
            if dist is not None and (k not in distances or dist < distances[k]):
                distances[k] = dist
    return {k: min(100.0, max(0.0, 100 - dist)) for k, dist in distances.items()}


def _peer_batch(sub):
    return {a["id"]: (float(a["weight"]), a["dimensiongrades"])
            for a in sub["assessments"]
            if a["grade"] is not None
            and None not in (a.get("dimensiongrades") or [None])}


def infer_comparison(data):
    """The workshop's comparison level, found by replaying Moodle's
    calculation at every level and keeping the one that reproduces the
    grading grades Moodle stored. The web services do not expose it.
    Returns (level, matched, total); level is None when nothing is stored.
    """
    dims = data["dimensions"]
    stored = {a["id"]: float(a["gradinggrade"])
              for s in data["submissions"] for a in s["assessments"]
              if a.get("gradinggrade") is not None}
    if not stored:
        return None, 0, 0
    best = None
    for level in COMPARISON_LEVELS:
        matched = 0
        for sub in data["submissions"]:
            for k, g in best_grading_grades(_peer_batch(sub), dims, level).items():
                if k in stored and abs(g - stored[k]) < 0.01:
                    matched += 1
        if best is None or matched > best[1]:
            best = (level, matched, len(stored))
    return best


def analyse(data, claude, strictness, claude_weight, comparison, bonus=None):
    bonus = bonus or {}
    dims = data["dimensions"]
    me = data["me"]
    students = {}   # userid -> row of the final table

    def student(user, userid):
        user = user or {}
        return students.setdefault(userid, {
            "userid": userid,
            "name": user.get("fullname") or f"user {userid}",
            "number": user.get("idnumber") or "",
            "submission": None, "combined_submission": None, "reviews": []})

    submissions = []
    for sub in data["submissions"]:
        author = student(sub.get("author"), sub["authorid"])
        author["submission"] = sub
        ref = claude.get(sub["id"])
        usable = ref is not None and ref["confidence"] != "low"
        batch = _peer_batch(sub)
        if usable:
            batch["claude"] = (float(claude_weight), ref["grades"])
        combined = best_grading_grades(batch, dims, comparison)
        # Moodle's submission grade is the weighted mean of the assessment
        # grades; Claude's joins it with its own weight.
        weighted = [(float(a["weight"]), a["grade"]) for a in sub["assessments"]
                    if a["id"] in batch]
        if usable:
            weighted.append((float(claude_weight), ref["grade"]))
        total = sum(w for w, _ in weighted)
        author["combined_submission"] = (
            sum(w * g for w, g in weighted) / total if total else None)
        rows = []
        for a in sub["assessments"]:
            filled = (a["grade"] is not None
                      and None not in (a.get("dimensiongrades") or [None]))
            row = {"assessment": a, "filled": filled, "quality": None,
                   "error": None, "gap": None, "teacher": a["reviewerid"] == me,
                   "combined": combined.get(a["id"]), "bonus": None,
                   "score": None}
            if filled and ref is not None:
                row["gap"] = a["grade"] - ref["grade"]
                row["quality"], row["error"] = review_quality(
                    a["dimensiongrades"], ref["grades"], dims, strictness)
            if filled and usable:
                # The score is the agreement with Claude plus the bonus for
                # comments, capped at 100.
                row["bonus"] = bonus.get(a["id"])
                extra = row["bonus"]["bonus"] if row["bonus"] else 0
                row["score"] = min(100.0, row["quality"] + extra)
            rows.append(row)
            if row["teacher"]:
                continue
            # An allocated assessment never filled in counts as 0: it was
            # part of the student's work in this activity.
            reviewer = student(a.get("reviewer"), a["reviewerid"])
            if not filled:
                score, combined_score = 0.0, 0.0
            else:
                score = row["score"]
                combined_score = row["combined"]
            reviewer["reviews"].append({"row": row, "sub": sub, "score": score,
                                        "combined": combined_score})
        submissions.append({"sub": sub, "author": author, "claude": ref,
                            "rows": rows})

    for s in students.values():
        scored = [r["score"] for r in s["reviews"] if r["score"] is not None]
        s["review_grade"] = statistics.mean(scored) if scored else None
        combined = [r["combined"] for r in s["reviews"] if r["combined"] is not None]
        s["combined_grade"] = statistics.mean(combined) if combined else None
        gaps = [abs(r["row"]["gap"]) for r in s["reviews"]
                if r["row"]["gap"] is not None]
        s["mean_gap"] = statistics.mean(gaps) if gaps else None
    return submissions, students


def moodle_final(data):
    """{userid: (submission grade, grading grade)} as Moodle currently has
    them, both already scaled to the workshop's maxima (e.g. 80 and 20)."""
    out = {}
    for row in data["report"]["grades"]:
        sub = row.get("submissiongradeover")
        if sub is None:
            sub = row.get("submissiongrade")
        out[row["userid"]] = (sub, row.get("gradinggrade"))
    return out


# ---------------------------------------------------------------- rendering

def slug(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _num(value, digits=0):
    return "-" if value is None else f"{value:.{digits}f}"


def render(data, claude, strictness, weights, gap, claude_weight, comparison=None,
           bonus=None):
    level, matched, stored = infer_comparison(data)
    if comparison is not None:
        comparison_note = f"comparison level {comparison:g}, as given"
    elif level is not None:
        comparison = level
        comparison_note = (f"comparison level {level}, inferred: it reproduces "
                           f"{matched} of the {stored} grading grades Moodle stored")
    else:
        comparison = DEFAULT_COMPARISON
        comparison_note = (f"comparison level {comparison}, Moodle's default: "
                           f"Moodle has no grading grades yet to infer it from")
    submissions, students = analyse(data, claude, strictness, claude_weight,
                                    comparison, bonus)
    dims = data["dimensions"]
    settings = data["settings"]
    current = moodle_final(data)
    wsub, wrev = weights
    maxsub, maxrev = settings["grade"], settings["gradinggrade"]
    today = datetime.date.today().isoformat()
    graded = sum(1 for s in submissions if s["claude"])
    low = [s for s in submissions if s["claude"] and s["claude"]["confidence"] == "low"]

    out = [f"# {data['name']}", "",
           f"{data['course']} · workshop {data['workshopid']} · {today}", "",
           "Peer assessments checked against an independent review by Claude, "
           "who graded every submission against the rubric without seeing the peer "
           "assessments.", "",
           f"- {len(submissions)} submissions, {graded} reviewed by Claude"
           + (f", {len(low)} with low confidence (excluded from review grades)" if low else ""),
           f"- **Bold** peer grade: {gap:g}+ points away from Claude's.",
           f"- Submission grade (Moodle + Claude): the weighted mean of the "
           f"peer assessments filled in, as Moodle computes it, with Claude's "
           f"review added weighing {claude_weight:g}x a student's.",
           f"- Review grade (Claude): 100 minus {strictness:g} x the weighted "
           f"mean per-criterion error against Claude, floored at 0, plus a "
           f"bonus of up to {BONUS_MAX} per assessment for comments Claude "
           f"found specific, useful and correct against the submission, capped "
           f"at 100. When "
           f"Claude's review of every submission they assessed is low "
           f"confidence, the Moodle + Claude grade is used instead (`*`).",
           f"- Review grade (Moodle + Claude), for reference: Moodle's own "
           f"method, comparison "
           f"with the best assessment, recomputed with Claude's review added to "
           f"every submission as one more assessment weighing {claude_weight:g}x "
           f"a student's ({comparison_note}). Claude's low-confidence reviews "
           f"are left out.",
           "- In both, an allocated assessment never filled in counts as 0. "
           "Moodle instead ignores it.",
           f"- Current final: Moodle as it stands, {maxsub:g}% submission + "
           f"{maxrev:g}% review (Moodle's own grading grade). `-`: the "
           f"student is not in Moodle's grades report (e.g. suspended enrolment).",
           f"- Proposed final: {wsub:g}% submission grade (Moodle + Claude) + "
           f"{wrev:g}% review grade (Claude). A student who left any "
           f"of their allocated assessments undone gets 0.", ""]

    # Submissions
    out += ["## Submissions", "",
            "| Student | Number | Peer assessments | Peer mean | Claude | Diff "
            "| Moodle + Claude |",
            "|---|---|---|---:|---:|---:|---:|"]
    for s in sorted(submissions, key=lambda s: s["author"]["name"]):
        parts = []
        for row in s["rows"]:
            a = row["assessment"]
            name = (a.get("reviewer") or {}).get("fullname") or f"user {a['reviewerid']}"
            if row["teacher"]:
                name += " (teacher)"
            if not row["filled"]:
                parts.append(f"{name}: not done")
                continue
            grade = f"{a['grade']:.0f}"
            if row["gap"] is not None and abs(row["gap"]) >= gap:
                grade = f"**{grade}**"
            parts.append(f"{name}: {grade}")
        peer = s["sub"].get("grade")
        ref = s["claude"]
        claude_cell = "-" if ref is None else f"{ref['grade']:.0f}"
        if ref is not None and ref["confidence"] != "high":
            claude_cell += f" ({ref['confidence']})"
        diff = None if ref is None or peer is None else peer - ref["grade"]
        out.append(f"| {_cell(s['author']['name'])} | {_cell(s['author']['number'])} "
                   f"| {_cell(' · '.join(parts) or 'none')} | {_num(peer)} "
                   f"| {claude_cell} | {_num(diff) if diff is None else f'{diff:+.0f}'} "
                   f"| {_num(s['author']['combined_submission'])} |")

    # Reviewers
    out += ["", "## Reviewers", "",
            "Mean gap: average distance, in points out of 100, between the "
            "student's assessments and Claude's. Bonus: the bonus for comments "
            "on each of their assessments, already included in the review "
            "grade (Claude).", "",
            "| Student | Number | Done | Mean gap | Review grade (Moodle) "
            "| Review grade (Moodle + Claude) | Bonus | Review grade (Claude) |",
            "|---|---|---:|---:|---:|---:|---|---:|"]
    reviewers = [s for s in students.values() if s["reviews"]]
    for s in sorted(reviewers, key=lambda s: (s["review_grade"] is None,
                                              s["review_grade"] or 0)):
        done = sum(1 for r in s["reviews"] if r["row"]["filled"])
        moodle = current.get(s["userid"], (None, None))[1]
        moodle_pct = None if moodle is None or not maxrev else 100 * moodle / maxrev
        given = [r["row"]["bonus"]["bonus"] for r in s["reviews"]
                 if r["row"]["bonus"]]
        bonus_cell = " · ".join(f"+{b}" for b in given) if any(given) else "-"
        out.append(f"| {_cell(s['name'])} | {_cell(s['number'])} "
                   f"| {done}/{len(s['reviews'])} | {_num(s['mean_gap'])} "
                   f"| {_num(moodle_pct)} | {_num(s['combined_grade'])} "
                   f"| {bonus_cell} | {_num(s['review_grade'])} |")

    # Final grades
    out += ["", "## Final grades", "",
            f"| Student | Number | Submission (Moodle) | Submission (Moodle + Claude) "
            f"| Review (Moodle) | Review (Claude) "
            f"| Current final ({maxsub:g}/{maxrev:g}) | Proposed final ({wsub:g}/{wrev:g}) "
            f"| Change |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in sorted(students.values(), key=lambda s: s["name"]):
        if s["userid"] == data["me"]:
            continue
        sub_pct = (s["submission"] or {}).get("grade")
        listed = s["userid"] in current
        msub, mrev = current.get(s["userid"], (None, None))
        # Moodle's grades report leaves out users it does not list as active
        # participants, e.g. a suspended enrolment: there is no current final.
        now = (msub or 0) + (mrev or 0) if listed else None
        mrev_pct = None if mrev is None or not maxrev else 100 * mrev / maxrev
        review = s["review_grade"]
        review_cell = _num(review)
        if review is None and s["reviews"]:
            # Claude could not read any submission they assessed reliably.
            review = s["combined_grade"]
            review_cell = f"{_num(review)}*"
        sub_combined = s["combined_submission"]
        proposed = (wsub * (sub_combined or 0) + wrev * (review or 0)) / 100
        proposed_cell = f"{proposed:.0f}"
        missed = sum(1 for r in s["reviews"] if not r["row"]["filled"])
        if missed:
            # Reviewing every allocated submission is part of the activity:
            # leaving any of them undone forfeits the whole grade.
            proposed = 0.0
            proposed_cell = f"0 (missed {missed}/{len(s['reviews'])})"
        out.append(f"| {_cell(s['name'])} | {_cell(s['number'])} | {_num(sub_pct)} "
                   f"| {_num(sub_combined)} "
                   f"| {_num(mrev_pct)} | {review_cell} | {_num(now)} "
                   f"| {proposed_cell} | {'-' if now is None else f'{proposed - now:+.0f}'} |")

    # Details
    out += ["", "## Details per submission", "",
            "Grades per criterion. Claude's comment explains each deduction.", ""]
    for s in sorted(submissions, key=lambda s: s["author"]["name"]):
        sub, ref = s["sub"], s["claude"]
        out += [f"### {s['author']['name']} ({s['author']['number']}) - submission {sub['id']}", ""]
        files = ", ".join(f["filename"] for f in sub.get("attachmentfiles") or []) or "no attachment"
        out.append(f"File: {files}")
        if ref is None:
            out += ["", "Not reviewed by Claude.", ""]
            continue
        out += [f"Claude: {ref['grade']:.0f} ({ref['confidence']} confidence). "
                f"{ref.get('summary', '')}".rstrip(), ""]
        filled = [r for r in s["rows"] if r["filled"]]
        heads = [(r["assessment"].get("reviewer") or {}).get("fullname")
                 or f"user {r['assessment']['reviewerid']}" for r in filled]
        out.append("| Criterion | Max | Claude | " + " | ".join(map(_cell, heads))
                   + " | Claude's reasoning |")
        out.append("|---|---:|---:|" + "---:|" * len(heads) + "---|")
        for d in dims:
            i = d["idx"]
            cells = []
            for r in filled:
                g = r["assessment"]["dimensiongrades"][i]
                cell = f"{g:g}"
                if abs(g - ref["grades"][i]) > d["max"] / 2:
                    cell = f"**{cell}**"
                cells.append(cell)
            out.append(f"| {i + 1}. {_cell(d['description'][:50])}... | {d['max']:g} "
                       f"| {ref['grades'][i]:g} | " + " | ".join(cells)
                       + f" | {_cell(ref['comments'][i])} |")
        totals = " | ".join(f"{r['assessment']['grade']:.0f}" for r in filled)
        out += [f"| **Total** | 100 | **{ref['grade']:.0f}** | {totals} | |", ""]
        rewarded = [(name, r["bonus"]) for name, r in zip(heads, filled)
                    if r["bonus"] and r["bonus"]["bonus"]]
        if rewarded:
            out += ["Bonus for comments:", ""]
            out += [f"- {name}: +{b['bonus']}. {b['reason']}" for name, b in rewarded]
            out.append("")

    return "\n".join(out) + "\n"


# ------------------------------------------------------------- publishing

# How the feedback names Claude's review. Students read this.
REFERENCE = "avaliação de referência"


def _pts(pct, maximum):
    """A 0-100 grade as whole points out of maximum: the override
    parameters are PARAM_ALPHANUMEXT, so Moodle rejects decimals."""
    return round(pct * maximum / 100)


def _criteria_list(items):
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def _reviewer_feedback(row, ref, dims, missed):
    a = row["assessment"]
    if not row["filled"]:
        return ("<p>Não fizeste esta avaliação. Todas as avaliações atribuídas "
                "são obrigatórias: ficas com 0 nesta avaliação e 0 na nota "
                "final da atividade.</p>")
    out = [f"<p>Comparámos a tua avaliação desta submissão com a {REFERENCE}.</p>",
           f"<p>Nota que deste: {a['grade']:.0f}/100. Nota da {REFERENCE}: "
           f"{ref['grade']:.0f}/100. Classificação da tua avaliação: "
           f"{row['score']:.0f}/100.</p>"]
    bonus = row["bonus"]
    if bonus and bonus["bonus"]:
        out.append(f"<p>Esta classificação inclui um bónus de {bonus['bonus']} "
                   f"pontos pela qualidade dos teus comentários (sem o bónus "
                   f"seria {row['quality']:.0f}/100): "
                   f"{html.escape(bonus['reason'], quote=False)}</p>")
    off = []
    for d in dims:
        i = d["idx"]
        given, expected = a["dimensiongrades"][i], ref["grades"][i]
        if given != expected:
            off.append(f"<b>Critério {i + 1}</b> (máx. {d['max']:g}): deste "
                       f"{given:g}, a {REFERENCE} dá {expected:g}. "
                       f"{html.escape(ref['comments'][i], quote=False)}")
    if off:
        out += [f"<p>Critérios em que te afastaste da {REFERENCE}:</p>",
                _criteria_list(off)]
    else:
        out.append(f"<p>As tuas notas coincidem com a {REFERENCE} em todos "
                   f"os critérios.</p>")
    if missed:
        out.append(f"<p><b>Nota final 0:</b> não fizeste {missed} das "
                   f"avaliações que te foram atribuídas, e todas são "
                   f"obrigatórias.</p>")
    return "".join(out)


def _author_feedback(ref, dims, grade, claude_weight, missed, allocated):
    out = []
    if missed:
        out.append(f"<p><b>Nota final 0:</b> não fizeste {missed} das "
                   f"{allocated} avaliações que te foram atribuídas, e todas "
                   f"são obrigatórias. Sem essa regra, a tua submissão teria "
                   f"{grade:.0f}/100.</p>")
    else:
        out.append(f"<p>Nota da submissão: {grade:.0f}/100, a média ponderada "
                   f"das avaliações dos colegas e da {REFERENCE}, que conta "
                   f"{claude_weight:g} vezes.</p>")
    out.append(f"<p>Comentários da {REFERENCE} ({ref['grade']:.0f}/100):</p>")
    out.append(_criteria_list(
        f"<b>Critério {d['idx'] + 1}</b> ({ref['grades'][d['idx']]:g}/{d['max']:g}): "
        f"{html.escape(ref['comments'][d['idx']], quote=False)}" for d in dims))
    return "".join(out)


def publish_plan(data, claude, strictness, weights, claude_weight, comparison=None,
                 bonus=None):
    """Every override and feedback text to write to Moodle, as one reviewable
    document. Grades are in points out of the maxima the workshop must have
    when it is published, (submission, assessment) = weights.
    """
    if comparison is None:
        comparison = infer_comparison(data)[0] or DEFAULT_COMPARISON
    submissions, students = analyse(data, claude, strictness, claude_weight,
                                    comparison, bonus)
    dims = data["dimensions"]
    maxsub, maxrev = weights

    def missed(userid):
        s = students.get(userid)
        return (sum(1 for r in s["reviews"] if not r["row"]["filled"]),
                len(s["reviews"])) if s else (0, 0)

    plan = {"workshopid": data["workshopid"], "cmid": data["cmid"],
            "name": data["name"], "maxima": {"submission": maxsub,
                                            "assessment": maxrev},
            "submissions": [], "assessments": [], "skipped": []}
    for s in submissions:
        sub, ref, author = s["sub"], s["claude"], s["author"]
        if ref is None or ref["confidence"] == "low":
            plan["skipped"].append({"submissionid": sub["id"],
                                    "author": author["name"],
                                    "reason": "no usable Claude review"})
            continue
        n_missed, allocated = missed(author["userid"])
        grade = author["combined_submission"]
        plan["submissions"].append({
            "submissionid": sub["id"], "author": author["name"],
            "authorid": author["userid"], "number": author["number"],
            "grade": grade,
            "gradeover": 0 if n_missed else _pts(grade, maxsub),
            "feedback": _author_feedback(ref, dims, grade, claude_weight,
                                         n_missed, allocated)})
        for row in s["rows"]:
            if row["teacher"]:
                continue
            a = row["assessment"]
            reviewer = students[a["reviewerid"]]
            n_missed, _ = missed(a["reviewerid"])
            score = row["score"] if row["filled"] else 0.0
            plan["assessments"].append({
                "assessmentid": a["id"], "submissionid": sub["id"],
                "reviewer": reviewer["name"], "reviewerid": reviewer["userid"],
                "number": reviewer["number"], "weight": int(a["weight"]),
                "grade": a["grade"], "score": score,
                "bonus": row["bonus"]["bonus"] if row["bonus"] else 0,
                "gradinggradeover": 0 if n_missed else _pts(score, maxrev),
                "feedback": _reviewer_feedback(row, ref, dims, n_missed)})
    return plan
