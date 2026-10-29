# Moodle behaviour this relies on

Verified on the Lusófona Moodle (4.5) by reading the source and on live
workshops. Source: https://raw.githubusercontent.com/moodle/moodle/MOODLE_405_STABLE/
(`mod/workshop/classes/external.php`, `mod/workshop/locallib.php`,
`mod/workshop/form/accumulative/lib.php`).

## Ids

- The workshop id the web services want is the **instance id** (`wid` in
  `workshop.py list`). The `id=` in `.../mod/workshop/view.php?id=31280` is the
  course module id (`cmid`), a different number.
- `mod_workshop_get_submission_assessments` takes only `submissionid`; adding
  `workshopid` fails with a misleading `invalidparameter`.

## Reading

- The submission grade is absent from `mod_workshop_get_submissions` until
  Moodle has calculated the grades: read it with `.get("grade")`.
- Per-criterion grades are not in the assessment summary: they come from
  `mod_workshop_get_assessment_form_definition` (mode `assessment`), in its
  `current` array. In its `fields` array, `grade__idx_N` is the criterion's
  **maximum**, not a grade.
- The response key is `dimenssionscount`, with Moodle's own double s.
- Accumulative grades normalise each criterion first:
  `100 * sum(w_i * g_i / max_i) / sum(w_i)`, not `sum(w_i g_i) / sum(w_i max_i)`.
  `moodle.assessment_grade()` matches Moodle exactly; do not re-derive it.
- Attachments download from the `fileurl` with the token as a query parameter.

## Writing

- Overrides (`mod_workshop_evaluate_submission` / `_evaluate_assessment`) need
  the **evaluation phase** and `mod/workshop:overridegrades` (editing teacher).
- `gradeover` and `gradinggradeover` are passed in **whole points** of the
  workshop's current maxima (Moodle rejects decimals), and Moodle stores them
  as **percentages**: 50 out of 60 reads back as 83.33. That is why `publish`
  refuses when the maxima differ from the plan's split.
- `evaluate_assessment` always writes the weight: the current one is passed
  back unchanged.
- Moodle shows the "feedback for the author" only on a submission whose grade
  is overridden; the plan overrides every submission it has a review for.
- Nothing reaches the gradebook until the teacher runs "Calculate submission
  grades" and "Calculate assessment grades" in the workshop. The review grade
  is then the mean of the student's assessment overrides.

## Other strategies

Only the accumulative grading strategy is supported. Rubric, number of errors
and comments strategies have different form fields and grade formulas.
