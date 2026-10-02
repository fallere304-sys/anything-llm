import pytest

from buddy.storage.db import Database, NotFound


def test_crud_and_cascade():
    db = Database(":memory:")
    c = db.create_conversation("t")
    db.add_message(c["id"], "user", "hi")
    db.add_message(c["id"], "assistant", "yo", provider="mock", model="m")
    assert [m["role"] for m in db.list_messages(c["id"])] == ["user", "assistant"]
    db.rename_conversation(c["id"], "new")
    assert db.get_conversation(c["id"])["title"] == "new"
    db.delete_conversation(c["id"])
    with pytest.raises(NotFound):
        db.list_messages(c["id"])


def test_persists_on_disk(tmp_path):
    p = tmp_path / "sub" / "b.sqlite3"
    db = Database(p)
    cid = db.create_conversation("t")["id"]
    db.add_message(cid, "user", "kept")
    db.close()
    assert Database(p).list_messages(cid)[0]["content"] == "kept"
