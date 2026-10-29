# DEISI Moodle reviewer for Claude Code

A Claude Code skill for DEISI teachers who use **Moodle workshops** (the
"Workshop" activity, with peer assessment) on the Lusófona Moodle.

| Skill | What it does |
|---|---|
| `review-moodle-workshop` | Claude grades every submission against the workshop's rubric without seeing the peers' grades (photos of diagrams or handwritten work, code, PDFs, online text), judges the quality of each peer's comments, compares every peer assessment with its own, and proposes submission and review grades. On your request, it publishes the grades and feedback in Portuguese to Moodle and exports them as CSV. |

It stops for your decisions: how to read ambiguous criteria, which settings
to use, and before anything is written to Moodle. Nothing is published until
you ask, and it publishes one student first for you to check.

## Install

```
/plugin marketplace add palves-ulht/deisi-moodle-reviewer
/plugin install deisi-moodle-reviewer@deisi-moodle-reviewer
```

`/plugin update deisi-moodle-reviewer` picks up later versions.

Requires Python 3.9 or later (no packages to install). On Windows use `py`
where the skill says `python3`.

## First use: connect to Moodle

Start Claude Code in a folder of your own for this work, for example
`~/moodle-reviews`, and ask:

```
review Moodle workshop
```

The first time, Claude gives you a `workshop.py login` command to run **in
your own terminal**. It asks for your Moodle username and password, gets a
personal token from Moodle (the same kind the Moodle mobile app uses), and
saves only the token, in `~/.config/deisi-moodle-reviewer/config.json`,
readable only by you. Your password is never stored and never goes through
Claude. Never paste the password or the token into the chat.

The token acts as you, with your permissions. To revoke it:
https://moodle.ensinolusofona.pt/user/managetoken.php

## Using it

```
/deisi-moodle-reviewer:review-moodle-workshop 61
```

or just ask Claude to review a workshop; it lists your workshops if you do
not give the id. A typical run:

1. Close the assessment phase (switch to evaluation) and run "Calculate
   submission grades" and "Calculate assessment grades" in the workshop.
2. Claude exports the workshop and asks for any material the rubric refers to
   that is not in Moodle (a starting diagram, slides, the exercise statement).
3. Claude grades every submission, then shows you the criteria it found
   ambiguous and any inconsistency between graders. Your decisions are kept in
   `notes.md` and applied to every submission.
4. Claude judges the peers' comments and writes a report comparing each peer
   assessment with its own, with proposed grades.
5. On your request: plan, dry run, publish one student, check, publish all,
   and verify. Then you recalculate the grades in Moodle and close the
   workshop.
6. Optionally, CSVs of submission and review grades on a 0-20 scale.

How the grades are computed is in
`skills/review-moodle-workshop/references/grading.md`.

## Student data

Everything is written to `exports/ws<id>/` in the folder where you started
Claude: submissions, names, student numbers and grades. Keep that folder
private and out of any git repository.
