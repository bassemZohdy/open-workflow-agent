from __future__ import annotations

from pathlib import Path

from open_workflow_agent.memory import MemoryService


def test_in_process_add_search_delete_roundtrip() -> None:
    memory = MemoryService()
    first = memory.add("user prefers dark mode")
    second = memory.add("deployment runs in Frankfurt", {"source": "chat"})
    assert first != second

    results = memory.search("dark mode")
    assert len(results) == 1
    assert results[0]["text"] == "user prefers dark mode"
    assert results[0]["id"] == first

    assert memory.search("Frankfurt")[0]["metadata"] == {"source": "chat"}

    assert memory.delete(first) is True
    assert memory.delete(first) is False
    assert memory.search("dark mode") == []
    memory.close()


def test_search_ranks_by_term_overlap() -> None:
    memory = MemoryService()
    memory.add("release checklist draft review")
    memory.add("release")
    memory.add("unrelated note entirely")
    results = memory.search("release checklist")
    assert results[0]["text"] == "release checklist draft review"
    assert results[1]["text"] == "release"
    assert all("unrelated" not in row["text"] for row in results)
    memory.close()


def test_search_limit_and_empty_query() -> None:
    memory = MemoryService()
    for index in range(5):
        memory.add(f"shared topic number {index}")
    results = memory.search("shared topic", limit=2)
    assert len(results) == 2
    # A whitespace-only query is a bounded listing: every entry matches and the
    # limit still applies.
    listing = memory.search("   ", limit=3)
    assert len(listing) == 3
    memory.close()


def test_sqlite_backed_store_persists(tmp_path: Path) -> None:
    database = str(tmp_path / "memory.sqlite3")
    memory = MemoryService(database)
    identifier = memory.add("customer invoice 42 is overdue", {"ticket": "42"})
    memory.close()

    reopened = MemoryService(database)
    rows = reopened.search("invoice overdue")
    assert len(rows) == 1
    assert rows[0]["id"] == identifier
    assert rows[0]["metadata"] == {"ticket": "42"}
    assert reopened.delete(identifier) is True
    reopened.close()

    final = MemoryService(database)
    assert final.search("invoice overdue") == []
    final.close()


def test_metadata_defaults_to_empty_dict(tmp_path: Path) -> None:
    memory = MemoryService(str(tmp_path / "memory.sqlite3"))
    memory.add("plain note without metadata")
    rows = memory.search("plain note")
    assert rows[0]["metadata"] == {}
    memory.close()
