"""Wraps a local Ollama model as an interactive tutor."""
from __future__ import annotations

from collections.abc import AsyncIterator

from ollama import AsyncClient

from app import rag
from app.curriculum import Lesson

# General-purpose instruct model rather than the Coder variant: this tutor
# mostly explains concepts (econometrics, portfolio theory, etc.) and only
# secondarily reviews code. Qwen3-4B-Instruct matches or beats Qwen2.5-7B on
# reasoning benchmarks at roughly double the tokens/sec on this CPU-only
# machine. The exact tag matters: qwen3:4b-instruct is the non-thinking 2507
# build; plain qwen3:4b would burn NUM_PREDICT on hidden reasoning tokens.
MODEL = "qwen3:4b-instruct"

# The model's native window is far larger; 8192 comfortably fits a full source
# lecture or two plus conversation history without the extra prefill cost of
# the full window on CPU-only inference.
NUM_CTX = 8192

# This machine is CPU-only (12 threads, hybrid P/E cores) with no GPU
# acceleration available to Ollama. Two changes matter a lot here:
# - KEEP_ALIVE: Ollama's default unload-after-5-min-idle causes a slow cold
#   reload (~1-2 min) on the next message. Keep it loaded for a full session.
# - NUM_THREAD: llama.cpp's default thread count under-uses this CPU (we
#   observed ~2 cores active during inference); leave 2 threads free for the
#   OS/UI rather than requesting all 12.
KEEP_ALIVE = "24h"
NUM_THREAD = 10

# Bound reply length: the tutor is instructed to be concise, and on CPU each
# extra token is latency. 640 tokens comfortably fits an explanation plus a
# code exercise.
NUM_PREDICT = 640

# Force the local instance regardless of any OLLAMA_HOST pointed at a remote
# peer in the user's shell environment -- this app must work fully offline.
_client = AsyncClient(host="http://127.0.0.1:11434")

SYSTEM_TEMPLATE = """You are an interactive, patient tutor teaching machine \
learning for finance, econometrics, and quantitative finance. You are running \
fully offline as a small local model, so keep explanations concise and concrete -- \
prefer short explanations plus a small runnable code exercise over long essays.

Current module: {module_title}
Current lesson: {lesson_title}

Teaching approach:
- Introduce the concept briefly (a few sentences, not a lecture).
- Give the student one small, concrete coding exercise to try, using a real \
  or synthetic finance/econometrics dataset where relevant (e.g. price series, \
  returns, factor data). Prefer numpy/pandas/statsmodels/scikit-learn.
- Exercises should be skeleton code with TODO comments marking the parts the \
  student fills in; worked examples should be complete, runnable as-is (use \
  np.random with a seed for synthetic data -- never require CSV files the \
  student doesn't have), with a brief note on what the output shows.
- When the student pastes code or its output, review it directly: point out \
  bugs, misconceptions, or ways to improve it. Be specific about lines/values.
- Only move to a harder exercise once the current one basically works.
- If the student asks to move on or says they're done, summarize the key \
  takeaway in 1-2 sentences.
- End every reply with either a short question checking understanding or a \
  concrete task -- never a dead end.

When the student is stuck, escalate hints one step at a time -- never jump \
straight to full code: (1) name the concept involved, (2) point to the \
relevant line, (3) show the shape of the solution (structure, not full \
code), (4) give the code only if they ask directly or remain stuck after \
genuinely trying -- then ask them to explain each line back to you.

When helping with an error: treat errors as information, not failure. \
Explain one error at a time (never the whole traceback at once), structured \
as: likely cause, smallest fix, why it works, then a follow-up check -- ask \
them to make one intentional change and predict the result, to confirm they \
understood the fix rather than just pasting a patch.
"""

# Extra coaching style for the beginner Python module, adapted from the
# "python-playground-coach" teaching method: predict-then-run loops beat
# lecturing for absolute basics. Applied per-module so the system prompt
# stays deterministic per lesson (prefix-cache requirement).
PLAYGROUND_COACH_TEMPLATE = """
This is a beginner Python lesson, so use a playground-style teaching loop:
- Set a tiny goal: exactly one concept at a time (variables, loops, \
  functions, lists, dictionaries, files, errors).
- Show a minimal runnable example, under 10 lines.
- Ask the student to PREDICT what it prints before they run it.
- Then give a small challenge: a short edit or extension of that example.
- Do not move to the next concept until this one is demonstrated (their \
  prediction was right, or the challenge was solved).
Structure lesson replies as: Concept / Example (under 10 lines) / Challenge \
/ Expected output / One extension idea. Good mini-projects to build toward: \
number guessing game, quiz score calculator, shopping list manager, \
word/character counter, CSV cleanup on a tiny inline sample.
"""

# Modules whose lessons get the playground-coach block (beginner Python).
PLAYGROUND_MODULE_IDS = {"python-foundations"}

# Sent on the student's behalf when they open a lesson with no history yet.
AUTO_START_MESSAGE = "Please introduce this lesson and give me the first exercise."

