import pytest

from app import curriculum, rag


def test_modules_load():
    mods = curriculum.load_modules()
    assert len(mods) >= 10
    assert sum(len(m.lessons) for m in mods) >= 65


def test_lesson_ids_unique():
    ids = [l.id for l in curriculum.all_lessons()]
    assert len(ids) == len(set(ids))


def test_find_and_next():
    first = curriculum.all_lessons()[0]
    assert curriculum.find_lesson(first.id) == first
    assert curriculum.next_lesson(first.id) == curriculum.all_lessons()[1]
    last = curriculum.all_lessons()[-1]
    assert curriculum.next_lesson(last.id) is None
    assert curriculum.find_lesson("no-such-lesson") is None


@pytest.mark.skipif(not rag.CORPUS_DIR.exists(), reason="corpus not cloned")
def test_all_sources_resolve():
    missing = [
        (l.id, s.repo, s.file)
        for l in curriculum.all_lessons()
        for s in l.sources
        if rag.find_source_file(s.repo, s.file) is None
    ]
    assert not missing, f"syllabus references nonexistent corpus files: {missing}"
