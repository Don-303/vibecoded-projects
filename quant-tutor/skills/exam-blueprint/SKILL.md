---
name: exam-blueprint
description: Build a practice exam or quiz from a topic, syllabus, or set of notes — mixed item types, a difficulty spread, and a full answer key with explanations. Use when the user wants a test, quiz, mock exam, practice questions, or to "quiz me" on material.
tags: [assessment, practice]
---

# Exam Blueprint

Generate a balanced practice assessment with an answer key.

## Before writing items

Pin down four things (ask if missing): **topic/scope**, **level** (grade or course), **length** (number of items or time), and **emphasis** (recall vs. application). Default to 10 items, mixed types, if unspecified.

## Coverage & difficulty

- Map the scope into sub-topics; distribute items so no sub-topic dominates by accident.
- Spread difficulty roughly **30% easy / 50% medium / 20% hard**. Easy = recall; medium = apply a single idea; hard = combine ideas or transfer to a new case.
- Tag each item with its sub-topic and difficulty so gaps are visible.

## Item types & quality

- **MCQ** — one unambiguous correct option; distractors must be plausible and reflect common mistakes (no "all of the above" padding, no joke options).
- **Short answer** — one clear expected response; state what a full-credit answer contains.
- **Worked/application** — a scenario requiring steps; allot partial-credit points per step.

Never write a question whose answer is given away by its phrasing or by another item on the test.

## Output

1. **The exam** — items numbered, type and points marked, no answers shown.
2. **Answer key** — correct answer + a one-line *why* for each (and why key distractors are wrong, for MCQ).
3. **Blueprint table** — item → sub-topic → difficulty, so the user sees the coverage.

Offer a "test mode": present items one at a time, withhold the key, and grade with explanations after each response.
