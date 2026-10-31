from datetime import date, timedelta

from app import progress


def test_completion_roundtrip():
    assert not progress.is_complete("py-core")
    progress.mark_complete("py-core")
    assert progress.is_complete("py-core")
    progress.mark_incomplete("py-core")
    assert not progress.is_complete("py-core")


def test_history_roundtrip():
    progress.append_history("l1", "user", "hi")
    progress.append_history("l1", "assistant", "hello")
    assert [t["role"] for t in progress.get_history("l1")] == ["user", "assistant"]
    progress.clear_history("l1")
    assert progress.get_history("l1") == []


def test_time_and_streaks():
    progress.add_time("l1", 30)
    progress.add_time("l2", 45)
    assert progress.get_time_spent("l1") == 30
    assert progress.total_time_spent() == 75
    assert progress.current_streak() == 1
    assert progress.longest_streak() == 1


def test_zero_time_is_ignored():
    progress.add_time("l1", 0)
    progress.add_time("l1", -5)
    assert progress.total_time_spent() == 0
    assert progress.current_streak() == 0


def test_streak_broken_by_gap():
    state = progress._load()
    today = date.today()
    state["activity_dates"] = [
        (today - timedelta(days=5)).isoformat(),
        (today - timedelta(days=4)).isoformat(),
        (today - timedelta(days=1)).isoformat(),
        today.isoformat(),
    ]
    progress._save(state)
    assert progress.current_streak() == 2
    assert progress.longest_streak() == 2


def test_review_due_lifecycle():
    progress.mark_complete("l1")
    assert not progress.is_review_due("l1")  # completed today

    # backdate completion past the review interval
    state = progress._load()
    old = (date.today() - timedelta(days=progress.REVIEW_INTERVAL_DAYS)).isoformat()
    state["completed_at"]["l1"] = old
    progress._save(state)
    assert progress.is_review_due("l1")

    progress.mark_reviewed("l1")
    assert not progress.is_review_due("l1")


def test_review_never_due_for_incomplete_or_undated():
    assert not progress.is_review_due("never-touched")
    # legacy entry: completed before dates were recorded
    state = progress._load()
    state["completed"].append("legacy")
    progress._save(state)
    assert not progress.is_review_due("legacy")


def test_code_persistence():
    assert progress.get_code("l1") == ""
    progress.save_code("l1", "x = 1\n")
    assert progress.get_code("l1") == "x = 1\n"
    progress.save_code("l1", "   \n")  # whitespace-only clears the entry
    assert progress.get_code("l1") == ""


def test_teaching_skill_persists_per_lesson():
    from app import progress

    assert progress.get_teaching_skill("l1") is None
    progress.save_teaching_skill("l1", "socratic-tutor")
    assert progress.get_teaching_skill("l1") == "socratic-tutor"
    assert progress.get_teaching_skill("l2") is None
    progress.save_teaching_skill("l1", None)
    assert progress.get_teaching_skill("l1") is None


def test_quiz_scores_recorded_and_aggregated():
    from app import progress

    assert progress.last_quiz_score("l1") is None
    assert progress.quiz_stats() is None
    progress.record_quiz_score("l1", 3, 5)
    progress.record_quiz_score("l1", 5, 5)
    progress.record_quiz_score("l2", 2, 5)
    assert progress.last_quiz_score("l1") == {
        "date": __import__("datetime").date.today().isoformat(),
        "correct": 5,
        "total": 5,
    }
    count, avg = progress.quiz_stats()
    assert count == 3
    assert abs(avg - (0.6 + 1.0 + 0.4) / 3) < 1e-9
    # nonsense scores are ignored
    progress.record_quiz_score("l1", 9, 5)
    progress.record_quiz_score("l1", -1, 5)
    progress.record_quiz_score("l1", 1, 0)
    assert progress.quiz_stats()[0] == 3


def test_weak_quiz_score_brings_review_forward():
    from datetime import date, timedelta

    from app import progress

    progress.mark_complete("l1")
    # completed 4 days ago: inside the normal 7-day interval
    state = progress._load()
    state["completed_at"]["l1"] = (date.today() - timedelta(days=4)).isoformat()
    progress._save(state)
    assert not progress.is_review_due("l1")
    # a weak quiz (< 60%) shortens the interval to 3 days -> now due
    progress.record_quiz_score("l1", 2, 5)
    assert progress.is_review_due("l1")
    # a strong retake restores the normal interval
    progress.record_quiz_score("l1", 5, 5)
    assert not progress.is_review_due("l1")


def test_last_lesson_persists():
    from app import progress

    assert progress.get_last_lesson() is None
    progress.save_last_lesson("ec-garch")
    assert progress.get_last_lesson() == "ec-garch"
