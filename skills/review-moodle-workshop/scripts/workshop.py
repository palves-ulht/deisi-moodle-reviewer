#!/usr/bin/env python3
"""Review a peer-assessed Moodle workshop with Claude, from the command line.

    workshop.py login                  once, in your own terminal
    workshop.py list
    workshop.py export 61
    workshop.py prepare exports/ws61/workshop.json
    workshop.py confirm exports/ws61/workshop.json 1438
    workshop.py report exports/ws61/workshop.json
    workshop.py plan exports/ws61/workshop.json
    workshop.py publish exports/ws61/publish.json --dry-run
    workshop.py publish exports/ws61/publish.json --only a22508126
    workshop.py publish exports/ws61/publish.json
    workshop.py verify exports/ws61/publish.json
    workshop.py csv exports/ws61/publish.json

Only `publish` without --dry-run writes to Moodle. Everything else reads from
Moodle or works offline on the files under exports/.
"""
import argparse
import csv
import getpass
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

# The sibling modules, found however the script is started (python -I leaves
# the script's own folder off the path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import report as results  # noqa: E402
from moodle import (CONFIG, DEFAULT_URL, PHASES, PHASE_EVALUATION, Moodle,  # noqa: E402
                    MoodleError, current_grades, fetch_token, load_config,
                    rubric, save_config, strip_html)


def cmd_login(args):
    """Get a personal web service token and store it in CONFIG. Run it in
    your own terminal: the password is typed there, sent to Moodle once and
    never stored or shown to Claude."""
    base = (args.url or load_config().get("url") or DEFAULT_URL).rstrip("/")
    if args.token:
        token = getpass.getpass("Moodle token (hidden): ").strip()
    else:
        print(f"Moodle: {base}")
        username = input("Username (the one you sign in to Moodle with): ").strip()
        password = getpass.getpass("Password (hidden, not stored): ")
        token = fetch_token(base, username, password)
    info = Moodle(base, token).ws("core_webservice_get_site_info")
    save_config({**load_config(), "url": base, "token": token})
    print(f"Signed in as {info['fullname']} on {info['sitename']}. "
          f"Token saved to {CONFIG}, readable only by you.")
    print(f"To revoke it later: {base}/user/managetoken.php")


def _workshops(m):
    """Every workshop in every course the token's user is enrolled in."""
    courses = m.ws("core_enrol_get_users_courses", userid=m.userid)
    names = {c["id"]: c["shortname"] for c in courses}
    found = m.ws("mod_workshop_get_workshops_by_courses",
                 courseids=[c["id"] for c in courses])["workshops"]
    return found, names


def _find(m, workshopid):
    found, names = _workshops(m)
    for w in found:
        if w["id"] == workshopid:
            return w, names.get(w["course"], str(w["course"]))
    raise MoodleError(f"workshop {workshopid} not found among your courses")


def cmd_list(m, args):
    found, names = _workshops(m)
    print(f"{'wid':>5} {'cmid':>7} {'phase':<11} {'subs':>5}  name")
    for w in sorted(found, key=lambda x: x["id"]):
        count = m.ws("mod_workshop_get_submissions",
                     workshopid=w["id"], perpage=1)["totalcount"]
        print(f"{w['id']:>5} {w['coursemodule']:>7} "
              f"{PHASES.get(w['phase'], w['phase']):<11} {count:>5}  "
              f"{w['name'][:44]}")
        print(f"{'':>31}{names.get(w['course'], '')[:70]}")


