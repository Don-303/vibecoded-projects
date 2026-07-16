"""Tests for the system-prompt assembly in app.agent."""
from app import agent
from app.curriculum import Lesson


def _lesson(module_id: str, module_title: str) -> Lesson:
    return Lesson(
        id=f"{module_id}-x",
        title="Some lesson",
        module_id=module_id,
        module_title=module_title,
    )


def test_python_foundations_gets_playground_block():
    prompt = agent.system_prompt(_lesson("python-foundations", "Python Foundations"))
    assert "playground-style teaching loop" in prompt
    assert "PREDICT" in prompt


def test_other_modules_do_not_get_playground_block():
    prompt = agent.system_prompt(_lesson("econometrics", "Time Series & Econometrics"))
    assert "playground-style teaching loop" not in prompt


def test_prompt_is_deterministic():
    lesson = _lesson("python-foundations", "Python Foundations")
    assert agent.system_prompt(lesson, "EXTRA") == agent.system_prompt(lesson, "EXTRA")


def test_extra_block_is_appended_last():
    lesson = _lesson("python-foundations", "Python Foundations")
    prompt = agent.system_prompt(lesson, "EXTRA-BLOCK")
    assert prompt.endswith("EXTRA-BLOCK")
