---
name: flashcard-deck
description: Turn notes, a chapter, or a topic into spaced-repetition flashcards (Q/A and cloze deletions) ready to study or import into Anki. Use when the user wants to memorize, review, "make flashcards", or drill facts and definitions.
tags: [practice, memory]
---

# Flashcard Deck

Convert source material into atomic, testable flashcards.

## Principles

- **One fact per card.** If a card has "and" in the answer, split it. Atomicity is what makes recall reliable.
- **Test recall, not recognition.** The front must force the learner to produce the answer, not pick it. Avoid yes/no fronts.
- **Front is unambiguous.** Exactly one correct answer. Add just enough context that the prompt isn't guessable from phrasing alone.
- **Prefer "why/how" over "what"** for anything conceptual — understanding cards outlast definition cards.

## Card types

- **Q/A** — `Q:` a question, `A:` the shortest correct answer.
- **Cloze** — a sentence with the key term blanked: `The mitochondria is the {{site}} of the cell.`
- **Definition → term** (reverse) — give the definition, ask for the term, so recall works both directions.

## Workflow

1. Extract the 5–20 ideas worth remembering. Drop anything that's lookup-able and not worth memorizing.
2. Write one card per idea using the type that best forces recall.
3. For dense facts, add a one-line cloze sibling so the fact is hit from two angles.
4. Order easy → hard; group by sub-topic.

## Output

Default to a clean numbered list of `Q:` / `A:` pairs. If the user mentions **Anki**, also provide a tab-separated block (`front<TAB>back`, one per line) they can paste into Anki's import. Offer to expand any card into an explanation on request.