def cmd_export(m, args):
    workshop, course = _find(m, args.workshopid)
    if workshop["strategy"] != "accumulative":
        raise MoodleError(f"grading strategy is {workshop['strategy']!r}; only "
                          f"'accumulative' is supported")
    out = Path(args.out or f"exports/ws{args.workshopid}")
    (out / "files").mkdir(parents=True, exist_ok=True)

    access = m.ws("mod_workshop_get_workshop_access_information",
                  workshopid=args.workshopid)
    if not access.get("canviewallsubmissions"):
        print("warning: your account cannot see every submission here",
              file=sys.stderr)

    submissions = m.ws("mod_workshop_get_submissions",
                       workshopid=args.workshopid, perpage=1000)["submissions"]
    people = set()
    dims = None
    for sub in submissions:
        sub["assessments"] = m.ws("mod_workshop_get_submission_assessments",
                                  submissionid=sub["id"])["assessments"]
        people.add(sub["authorid"])
        people.update(a["reviewerid"] for a in sub["assessments"])
        for a in sub["assessments"]:
            # The summary above only has the total; the per-dimension grades
            # need one form definition call per assessment.
            form = m.ws("mod_workshop_get_assessment_form_definition",
                        assessmentid=a["id"], mode="assessment")
            dims = dims or rubric(form)
            a["dimensiongrades"], a["dimensioncomments"] = current_grades(form)
        if not args.no_files:
            for f in sub.get("attachmentfiles") or []:
                dest = out / "files" / f"{sub['id']}_{f['filename']}"
                m.download(f["fileurl"], dest)
                f["localpath"] = str(dest.relative_to(out))

    if people:
        users = {u["id"]: u for u in m.ws("core_user_get_users_by_field",
                                          field="id", values=sorted(people))}
        for sub in submissions:
            sub["author"] = users.get(sub["authorid"])
            for a in sub["assessments"]:
                a["reviewer"] = users.get(a["reviewerid"])

    if dims is None:
        raise MoodleError("no assessment exists in this workshop, so there is "
                          "no rubric to read and nothing to check")
    report = m.ws("mod_workshop_get_grades_report",
                  workshopid=args.workshopid, perpage=1000)["report"]
    payload = {"workshopid": args.workshopid, "cmid": workshop["coursemodule"],
               "name": workshop["name"], "course": course,
               "phase": workshop["phase"], "me": m.userid,
               "settings": {
                   "grade": workshop["grade"],
                   "gradinggrade": workshop["gradinggrade"],
                   "strategy": workshop["strategy"],
                   "instructauthors": strip_html(workshop["instructauthors"]),
                   "instructreviewers": strip_html(workshop["instructreviewers"]),
               },
               "dimensions": dims,
               "submissions": submissions,
               "report": report}
    (out / "workshop.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False))
    print(f"{len(submissions)} submissions, "
          f"{sum(len(s['assessments']) for s in submissions)} peer assessments "
          f"-> {out}/workshop.json")
    _readiness(payload)


def _readiness(payload):
    """Warns when the peer assessments or Moodle's grades are not final yet."""
    phase = payload["phase"]
    print(f"phase: {PHASES.get(phase, phase)}")
    if phase < PHASE_EVALUATION:
        print("warning: the assessment phase is still open, so peer "
              "assessments may be missing or change", file=sys.stderr)
    done = [a for s in payload["submissions"] for a in s["assessments"]
            if a["grade"] is not None]
    # Moodle leaves the submission grade out entirely until it is calculated.
    ungraded = [s for s in payload["submissions"]
                if s.get("grade") is None and _graded(s)]
    unweighed = [a for a in done if a["gradinggrade"] is None]
    if ungraded or unweighed:
        print(f"warning: Moodle has not calculated the grades yet "
              f"({len(ungraded)} submission grade(s) and {len(unweighed)} "
              f"assessment grade(s) missing). Run 'Calculate submission "
              f"grades' and 'Calculate assessment grades' in the workshop, "
              f"then export again", file=sys.stderr)


def _graded(sub):
    return [a for a in sub["assessments"] if a["grade"] is not None
            and None not in (a.get("dimensiongrades") or [None])]


# ------------------------------------------------------------------- prepare

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif",
                  ".tif", ".tiff", ".bmp"}
# Limits on what a submitted zip may expand to, against zip bombs.
ZIP_MAX_BYTES = 200 * 1024 * 1024
ZIP_MAX_FILES = 5000


