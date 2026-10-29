---
name: review-moodle-workshop
description: Review a peer-assessed Moodle workshop ("Workshop" activity) on the Lusófona Moodle with Claude. Claude grades every submission (photos of diagrams or handwritten work, code, PDFs, online text) against the workshop's rubric without seeing the peers' grades, judges the quality of the peers' comments, compares every peer assessment with its own, proposes submission and review grades, and, only when the teacher asks, publishes grades and feedback in Portuguese to Moodle and exports them as CSV. Also guides the first-time Moodle connection (getting a personal token). Use when a teacher asks to review, check, audit, grade or publish a Moodle workshop or its peer assessments, or to connect Claude to Moodle for that.
---

# Review a Moodle workshop with Claude

The teacher names a workshop; you take it from export to published grades,
stopping for the teacher's decisions. Arguments, all optional: the workshop
id and the submission/review split, e.g. `61` or `61 50/50` (default 60/40).

## Ground rules

- **Scripts.** Everything goes through `workshop.py` in the `scripts/` folder
  next to this file (the base directory shown when the skill loads). Below,
  `W` stands for `python3 "<base directory>/scripts/workshop.py"` with the
  real absolute path. Python 3.9+, standard library only. On Windows the
  command is `py` instead of `python3`.
- **Working folder.** Data goes to `exports/ws<wid>/` under the current
  directory. It holds student names, numbers and work: personal data. If the
  current directory is a git repository, make sure `exports/` is in its
  `.gitignore` before exporting. Never copy anything from `exports/` elsewhere.
- **Blind grading.** You (this session) will see peer grades. The grading
  agents must not. Never write or edit files in `claude/` yourself: grading
  and regrading always go to fresh subagents with the prompts in
  `references/prompts.md`, which forbid opening `workshop.json`.
- **Writes to Moodle.** Only `W publish` without `--dry-run` writes. Run it
  only when the teacher explicitly asks, one student first.
- **Never ask for the password or the token in the chat.** See step 0.
- Talk to the teacher in the language they use. Everything students read
  (comments, bonus reasons, feedback) is in European Portuguese.

## 0. Connect to Moodle (once per teacher)

Run `W list`. If it says there is no token or the token is invalid, follow
`references/setup.md` with the teacher: they run `W login` **in their own
terminal** (give them the full command with the absolute path), type their
Moodle username and password there, and the token is saved to
`~/.config/deisi-moodle-reviewer/config.json`. Then run `W list` again.

## 1. Pick the workshop and check it is ready

`W list` shows every workshop in the teacher's courses. The id to use is the
`wid` column (the instance id), not the `id=` in the Moodle URL (that is the
`cmid`). Then:

```bash
W export <wid>
```

It downloads submissions, files, every peer assessment with its
per-criterion grades and comments, and the students' names and numbers.
Stop and tell the teacher if it warns that:

- **the assessment phase is still open**: peer assessments may be missing or
  change. Ask them to switch the workshop to the evaluation phase;
- **Moodle has not calculated the grades**: ask them to run "Calculate
  submission grades" and "Calculate assessment grades" in the workshop.

Then export again. Go on regardless only if the teacher explicitly says so.
Only the accumulative grading strategy is supported; `export` says so if the
workshop uses another.

## 2. Reference material and interpretation notes

```bash
W prepare exports/ws<wid>/workshop.json
```

It writes `review/rubric.md` (instructions and every criterion), stages each
submission under `review/files/<submissionid>/` (photos as JPEG, zips
extracted, code and PDFs copied, online text as `texto-online.html`) and the
queues `review/queue.json` and `review/bonus-queue.json`.

Read `review/rubric.md`. Rubrics often grade against material that is not in
Moodle: a starting diagram, statement, slides, a cheat sheet, the expected
API of a programming exercise. If the instructions or criteria mention any
such material and `exports/ws<wid>/reference/` is empty, ask the teacher to
put it there before grading. If they say to go on without it, tell every
grader which criteria cannot be checked.

