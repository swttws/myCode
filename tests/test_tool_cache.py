from concurrent.futures import ThreadPoolExecutor

from mycode.tool.cache import FileTextCache


def test_file_text_cache_reads_from_cache_when_file_is_unchanged(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    cache = FileTextCache()

    assert cache.read_text(path) == "hello"
    assert cache.read_text(path) == "hello"


def test_file_text_cache_write_updates_disk_and_cache(tmp_path):
    path = tmp_path / "nested" / "notes.txt"
    cache = FileTextCache()

    cache.write_text(path, "fresh")

    assert path.read_text(encoding="utf-8") == "fresh"
    assert cache.read_text(path) == "fresh"


def test_file_text_cache_reloads_when_file_metadata_changes(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("old", encoding="utf-8")
    cache = FileTextCache()
    assert cache.read_text(path) == "old"

    path.write_text("new content", encoding="utf-8")

    assert cache.read_text(path) == "new content"


def test_file_text_cache_edit_replaces_unique_text_and_updates_cache(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello world", encoding="utf-8")
    cache = FileTextCache()

    match_count, updated_text = cache.edit_text(path, "world", "tool")

    assert match_count == 1
    assert updated_text == "hello tool"
    assert path.read_text(encoding="utf-8") == "hello tool"
    assert cache.read_text(path) == "hello tool"


def test_file_text_cache_edit_does_not_write_on_zero_or_multiple_matches(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("one two one", encoding="utf-8")
    cache = FileTextCache()
    assert cache.read_text(path) == "one two one"

    assert cache.edit_text(path, "missing", "x") == (0, None)
    assert path.read_text(encoding="utf-8") == "one two one"
    assert cache.read_text(path) == "one two one"

    assert cache.edit_text(path, "one", "x") == (2, None)
    assert path.read_text(encoding="utf-8") == "one two one"
    assert cache.read_text(path) == "one two one"


def test_file_text_cache_keeps_disk_and_cache_consistent_under_concurrent_access(tmp_path):
    path = tmp_path / "notes.txt"
    cache = FileTextCache()

    def write_and_read(index: int):
        text = f"value-{index}"
        cache.write_text(path, text)
        return cache.read_text(path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write_and_read, range(30)))

    assert cache.read_text(path) == path.read_text(encoding="utf-8")