def _stage(src, dest_dir, mimetype):
    """Put one submitted file under dest_dir in a form Claude can read, and
    return the path to give the grader.

    - Images go through macOS `sips` into a JPEG of at most 2000 px: phone
      photos are often 4000 px, HEIC, or saved without an extension. Without
      sips, or if it fails, the file is copied as it is.
    - Zip files are extracted into a folder of their own name.
    - Anything else (code, PDF, text) is copied unchanged.
    """
    name = src.name.split("_", 1)[1] if "_" in src.name else src.name
    suffix = Path(name).suffix.lower()
    if mimetype.startswith("image/") or suffix in IMAGE_SUFFIXES:
        dest = dest_dir / (Path(name).stem + ".jpg")
        if shutil.which("sips"):
            done = subprocess.run(["sips", "-s", "format", "jpeg", "-Z", "2000",
                                   str(src), "--out", str(dest)], capture_output=True)
            if done.returncode == 0 and dest.exists():
                return dest
    if suffix == ".zip" or mimetype in ("application/zip", "application/x-zip-compressed"):
        return _unzip(src, dest_dir / Path(name).stem)
    dest = dest_dir / name
    shutil.copyfile(src, dest)
    return dest


def _unzip(src, dest):
    """Extract a submitted zip without letting it write outside dest or
    expand without bound. macOS metadata folders are skipped."""
    with zipfile.ZipFile(src) as z:
        members = [i for i in z.infolist() if not i.is_dir()
                   and not i.filename.startswith("__MACOSX/")
                   and PurePosixPath(i.filename).name != ".DS_Store"]
        if len(members) > ZIP_MAX_FILES or sum(i.file_size for i in members) > ZIP_MAX_BYTES:
            raise MoodleError(f"{src.name} expands beyond the limits "
                              f"({ZIP_MAX_FILES} files, {ZIP_MAX_BYTES >> 20} MB)")
        for info in members:
            parts = PurePosixPath(info.filename).parts
            if info.filename.startswith("/") or ".." in parts:
                raise MoodleError(f"{src.name}: unsafe path {info.filename!r}")
            target = dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as fin, open(target, "wb") as fout:
                shutil.copyfileobj(fin, fout)
    return dest


