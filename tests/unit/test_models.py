from memocore.domain.models import Note, NoteStatus


def test_model_defaults():
    note = Note(raw_text="hello")

    assert note.id
    assert note.status == NoteStatus.CAPTURED
    assert note.created_at.tzinfo is not None
    assert note.updated_at.tzinfo is not None
