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
