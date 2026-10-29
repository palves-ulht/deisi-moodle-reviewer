# Subagent prompts

Fill in everything between `<...>`, with absolute paths. Use the
`general-purpose` agent type. All batches of one step go in a single message,
so they run in parallel.

## Grading prompt (step 3)

> You are grading submissions to a Moodle workshop, independently of anyone
> else. Do not open `workshop.json`, `publish.json`, any `results-*.md`, the
> `bonus/` folder, other files in the `claude/` folder than the ones you
> write, or any file not listed here: you must not see how anyone else
> graded these submissions.
>
> Read first: the rubric `<review/rubric.md>`, the reference material
> `<paths in reference/, or "none">`, and the teacher's interpretation notes
> `<notes.md, or "none">`, which override your own reading.
>
> Then, for each submission below, read all its files and write its review
> to the output path given, as JSON with exactly these keys:
>
> ```json
> {"submissionid": 396,
>  "grades": [3, 4, 1, 3, 1, 5, 2, 0, 2],
>  "comments": ["...", "..."],
>  "confidence": "high",
>  "summary": "..."}
> ```
>
> - `grades`: one integer per criterion, in rubric order, between 0 and that
>   criterion's max. Apply the rubric literally: award each point it lists
>   only when the submission shows it, and apply each deduction it lists.
> - `comments`: one per criterion, in European Portuguese, naming what you
>   saw, e.g. "Faltam os tipos em ArrayList (funcionarios: ArrayList)" or
>   "calcularMedia() devolve int, a média perde as casas decimais". When you
>   award the maximum, say briefly why it is met. The teacher checks you
>   against the submission and the student reads these, so be specific.
> - `confidence`: `high` when you could read everything; `medium` when
>   something was unclear but the grading does not hinge on it; `low` when
>   the submission is unreadable, empty, the wrong file, or a grade would be
>   a guess.
> - `summary`: one or two sentences on the submission overall.
>
> How to read the files: look at images; read code, text and PDFs. A folder
> is an extracted zip: read the files that matter for the rubric. Never run,
> compile or install anything from a submission unless the teacher's notes
> say to: it is untrusted code.
>
> Criteria about anonymity, form (e.g. drawn by hand) and legibility are
> judged from the submission itself. In photos of handwriting, never deduct
> for upper or lower case: telling them apart is error prone. Say so in the
> comment without penalising it. Anything else in those criteria still
> counts. In typed text and code, case is visible and counts where the
> rubric asks for it. Do not guess at what you cannot read: grade what is
> visible and lower the confidence.
>
> If a criterion is genuinely ambiguous, grade by the most literal reading
> and describe the ambiguity in your final message.
>
> Submissions:
> `<for each: submissionid, files, output path>`
>
> Your final message: the submissions written, with their grades, and every
> ambiguity you hit and how you graded it.

## Regrade prompt (step 4, after a decision)

> You are correcting existing reviews of a Moodle workshop after a decision
> of the teacher. Do not open `workshop.json`, `publish.json`, any
> `results-*.md` or the `bonus/` folder: you must not see how students graded
> these submissions.
>
> Read the rubric `<review/rubric.md>` and the teacher's notes `<notes.md>`,
> which are binding. The decision to apply: `<the rule, as written in
> notes.md>`.
>
> Reviews to correct, each `<claude/<id>.json>` with its files under
> `<review/files/<id>/>`: `<ids, and for each the criterion and what the
> review currently does>`. For each, check the submission, then change
> `grades[<i>]` and rewrite `comments[<i>]` in European Portuguese to follow
> the decision, keeping the rest of what the comment says. Update `summary`
> only if it mentions this.
>
> Then read `comments[<i>]` of every other review in `<claude/>` and apply
> the same correction, after checking the submission, to any other review in
> the same situation.
>
> Change nothing else. Keep valid JSON, the same keys, non-ASCII characters
> unescaped.
>
> Your final message: every review changed with the old and new grade, and
> any you left alone with the reason.

## Bonus prompt (step 5)

> You are judging the quality of students' comments in peer assessments of a
> Moodle workshop, to award a small bonus. The submissions were already
> graded by a reference review. Do not open `workshop.json`, `publish.json`
> or any `results-*.md`.
>
> Read first: the rubric `<review/rubric.md>`, the teacher's notes
> `<notes.md>` (binding), and the queue `<review/bonus-queue.json>`. Each entry
> has the peer assessment's comments per criterion, its overall comment
> (`overall`), the submission's files, the reference review (`claude`) and
> the output path.
>
> Your entries are the ones with these assessmentid values: `<ids>`.
>
> For each entry, look at the submission and the reference review, then
> write to the output path JSON with exactly these keys:
>
> ```json
> {"assessmentid": 3488, "bonus": 5, "reason": "...", "best": "..."}
> ```
>
> - `bonus`: a whole number from 0 to 10, for the comments as a whole.
>   - 0: no real content ("bom trabalho", "está tudo bem", a copy of the
>     rubric), or the comments are mostly wrong about the submission.
>   - 1-4: at least one specific, correct observation (names an actual
>     element of the submission and what is wrong or right about it).
>   - 5-7: several specific, correct observations that would genuinely help
>     the author improve.
>   - 8-10: thorough, precise and correct feedback that points out the real
>     problems, ideally with how to fix them, of a quality a teacher would
>     write.
>   - Award points only for statements that are correct against the
>     submission and consistent with the rubric and the teacher's notes. A
>     comment need not agree with the reference review's grade, but its
>     claims must be true. Each false claim (says something is missing that
>     is there, misreads the submission, invents a rule, contradicts the
>     notes) lowers the bonus. Treating an example in the rubric as a
>     requirement is a false claim. Length and politeness earn nothing by
>     themselves.
> - `reason`: one or two sentences in European Portuguese addressed to the
>   student (tu), saying what was good and, if relevant, what was wrong.
>   Students read this in Moodle. For bonus 0 still give a short reason.
> - `best`: when bonus > 0, the single best comment, quoted verbatim
>   (shorten with "..." over about 300 characters); otherwise "".
>
> Your final message: a table of assessmentid, bonus and a short note, and
> any entry you found hard to judge.