def _submission_files(sub, base, review):
    """The files of one submission, staged under review/files/<id>/, plus
    its online text if any. Idempotent: an existing folder is reused."""
    dest_dir = review / "files" / str(sub["id"])
    if dest_dir.exists() and any(dest_dir.iterdir()):
        return sorted(str(p) for p in dest_dir.iterdir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for f in sub.get("attachmentfiles") or []:
        src = base / f.get("localpath", "")
        if not f.get("localpath") or not src.exists():
            raise MoodleError(f"submission {sub['id']}: {f['filename']} was not "
                              f"downloaded; re-run `export` without --no-files")
        staged.append(str(_stage(src, dest_dir, f.get("mimetype", ""))))
    if strip_html(sub.get("content")):
        text = dest_dir / "texto-online.html"
        text.write_text(sub["content"])
        staged.append(str(text))
    return sorted(staged)


def cmd_prepare(args):
    """Lay out what the grading steps need, under review/ next to the export:
    the rubric as markdown, the submissions in a readable form, the queue of
    submissions Claude has not reviewed yet, and the queue of peer
    assessments whose comments Claude has not judged yet. Idempotent: re-run
    it to see what is still pending.
    """
    path = Path(args.export)
    data = json.loads(path.read_text())
    base = path.parent
    review = base / "review"
    (review / "files").mkdir(parents=True, exist_ok=True)
    for folder in ("reference", "claude", "bonus"):
        (base / folder).mkdir(exist_ok=True)

    lines = [f"# {data['name']}", "", "## Instructions to authors", "",
             data["settings"]["instructauthors"], "",
             "## Instructions to reviewers", "",
             data["settings"]["instructreviewers"], "",
             "## Rubric", "",
             "Accumulative: each criterion is graded from 0 to its maximum. "
             "The final grade normalises every criterion to its maximum, then "
             "takes the weighted mean.", ""]
    for d in data["dimensions"]:
        lines += [f"### Criterion {d['idx'] + 1} (index {d['idx']}) - "
                  f"max {d['max']:g}, weight {d['weight']:g}", "", d["description"], ""]
    (review / "rubric.md").write_text("\n".join(lines))

    queue, empty, files_of = [], [], {}
    for sub in data["submissions"]:
        files = _submission_files(sub, base, review)
        if not files:
            empty.append(sub["id"])
            continue
        files_of[sub["id"]] = files
        if not (base / "claude" / f"{sub['id']}.json").exists():
            queue.append({"submissionid": sub["id"], "files": files,
                          "output": str(base / "claude" / f"{sub['id']}.json")})

    reference = sorted(str(p) for p in (base / "reference").iterdir()
                       if not p.name.startswith("."))
    notes = base / "notes.md"
    (review / "queue.json").write_text(json.dumps({
        "workshop": data["name"], "rubric": str(review / "rubric.md"),
        "reference": reference,
        "notes": str(notes) if notes.exists() else None,
        "dimensions": len(data["dimensions"]),
        "pending": queue}, indent=1, ensure_ascii=False))
    bonus_queue = _bonus_queue(data, base, files_of)
    (review / "bonus-queue.json").write_text(json.dumps({
        "workshop": data["name"], "rubric": str(review / "rubric.md"),
        "notes": str(notes) if notes.exists() else None,
        "max": results.BONUS_MAX,
        "pending": bonus_queue}, indent=1, ensure_ascii=False))

    total = len(data["submissions"])
    print(f"{total} submissions: {total - len(empty) - len(queue)} reviewed by "
          f"Claude, {len(queue)} pending, {len(empty)} empty")
    if empty:
        print(f"  empty (no file, no text): {empty}")
    print(f"  comment bonus: {len(bonus_queue)} peer assessment(s) with comments "
          f"to judge -> {review / 'bonus-queue.json'}")
    print(f"  reference material: {len(reference)} file(s) in {base / 'reference'}")
    print(f"  interpretation notes: {notes if notes.exists() else 'none (' + str(notes) + ')'}")
    print(f"-> {review / 'queue.json'}")


def _bonus_queue(data, base, files_of):
    """The peer assessments whose comments Claude has yet to judge for the
    comment bonus: filled in, with at least one comment, on a submission that
    Claude reviewed with enough confidence to check the comments against.
    Assessments without comments get no bonus and are not queued.
    """
    queue = []
    for sub in data["submissions"]:
        ref = base / "claude" / f"{sub['id']}.json"
        if sub["id"] not in files_of or not ref.exists():
            continue
        if json.loads(ref.read_text()).get("confidence") == "low":
            continue
        for a in sub["assessments"]:
            if a["reviewerid"] == data["me"] or not _graded({"assessments": [a]}):
                continue
            comments = [{"criterion": i + 1, "comment": strip_html(c)}
                        for i, c in enumerate(a.get("dimensioncomments") or [])
                        if strip_html(c)]
            overall = strip_html(a.get("feedbackauthor"))
            out = base / "bonus" / f"{a['id']}.json"
            if (comments or overall) and not out.exists():
                queue.append({"assessmentid": a["id"], "submissionid": sub["id"],
                              "files": files_of[sub["id"]], "claude": str(ref),
                              "comments": comments, "overall": overall,
                              "output": str(out)})
    return queue


def cmd_confirm(args):
    """Mark Claude's low-confidence review of a submission as checked by the
    teacher, so it counts like any other. The grades are left as they are."""
    path = Path(args.export).parent / "claude" / f"{args.submissionid}.json"
    if not path.exists():
        raise MoodleError(f"{path} does not exist")
    review = json.loads(path.read_text())
    if review.get("confidence") != "low":
        print(f"{path}: confidence is already {review.get('confidence')!r}")
        return
    review["confidence"] = "medium"
    review["confirmed_by_teacher"] = True
    path.write_text(json.dumps(review, indent=1, ensure_ascii=False))
    print(f"{path}: confirmed by the teacher, now counts as medium confidence")


# -------------------------------------------------------------------- report

def _bonus(base):
    folder = base / "bonus"
    return results.load_bonus(folder) if folder.exists() else {}


def _load(args):
    path = Path(args.export)
    data = json.loads(path.read_text())
    claude = results.load_claude_reviews(path.parent / "claude", data["dimensions"])
    return path, data, claude


def cmd_report(args):
    path, data, claude = _load(args)
    text = results.render(data, claude, args.strictness, _weights(args.weights),
                          args.gap, args.claude_weight, args.comparison,
                          _bonus(path.parent))
    out = Path(args.out or path.parent / f"results-workshop-{results.slug(data['name'])}.md")
    out.write_text(text)
    pending = len(data["submissions"]) - len(claude)
    print(f"{len(claude)} Claude reviews, {pending} submissions without one -> {out}")


def _weights(text):
    try:
        wsub, wrev = (float(x) for x in text.split("/"))
    except ValueError:
        raise MoodleError(f"--weights must look like 60/40, not {text!r}")
    if abs(wsub + wrev - 100) > 1e-9:
        raise MoodleError(f"--weights must add up to 100, not {wsub + wrev:g}")
    return wsub, wrev


def cmd_plan(args):
    path, data, claude = _load(args)
    _readiness(data)
    plan = results.publish_plan(data, claude, args.strictness, _weights(args.weights),
                                args.claude_weight, args.comparison,
                                _bonus(path.parent))
    out = Path(args.out or path.parent / "publish.json")
    out.write_text(json.dumps(plan, indent=1, ensure_ascii=False))
    print(f"{len(plan['submissions'])} submission and "
          f"{len(plan['assessments'])} assessment overrides, "
          f"{len(plan['skipped'])} submissions skipped -> {out}")
    for s in plan["skipped"]:
        print(f"  skipped {s['submissionid']} ({s['author']}): {s['reason']}")


# ------------------------------------------------------------------- publish

def cmd_publish(m, args):
    """Write the overrides and feedback of a publish.json to Moodle.

    Everything is checked against the live workshop first, and any mismatch
    stops the run before a single write: the plan was computed from an
    export, and an export that no longer matches Moodle means stale grades.
    """
    plan = json.loads(Path(args.plan).read_text())
    if args.only:
        plan = _only(plan, args.only)
    workshopid = plan["workshopid"]
    workshop, _ = _find(m, workshopid)

    problems = []
    if workshop["phase"] != PHASE_EVALUATION:
        problems.append(f"the workshop is in the {PHASES.get(workshop['phase'])} "
                        f"phase; overriding grades needs the evaluation phase")
    maxima = plan["maxima"]
    if (workshop["grade"], workshop["gradinggrade"]) != (maxima["submission"],
                                                         maxima["assessment"]):
        problems.append(
            f"the workshop's maxima are {workshop['grade']:g}/"
            f"{workshop['gradinggrade']:g}, the plan needs "
            f"{maxima['submission']:g}/{maxima['assessment']:g}: set 'Grade for "
            f"submission' and 'Grade for assessment' in the workshop settings")

    live_subs = {s["id"]: s for s in m.ws("mod_workshop_get_submissions",
                                          workshopid=workshopid,
                                          perpage=1000)["submissions"]}
    live = _live_assessments(m, plan)
    for s in plan["submissions"]:
        if s["submissionid"] not in live_subs:
            problems.append(f"submission {s['submissionid']} no longer exists")
    for a in plan["assessments"]:
        now = live.get(a["assessmentid"])
        if now is None:
            problems.append(f"assessment {a['assessmentid']} no longer exists")
        elif now["grade"] != a["grade"] or int(now["weight"]) != a["weight"]:
            problems.append(f"assessment {a['assessmentid']} ({a['reviewer']}) "
                            f"changed since the export: export again")

    if problems:
        for p in problems:
            print(f"{'warning' if args.dry_run else 'error'}: {p}", file=sys.stderr)
        if not args.dry_run:
            raise MoodleError(f"{len(problems)} problem(s), nothing was written")

    for s in plan["submissions"]:
        print(f"  submission {s['submissionid']:>6} {s['author']:<30} "
              f"gradeover {s['gradeover']:>3}/{maxima['submission']:g}")
    for a in plan["assessments"]:
        print(f"  assessment {a['assessmentid']:>6} {a['reviewer']:<30} "
              f"gradinggradeover {a['gradinggradeover']:>3}/{maxima['assessment']:g}")
    if args.dry_run:
        print("dry run: nothing was written")
        return

    failed = 0
    for s in plan["submissions"]:
        result = m.ws("mod_workshop_evaluate_submission",
                      submissionid=s["submissionid"], feedbacktext=s["feedback"],
                      feedbackformat=1,
                      published=int(bool(live_subs[s["submissionid"]].get("published"))),
                      gradeover=str(s["gradeover"]))
        failed += _report_write(f"submission {s['submissionid']}", result)
    for a in plan["assessments"]:
        # weight is always written, so the current one is passed back.
        result = m.ws("mod_workshop_evaluate_assessment",
                      assessmentid=a["assessmentid"], feedbacktext=a["feedback"],
                      feedbackformat=1, weight=a["weight"],
                      gradinggradeover=str(a["gradinggradeover"]))
        failed += _report_write(f"assessment {a['assessmentid']}", result)
    print(f"{len(plan['submissions']) + len(plan['assessments']) - failed} written, "
          f"{failed} failed. Check them with `verify`, then run 'Calculate "
          f"submission grades' and 'Calculate assessment grades' in the "
          f"workshop: Moodle only aggregates the overrides then.")


def _live_assessments(m, plan):
    live = {}
    for sid in {a["submissionid"] for a in plan["assessments"]}:
        for a in m.ws("mod_workshop_get_submission_assessments",
                      submissionid=sid)["assessments"]:
            live[a["id"]] = a
    return live


def _only(plan, who):
    """The part of a plan that sets one student's grade: their submission and
    the assessments they made. who is a student number or part of a name,
    and must match exactly one student."""
    people = {}
    for s in plan["submissions"]:
        people[s["authorid"]] = (s["author"], s["number"])
    for a in plan["assessments"]:
        people[a["reviewerid"]] = (a["reviewer"], a["number"])
    needle = who.casefold()
    found = [uid for uid, (name, number) in people.items()
             if needle == (number or "").casefold() or needle in name.casefold()]
    if len(found) != 1:
        names = ", ".join(people[u][0] for u in found) or "nobody"
        raise MoodleError(f"--only {who!r} must match one student, matched {names}")
    uid = found[0]
    print(f"only {people[uid][0]} ({people[uid][1]})")
    return {**plan,
            "submissions": [s for s in plan["submissions"] if s["authorid"] == uid],
            "assessments": [a for a in plan["assessments"] if a["reviewerid"] == uid]}


def _report_write(what, result):
    if result.get("status"):
        return 0
    print(f"error: {what}: {result.get('warnings')}", file=sys.stderr)
    return 1


def cmd_verify(m, args):
    """Read every override of a publish.json back from Moodle and compare it
    with the plan. Moodle stores overrides as percentages of the maxima."""
    plan = json.loads(Path(args.plan).read_text())
    if args.only:
        plan = _only(plan, args.only)
    maxsub, maxrev = plan["maxima"]["submission"], plan["maxima"]["assessment"]
    wrong = []
    for s in plan["submissions"]:
        got = m.ws("mod_workshop_get_submission",
                   submissionid=s["submissionid"])["submission"]
        over = got.get("gradeover")
        if (over is None or abs(over - 100 * s["gradeover"] / maxsub) > 0.01
                or not got.get("feedbackauthor")):
            wrong.append(f"submission {s['submissionid']} ({s['author']}): "
                         f"expected {s['gradeover']}/{maxsub:g}, Moodle has "
                         f"{over if over is None else f'{over * maxsub / 100:g}'}")
    live = _live_assessments(m, plan)
    for a in plan["assessments"]:
        now = live.get(a["assessmentid"], {})
        over = now.get("gradinggradeover")
        if (over is None or abs(over - 100 * a["gradinggradeover"] / maxrev) > 0.01
                or not now.get("feedbackreviewer")):
            wrong.append(f"assessment {a['assessmentid']} ({a['reviewer']}): "
                         f"expected {a['gradinggradeover']}/{maxrev:g}, Moodle has "
                         f"{over if over is None else f'{over * maxrev / 100:g}'}")
    for w in wrong:
        print(f"mismatch: {w}", file=sys.stderr)
    print(f"{len(plan['submissions'])} submissions and {len(plan['assessments'])} "
          f"assessments checked, {len(wrong)} mismatch(es)")
    if wrong:
        raise MoodleError("Moodle does not match the plan")


def cmd_csv(args):
    """Two CSVs next to the plan, studentId and grade on a 0..scale scale:
    the submission grade, and the review grade (the mean of the student's
    assessment grades, as Moodle aggregates them)."""
    path = Path(args.plan)
    plan = json.loads(path.read_text())
    maxsub, maxrev = plan["maxima"]["submission"], plan["maxima"]["assessment"]
    reviews = {}
    for a in plan["assessments"]:
        reviews.setdefault(a["number"], []).append(a["gradinggradeover"])
    tables = {
        "submission-grades.csv": ("submissionGrade", {
            s["number"]: args.scale * s["gradeover"] / maxsub
            for s in plan["submissions"]}),
        "review-grades.csv": ("reviewGrade", {
            n: args.scale * sum(g) / len(g) / maxrev for n, g in reviews.items()}),
    }
    for name, (column, grades) in tables.items():
        with open(path.parent / name, "w", newline="") as f:
            out = csv.writer(f)
            out.writerow(["studentId", column])
            for number in sorted(grades):
                out.writerow([number, f"{grades[number]:.2f}"])
        print(f"{len(grades)} students -> {path.parent / name}")
    if plan["skipped"]:
        print(f"warning: {len(plan['skipped'])} skipped submission(s) are not "
              f"in submission-grades.csv", file=sys.stderr)


# ---------------------------------------------------------------------- main

def _grading_args(p):
    p.add_argument("export", help="path to a workshop.json written by export")
    p.add_argument("--weights", default="60/40",
                   help="split, submission/review, in percent (default: 60/40)")
    p.add_argument("--strictness", type=float, default=2,
                   help="review grade = 100 - strictness x weighted mean "
                        "per-criterion error, floored at 0 (default: 2)")
    p.add_argument("--claude-weight", type=float, default=4,
                   help="weight of Claude's review in the submission grade, a "
                        "student's being 1 (default: 4)")
    p.add_argument("--comparison", type=float, choices=results.COMPARISON_LEVELS,
                   help="Moodle's comparison of assessments level, 1 (very "
                        "strict) to 9 (very lax) (default: inferred from the "
                        "grading grades Moodle stored)")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="get a Moodle token and save it (run in "
                                     "your own terminal)")
    p.add_argument("--url", help=f"Moodle address (default: {DEFAULT_URL})")
    p.add_argument("--token", action="store_true",
                   help="paste an existing token instead of signing in")

    sub.add_parser("list", help="show every workshop in your courses")

    p = sub.add_parser("export", help="download submissions, peer assessments and files")
    p.add_argument("workshopid", type=int, help="the wid column of `list`")
    p.add_argument("--out")
    p.add_argument("--no-files", action="store_true")

    p = sub.add_parser("prepare", help="lay out rubric, files and queues for "
                                       "Claude (offline)")
    p.add_argument("export", help="path to a workshop.json written by export")

    p = sub.add_parser("confirm", help="count a low-confidence Claude review the "
                                       "teacher has checked (offline)")
    p.add_argument("export", help="path to a workshop.json written by export")
    p.add_argument("submissionid", type=int)

    report = sub.add_parser("report", help="write results-workshop-<name>.md "
                                           "(offline)")
    plan = sub.add_parser("plan", help="write publish.json, the overrides and "
                                       "feedback to send to Moodle (offline)")
    for p in (report, plan):
        _grading_args(p)
    report.add_argument("--gap", type=float, default=15,
                        help="highlight peer grades this many points or more "
                             "away from Claude's (default: 15)")
    report.add_argument("--out", help="default: next to the export")
    plan.add_argument("--out", help="default: publish.json next to the export")

    for name, text in (("publish", "write a publish.json's overrides and "
                                   "feedback to Moodle"),
                       ("verify", "check that Moodle holds what a publish.json "
                                  "planned")):
        p = sub.add_parser(name, help=text)
        p.add_argument("plan", help="path to a publish.json written by plan")
        p.add_argument("--only", metavar="STUDENT",
                       help="only what sets this student's grade (a student "
                            "number or part of a name)")
        if name == "publish":
            p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("csv", help="write submission and review grades as CSV "
                                   "(offline)")
    p.add_argument("plan", help="path to a publish.json written by plan")
    p.add_argument("--scale", type=float, default=20,
                   help="grade scale of the CSVs (default: 20)")

    args = parser.parse_args()
    try:
        standalone = {"login": cmd_login, "prepare": cmd_prepare,
                   "confirm": cmd_confirm, "report": cmd_report,
                   "plan": cmd_plan, "csv": cmd_csv}
        if args.cmd in standalone:
            standalone[args.cmd](args)
            return 0
        m = Moodle()
        {"list": cmd_list, "export": cmd_export, "publish": cmd_publish,
         "verify": cmd_verify}[args.cmd](m, args)
    except MoodleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
