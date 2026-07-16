"""Wraps a local Ollama model as an interactive tutor."""
from __future__ import annotations

from collections.abc import AsyncIterator

from ollama import AsyncClient

from app import rag, skills
from app.curriculum import Lesson

# The launch-time picker (app/main.py ModelPicker) chooses from this catalog;
# descriptions are shown to the student. Speeds measured on this CPU-only
# machine. Default is the general instruct model rather than a Coder variant:
# this tutor mostly explains concepts (econometrics, portfolio theory, etc.)
# and only secondarily reviews code. The exact qwen3 tag matters:
# qwen3:4b-instruct is the non-thinking 2507 build; plain qwen3:4b would burn
# NUM_PREDICT on hidden reasoning tokens.
MODEL_CATALOG = [
    (
        "qwen3:4b-instruct",
        "2.5 GB · Best all-rounder (default). Strongest explanations and "
        "reasoning for its speed (~7 tok/s); matches the old 7B on most "
        "benchmarks.",
    ),
    (
        "qwen2.5-coder:3b-instruct-q8_0",
        "3.3 GB · Code specialist at Q8 precision (~5.5 tok/s). Sharp for "
        "exercises, code review and debugging; weaker on theory/econometrics.",
    ),
    (
        "phi4-mini",
        "2.5 GB · Microsoft Phi-4-mini (3.8B, ~7 tok/s). Strong at math, "
        "logic and step-by-step derivations; terser conversational style.",
    ),
    (
        "qwen2.5:3b-instruct",
        "1.9 GB · Smallest and fastest (~8 tok/s). Fine for quick drills; "
        "noisier on multi-step math and theory.",
    ),
    (
        "qwen2.5-coder:7b",
        "4.7 GB · Big code specialist, slowest (~4 tok/s). Deepest code "
        "review; weakest tradeoff for concept teaching.",
    ),
]

DEFAULT_MODEL = MODEL_CATALOG[0][0]

# The active model. Set once at launch by the picker (set_model); requests
# read it at call time.
MODEL = DEFAULT_MODEL


def set_model(tag: str) -> None:
    global MODEL
    MODEL = tag

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

# Wraps a teaching-skill playbook (an EduHub/Agent-Skills SKILL.md body, see
# app/skills.py) when one is active for the lesson.
SKILL_TEMPLATE = """

Teaching skill "{name}" is active for this lesson -- follow its playbook in \
every reply, adapted to the current lesson's subject matter:

{body}
"""

# Skills applied automatically to every lesson of a module. Keyed on module
# id so the system prompt stays deterministic per lesson (prefix-cache
# requirement). python-foundations: predict-then-run playground loops beat
# lecturing for absolute basics. The two applied-project modules get the
# staged project walkthrough that teaches the quant package stack.
MODULE_SKILLS = {
    "python-foundations": "python-playground-coach",
    "moquant-projects": "quant-project-mentor",
    "capstone": "quant-project-mentor",
}

# Skills the student can switch on per lesson as a "tutor style" (persisted
# in progress.json and appended to the system prompt).
MODE_SKILLS = ("socratic-tutor", "concept-explainer")

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


def system_prompt(lesson: Lesson, extra_block: str = "", skill_name: str | None = None) -> str:
    prompt = SYSTEM_TEMPLATE.format(
        module_title=lesson.module_title, lesson_title=lesson.title
    )
    active = [MODULE_SKILLS.get(lesson.module_id), skill_name]
    seen: set[str] = set()
    for name in active:
        if not name or name in seen:
            continue
        seen.add(name)
        skill = skills.load(name)
        if skill:
            prompt += SKILL_TEMPLATE.format(name=skill.name, body=skill.body)
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


# History budget: keep the whole prompt (system ~600 tok + sources ~2000 tok
# + history + reply headroom) inside NUM_CTX, since overflow makes Ollama
# silently drop prompt from the front -- losing the lesson material AND
# breaking the prefix cache every turn. ~3.5 chars/token, so 12000 chars is
# ~3400 tokens of history. Trimming uses two watermarks so it happens in
# chunks: when history passes HIGH it is cut back to LOW, then the kept
# window stays byte-stable (prefix-cacheable) until it regrows past HIGH.
# Each trim costs one re-prefill; a naive "keep the last N that fit" would
# slide the window and re-prefill every single turn.
HISTORY_HIGH_CHARS = 12000
HISTORY_LOW_CHARS = 7000

TRIM_NOTE = {
    "role": "assistant",
    "content": "[Earlier turns were trimmed to fit the context window. "
    "The lesson has progressed past the opening sections; continue from "
    "the conversation below, not from the start.]",
}


def _fit_history(history: list[dict]) -> list[dict]:
    """Return the suffix of history that fits the budget, replaying growth
    chronologically so the cut point is deterministic and only advances in
    chunks (identical result whether computed incrementally or from scratch)."""
    cut = 0
    size = 0
    for i, turn in enumerate(history):
        size += len(turn["content"])
        if size > HISTORY_HIGH_CHARS:
            while size > HISTORY_LOW_CHARS and cut < i:
                size -= len(history[cut]["content"])
                cut += 1
    # Start the kept window on a user turn so the transcript reads naturally.
    while cut < len(history) and history[cut]["role"] != "user":
        cut += 1
    if cut == 0:
        return history
    return [TRIM_NOTE] + history[cut:]


async def stream_reply(
    lesson: Lesson, history: list[dict], user_message: str, skill_name: str | None = None
) -> AsyncIterator[str]:
    extra_block = await _lesson_extra_block(lesson)

    messages = [
        {"role": "system", "content": system_prompt(lesson, extra_block, skill_name)}
    ]
    messages.extend(_fit_history(history))
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


async def prewarm(lesson: Lesson, history: list[dict], skill_name: str | None = None) -> None:
    """Prefill llama.cpp's prompt cache with this lesson's system prompt and
    history (a 1-token request) so the user's next real message extends an
    already-processed prefix instead of paying the full prefill (measured up
    to ~80s on this CPU for source-heavy lessons)."""
    extra_block = await _lesson_extra_block(lesson)
    messages = [
        {"role": "system", "content": system_prompt(lesson, extra_block, skill_name)}
    ]
    messages.extend(_fit_history(history))
    try:
        await _client.chat(
            model=MODEL,
            messages=messages,
            options={"num_ctx": NUM_CTX, "num_thread": NUM_THREAD, "num_predict": 1},
            keep_alive=KEEP_ALIVE,
        )
    except Exception:
        pass  # best-effort: the real message will just pay the prefill


async def installed_models() -> set[str]:
    """Model tags present on the local Ollama; empty if it's unreachable."""
    try:
        models = await _client.list()
        return {m.get("model", m.get("name", "")) for m in models.get("models", [])}
    except Exception:
        return set()


def is_installed(tag: str, installed: set[str]) -> bool:
    # Ollama lists fully-qualified tags ("phi4-mini:latest"), so match on
    # prefix rather than equality.
    return any(name == tag or name.startswith(tag + ":") for name in installed)


async def check_model_available() -> bool:
    return is_installed(MODEL, await installed_models())