REFERENCE_TEMPLATE = """

Reference material (from your local lecture corpus -- cite the title in \
brackets when you draw on one, e.g. "[{example_title}]"). If none of it is \
actually relevant to the question, ignore it and answer from your own \
knowledge instead -- say so if you're doing that.

{blocks}
"""

SOURCE_LESSON_TEMPLATE = """

Primary lesson material (the actual source lecture(s) for this topic, from \
your local corpus). Walk through this WITH the student interactively rather \
than dumping it all at once: explain one section in your own words, then \
pause with a short question or small exercise checking understanding before \
moving to the next section. Ground claims, formulas, and code in what's \
below. In every reply, start by naming which source you're drawing on, \
e.g. "From [{first_source_name}]:", so the student always knows this is \
coming from real lecture material and not just your own memory. If multiple \
documents are given, work through them in order across the conversation, \
using the chat history to judge how far you've already gotten.

{blocks}
"""


def system_prompt(lesson: Lesson, extra_block: str = "") -> str:
    prompt = SYSTEM_TEMPLATE.format(
        module_title=lesson.module_title, lesson_title=lesson.title
    )
    if lesson.module_id in PLAYGROUND_MODULE_IDS:
        prompt += PLAYGROUND_COACH_TEMPLATE
    return prompt + extra_block


def _build_reference_block(chunks: list[rag.Chunk]) -> str:
    if not chunks:
        return ""
    blocks = "\n\n".join(f"[{c.title}]\n{c.text}" for c in chunks)
    return REFERENCE_TEMPLATE.format(example_title=chunks[0].title, blocks=blocks)


# Total character budget for injected source material per lesson. Prefill is
# the dominant first-message cost on CPU, and it scales with prompt length --
# splitting a fixed budget across sources bounds the worst case (previously a
# 3-source lesson injected up to 10.5k chars).
SOURCE_BUDGET_CHARS = 7000


def _build_source_lesson_block(lesson: Lesson) -> str:
    if not lesson.sources:
        return ""
    per_source = min(3500, max(2200, SOURCE_BUDGET_CHARS // len(lesson.sources)))
    parts = []
    for src in lesson.sources:
        text = rag.load_source_text(src.repo, src.file, max_chars=per_source)
        if text:
            parts.append(f"=== {src.file} ({src.repo}) ===\n{text}")
    if not parts:
        return ""
    return SOURCE_LESSON_TEMPLATE.format(
        first_source_name=lesson.sources[0].file, blocks="\n\n".join(parts)
    )


# Memoized per-lesson prompt block. Keeping the system prompt byte-identical
# across turns within a lesson lets llama.cpp's prefix cache reuse the
# prefilled system prompt + history instead of re-processing the whole
# conversation every message -- on CPU that's the difference between a
# follow-up reply starting in seconds vs. re-paying a 30-60s prefill. This is
# why retrieval is keyed on the lesson title only, not the user's message.
_lesson_block_cache: dict[str, str] = {}


async def _lesson_extra_block(lesson: Lesson) -> str:
    cached = _lesson_block_cache.get(lesson.id)
    if cached is not None:
        return cached
    block = _build_source_lesson_block(lesson)
    if not block:
        retrieved = await rag.retrieve(lesson.title)
        block = _build_reference_block(retrieved)
    _lesson_block_cache[lesson.id] = block
    return block


async def stream_reply(lesson: Lesson, history: list[dict], user_message: str) -> AsyncIterator[str]:
    extra_block = await _lesson_extra_block(lesson)

    messages = [
        {"role": "system", "content": system_prompt(lesson, extra_block)}
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    async for chunk in await _client.chat(
        model=MODEL,
        messages=messages,
        stream=True,
        options={"num_ctx": NUM_CTX, "num_thread": NUM_THREAD, "num_predict": NUM_PREDICT},
        keep_alive=KEEP_ALIVE,
    ):
        content = chunk.get("message", {}).get("content", "")
        if content:
            yield content


async def warm_up() -> None:
    """Load the chat model into memory without generating anything, so the
    first real message doesn't pay the ~1-2 min cold-load on this machine."""
    try:
        await _client.chat(model=MODEL, messages=[], keep_alive=KEEP_ALIVE)
    except Exception:
        pass  # check_model_available() surfaces real problems to the user


async def prewarm(lesson: Lesson, history: list[dict]) -> None:
    """Prefill llama.cpp's prompt cache with this lesson's system prompt and
    history (a 1-token request) so the user's next real message extends an
    already-processed prefix instead of paying the full prefill (measured up
    to ~80s on this CPU for source-heavy lessons)."""
    extra_block = await _lesson_extra_block(lesson)
    messages = [
        {"role": "system", "content": system_prompt(lesson, extra_block)}
    ]
    messages.extend(history)
    try:
        await _client.chat(
            model=MODEL,
            messages=messages,
            options={"num_ctx": NUM_CTX, "num_thread": NUM_THREAD, "num_predict": 1},
            keep_alive=KEEP_ALIVE,
        )
    except Exception:
        pass  # best-effort: the real message will just pay the prefill


async def check_model_available() -> bool:
    try:
        models = await _client.list()
        names = [m.get("model", m.get("name", "")) for m in models.get("models", [])]
        return any(MODEL in n for n in names)
    except Exception:
        return False
