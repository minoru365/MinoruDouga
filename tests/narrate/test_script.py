from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from minoru_studio.narrate import script as script_module
from minoru_studio.narrate.models import Utterance
from minoru_studio.narrate.script import parse_script, snapshot_script, snapshot_valid


def write_script(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def texts(path: Path) -> tuple[str, ...]:
    result = parse_script(path)
    assert isinstance(result, tuple)
    assert result == tuple(Utterance(index + 1, item.text) for index, item in enumerate(result))
    return tuple(item.text for item in result)


def test_txt_trims_lines_removes_blanks_and_splits_at_terminators(tmp_path: Path):
    source = write_script(
        tmp_path, "script.txt", "  最初です。 次です！\n\n  Last.  Another?  \n"
    )

    assert texts(source) == ("最初です。", "次です！", "Last.", "Another?")


def test_txt_joins_trimmed_nonblank_lines_before_sentence_segmentation(tmp_path: Path):
    source = write_script(tmp_path, "script.txt", "  first  \n\n second。  \n")

    assert texts(source) == ("first\nsecond。",)


def test_txt_prefers_whitespace_boundary_at_maximum_length(tmp_path: Path):
    source = write_script(tmp_path, "script.txt", f"{'a' * 55} {'b' * 10}")

    assert texts(source) == ("a" * 55, "b" * 10)


def test_txt_hard_splits_a_61_character_token(tmp_path: Path):
    source = write_script(tmp_path, "script.txt", "x" * 61)

    assert texts(source) == ("x" * 60, "x")


def test_txt_retains_punctuation_and_counts_emoji_as_unicode_code_points(tmp_path: Path):
    source = write_script(tmp_path, "script.txt", "🙂" * 61 + "。")

    assert texts(source) == ("🙂" * 60, "🙂。")


def test_markdown_reads_only_narration_sections_in_document_order(tmp_path: Path):
    source = write_script(
        tmp_path,
        "script.md",
        "# Intro\nignore\n## ナレーション ###\n第一。\n### 詳細\n第二。\n"
        "## 操作\nignore\n# ナレーション\n第三。\n# End\nignore\n",
    )

    assert texts(source) == ("第一。", "第二。", "第三。")


def test_markdown_excludes_non_narration_content_lines(tmp_path: Path):
    source = write_script(
        tmp_path,
        "script.md",
        "# ナレーション\n話す文。\n画面の説明：見出し\n操作: クリック\n![](image.png)\n"
        "[](audio.wav)\n[label](https://example.test)\n続き。\n",
    )

    assert texts(source) == ("話す文。", "続き。")


@pytest.mark.parametrize(
    ("name", "content"), [("script.txt", " \n\t"), ("script.md", "# Other\ntext\n")]
)
def test_parse_script_rejects_missing_narration(tmp_path: Path, name: str, content: str):
    with pytest.raises(ValueError, match="narration"):
        parse_script(write_script(tmp_path, name, content))


def test_parse_script_rejects_wrong_suffix_and_invalid_utf8(tmp_path: Path):
    wrong_suffix = write_script(tmp_path, "script.rtf", "Narration")
    invalid = tmp_path / "script.txt"
    invalid.write_bytes(b"\xff")

    with pytest.raises(ValueError, match=r"\.txt or \.md"):
        parse_script(wrong_suffix)
    with pytest.raises(UnicodeDecodeError):
        parse_script(invalid)


def test_snapshot_is_byte_identical_and_does_not_change_source(tmp_path: Path):
    source = tmp_path / "untrusted-name.MD"
    source_bytes = "# ナレーション\n本文。\n".encode()
    source.write_bytes(source_bytes)
    inputs = tmp_path / "job" / "inputs"
    inputs.mkdir(parents=True)

    snapshot = snapshot_script(source, inputs)

    assert snapshot == inputs / "script.md"
    assert snapshot.read_bytes() == source_bytes
    assert source.read_bytes() == source_bytes


def test_snapshot_rejects_invalid_utf8_and_publishes_nothing(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"\xff")
    inputs = tmp_path / "job" / "inputs"
    inputs.mkdir(parents=True)

    with pytest.raises(UnicodeDecodeError):
        snapshot_script(source, inputs)

    assert not list(inputs.iterdir())


def test_snapshot_uses_create_only_publish_and_refuses_collision(tmp_path: Path):
    source = write_script(tmp_path, "source.txt", "content")
    inputs = tmp_path / "job" / "inputs"
    inputs.mkdir(parents=True)
    target = inputs / "script.txt"
    target.write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError):
        snapshot_script(source, inputs)

    assert target.read_text(encoding="utf-8") == "existing"
    assert not list(inputs.glob(".script-*.tmp"))


def test_snapshot_rejects_a_staged_copy_with_a_different_source_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = write_script(tmp_path, "source.txt", "content")
    inputs = tmp_path / "job" / "inputs"
    inputs.mkdir(parents=True)
    original_fingerprint = script_module.fingerprint_file

    def mismatched_stage(path: Path):
        fingerprint = original_fingerprint(path)
        if Path(path).suffix == ".tmp":
            return SimpleNamespace(sha256="0" * 64)
        return fingerprint

    monkeypatch.setattr(script_module, "fingerprint_file", mismatched_stage)

    with pytest.raises(ValueError, match="hash"):
        snapshot_script(source, inputs)

    assert not list(inputs.iterdir())


def test_snapshot_valid_uses_sha256_and_rejects_mismatch(tmp_path: Path):
    snapshot = write_script(tmp_path, "script.txt", "content")
    expected = hashlib.sha256(snapshot.read_bytes()).hexdigest()

    assert snapshot_valid(snapshot, expected) is True
    assert snapshot_valid(snapshot, "0" * 64) is False
    assert snapshot_valid(tmp_path / "missing.txt", expected) is False


def test_snapshot_valid_uses_safe_digest_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    snapshot = write_script(tmp_path, "script.txt", "content")
    expected = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    monkeypatch.setattr(
        script_module,
        "hmac",
        SimpleNamespace(compare_digest=lambda actual, wanted: False),
        raising=False,
    )

    assert snapshot_valid(snapshot, expected) is False
