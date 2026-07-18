from minoru_studio.beat_sync.media import (
    MaterialKind,
    discover_materials,
    order_materials,
    validate_music_file,
)


def test_discovery_is_non_recursive_and_case_insensitive(tmp_path):
    (tmp_path / "B.MOV").write_bytes(b"video")
    (tmp_path / "a.jpg").write_bytes(b"photo")
    (tmp_path / "ignore.txt").write_text("x")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "hidden.png").write_bytes(b"photo")
    found = discover_materials(tmp_path)
    assert [(item.path.name, item.kind) for item in found] == [
        ("a.jpg", MaterialKind.PHOTO),
        ("B.MOV", MaterialKind.VIDEO),
    ]


def test_random_order_is_persistable_by_injection(tmp_path):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (tmp_path / name).write_bytes(b"x")
    ordered = order_materials(
        discover_materials(tmp_path),
        "random",
        shuffler=lambda values: values.reverse(),
    )
    assert [item.path.name for item in ordered] == ["c.jpg", "b.jpg", "a.jpg"]


def test_music_extension_is_validated(tmp_path):
    song = tmp_path / "song.MP3"
    song.write_bytes(b"audio")
    assert validate_music_file(song) == song.resolve()
