"""Tests for the system-prompt assembly and teaching skills in app.agent."""
from app import agent, skills
from app.curriculum import Lesson


def _lesson(module_id: str, module_title: str) -> Lesson:
    return Lesson(
        id=f"{module_id}-x",
        title="Some lesson",
        module_id=module_id,
        module_title=module_title,
    )


def test_shipped_skills_load():
    names = {s.name for s in skills.available()}
    assert {
        "python-playground-coach",
        "socratic-tutor",
        "concept-explainer",
        "exam-blueprint",
        "flashcard-deck",
        "quant-project-mentor",
    } <= names
    coach = skills.load("python-playground-coach")
    assert coach is not None
    assert "Teaching loop" in coach.body
    assert not coach.body.startswith("---")  # frontmatter stripped


def test_missing_skill_returns_none():
    assert skills.load("no-such-skill") is None


def test_python_foundations_gets_playground_skill():
    prompt = agent.system_prompt(_lesson("python-foundations", "Python Foundations"))
    assert 'Teaching skill "python-playground-coach" is active' in prompt


def test_other_modules_do_not_get_playground_skill():
    prompt = agent.system_prompt(_lesson("econometrics", "Time Series & Econometrics"))
    assert "python-playground-coach" not in prompt


def test_project_modules_get_project_mentor_skill():
    for module_id in ("moquant-projects", "capstone"):
        prompt = agent.system_prompt(_lesson(module_id, "Applied Projects"))
        assert 'Teaching skill "quant-project-mentor" is active' in prompt
        assert "Stage" in prompt


def test_mode_skill_is_injected():
    prompt = agent.system_prompt(
        _lesson("econometrics", "Time Series & Econometrics"),
        skill_name="socratic-tutor",
    )
    assert 'Teaching skill "socratic-tutor" is active' in prompt
    assert "Lead the learner to the answer with questions" in prompt


def test_module_and_mode_skills_stack_without_duplicates():
    lesson = _lesson("python-foundations", "Python Foundations")
    prompt = agent.system_prompt(lesson, skill_name="socratic-tutor")
    assert 'Teaching skill "python-playground-coach" is active' in prompt
    assert 'Teaching skill "socratic-tutor" is active' in prompt
    # Selecting the module's own skill as the mode must not inject it twice.
    doubled = agent.system_prompt(lesson, skill_name="python-playground-coach")
    assert doubled.count('Teaching skill "python-playground-coach"') == 1


def test_prompt_is_deterministic():
    lesson = _lesson("python-foundations", "Python Foundations")
    assert agent.system_prompt(lesson, "EXTRA", "socratic-tutor") == agent.system_prompt(
        lesson, "EXTRA", "socratic-tutor"
    )


def test_extra_block_is_appended_last():
    lesson = _lesson("python-foundations", "Python Foundations")
    prompt = agent.system_prompt(lesson, "EXTRA-BLOCK")
    assert prompt.endswith("EXTRA-BLOCK")


def _turns(n: int, chars: int = 1000) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"t{i}:" + "x" * chars}
        for i in range(n)
    ]


def test_fit_history_untouched_under_budget():
    history = _turns(6)
    assert agent._fit_history(history) == history


def test_fit_history_trims_to_low_watermark_with_note():
    history = _turns(24)
    fitted = agent._fit_history(history)
    assert fitted[0] == agent.TRIM_NOTE
    assert fitted[1]["role"] == "user"
    kept = sum(len(t["content"]) for t in fitted[1:])
    assert kept <= agent.HISTORY_HIGH_CHARS
    # the newest turns are always kept
    assert fitted[-1] == history[-1]


def test_fit_history_cut_is_stable_as_history_grows():
    """The kept window must stay byte-identical across a few new turns
    (prefix-cache requirement) -- the cut only advances in chunks."""
    history = _turns(24)
    before = agent._fit_history(history)
    grown = history + _turns(2, chars=200)
    after = agent._fit_history(grown)
    assert after[: len(before)] == before