`exports/ws<wid>/notes.md` holds the teacher's decisions on how to read the
rubric. It is binding for every agent. Create it as soon as there is a first
decision (step 4). If the teacher has standing preferences (e.g. "do not run
the code", "case does not matter"), write them there before grading.

## 3. Grade every submission (blind)

Split `pending` of `review/queue.json` into batches (5 submissions per agent
for photos or short files, 2-3 for sizeable code projects) and spawn one
`general-purpose` subagent per batch, **all in a single message** (at most 10
per message). Give each the grading prompt from `references/prompts.md`,
filled in. Re-run `W prepare`: it must report 0 pending, otherwise grade the
rest.

## 4. Settle the ambiguities

Collect the ambiguities the graders reported. Then compare the batches: the
same situation graded differently by two agents (e.g. the same fault in two
submissions, one deducted and one not) is the most important thing to
surface, because it is unfair between students.

Present the teacher with the inconsistencies first, then the readings the
graders applied uniformly, each with a recommendation. For each decision:

1. Add it to `notes.md`, phrased as a rule.
2. Find the reviews it affects (read `comments[i]` in `claude/*.json`; reading
   is fine, writing is not).
3. Spawn a fresh subagent with the regrade prompt from
   `references/prompts.md`, naming the reviews and criteria, and asking it to
   also scan the other reviews for the same situation.

Low-confidence reviews (unreadable photo, empty or wrong file) are left out
of everyone's review grade and their submission keeps the peers' grade. Ask
the teacher to look at those submissions. If Claude's review is right, run
`W confirm exports/ws<wid>/workshop.json <submissionid>`, which makes it count;
otherwise regrade it with the teacher's reading in `notes.md`.

## 5. Judge the comments (bonus)

A peer assessment can gain up to +10 on its review score (capped at 100) for
comments that are specific, useful and **correct** against the submission.
Wrong claims lower it; "bom trabalho" earns nothing. Do this after step 4,
because the judges check comments against Claude's reviews.

Re-run `W prepare`, split `pending` of `review/bonus-queue.json` into batches
of about 10, keeping the assessments of one submission together, and spawn
one subagent per batch with the bonus prompt from `references/prompts.md`.
Re-run `W prepare` until the bonus queue is empty.

## 6. Report

```bash
W report exports/ws<wid>/workshop.json --weights <submission>/<review>
```

It writes `exports/ws<wid>/results-workshop-<name>.md`: per submission each
peer grade next to Claude's; per reviewer Moodle's review grade, the bonus
and the review grade against Claude; current and proposed final grades; and
per-criterion details with Claude's reasoning. How the grades are computed
is in `references/grading.md`; explain it when the teacher asks. A malformed
file in `claude/` or `bonus/` stops it with the file name: regrade that one.

Tell the teacher, briefly:

- the reviewers with the lowest review grade, with the reason that stands out
  in the details (e.g. "gave 100 to everything", "missed the missing month
  parameter in all three");
- the submissions whose peer mean is furthest from Claude's grade;
- the best comments and their authors (highest bonus, from `bonus/*.json`,
  field `best`);
- students who left allocated assessments undone: their final is 0;
- any remaining low-confidence review.

Say that Claude's review is a reference, not ground truth, and suggest
spot-checking a few rows of the details table against the submissions.

If the teacher finds the grades too harsh or too lenient, the knobs are
`--strictness` (default 2), `--claude-weight` (default 4) and `--weights`. Show
the effect of a change on a few named students before adopting it. Prefer
these over changing the rubric's criterion weights.

## 7. Publish (only on the teacher's request)

1. The teacher sets the workshop's "Grade for submission" and "Grade for
   assessment" to the split (e.g. 60 and 40) and leaves it in the evaluation
   phase. `publish` refuses otherwise.
2. `W plan exports/ws<wid>/workshop.json` (same options as `report`) writes
   `publish.json`: every override and the Portuguese feedback text.
3. `W publish exports/ws<wid>/publish.json --dry-run`. Show the teacher one
   author feedback and one reviewer feedback from `publish.json`.
4. On the teacher's go: `W publish ... --only <student number>`, then
   `W verify ... --only <student number>`. Ask the teacher to run "Calculate
   submission grades" and "Calculate assessment grades" and check that
   student in Moodle.
5. On the teacher's go: `W publish exports/ws<wid>/publish.json`, then
   `W verify exports/ws<wid>/publish.json`. Students who skipped assessments
   are planned at 0, which `verify` checks like any other value.
6. The teacher recalculates both grades in the workshop again, and closes it
   when satisfied so the students see grades and feedback.

Re-running `publish` writes the same values again, so it is safe after a
partial failure.

## 8. CSV export

```bash
W csv exports/ws<wid>/publish.json [--scale 20]
```

Writes `submission-grades.csv` (`studentId,submissionGrade`) and
`review-grades.csv` (`studentId,reviewGrade`) next to the plan, on a 0-20
scale by default, from exactly what was published.

## When something surprises you

`references/moodle-notes.md` has the Moodle behaviour this relies on and the
traps already hit. Read it before improvising around an error.
