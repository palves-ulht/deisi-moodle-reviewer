# How the grades are computed

All of this is in `scripts/report.py`. Percentages are 0-100; the publish
plan converts them to points out of the workshop's maxima.

## Submission grade

The weighted mean of the peer assessments that were filled in, as Moodle
computes it, with Claude's review added as one more assessment weighing **4x**
a student's (`--claude-weight`). Each assessment's grade is Moodle's
accumulative grade: every criterion normalised to its maximum, then the
weighted mean with the rubric's criterion weights. With one peer missing, two
students plus Claude decide it; Claude's weight keeps one generous or harsh
classmate from setting the grade alone.

## Review grade

How close each of the student's assessments is to Claude's, criterion by
criterion:

- error = the weighted mean, over the criteria (rubric weights), of
  |peer grade - Claude's grade| / criterion maximum;
- score = 100 - **2** x error x 100 (`--strictness`), floored at 0. At 2, an
  assessment off by half the scale on every criterion scores 0;
- plus the **comment bonus**, 0-10, capped at 100.

A student's review grade is the mean over the assessments allocated to them.

Moodle's own review grade ("comparison with the best assessment") rewards
agreeing with the other peers, so a reviewer who sides with a generous
majority scores near 100 even when everyone missed the same faults. The
report shows it next to Claude's for reference, recomputed with Claude's
review added to every submission. The workshop's "comparison of assessments"
level is not exposed by Moodle's web services; the report infers it by
replaying Moodle's calculation at every level (`--comparison` overrides).

## Rules

- **Every allocated assessment is mandatory.** One left undone counts 0 in the
  review grade, and the student's final is 0 (submission and review overrides
  are both 0).
- **Low-confidence Claude reviews are left out**: that submission's grade
  comes from the peers alone, and its assessments do not count toward anyone's
  review grade. `workshop.py confirm` brings a review back in once the teacher
  has checked it.
- **Final grade**: submission x 60% + review x 40% (`--weights`), applied in
  Moodle through the workshop's two maxima.

## Choosing the knobs

- Too harsh on reviewers who agreed with a generous majority: lower
  `--strictness` (1.5 or 1). It raises every reviewer and keeps their order.
- Peers should weigh more in the submission grade: lower `--claude-weight`.
- Changing the rubric's own criterion weights changes both the submission
  grades and the review grades; avoid it after the peers have graded.
