from __future__ import annotations

from pathlib import Path

import pytest

from minoru_studio.transcribe.models import (
    MODEL_SPECS,
    REQUIRED_MODEL_FILES,
    ModelCapacityError,
    default_model_cache_dir,
    model_directory,
    model_is_complete,
    require_model_capacity,
)


def test_default_cache_uses_local_app_data(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    assert default_model_cache_dir() == tmp_path / "local" / "MinoruStudio" / "models" / "faster-whisper"


def test_model_specs_have_the_approved_estimates():
    assert MODEL_SPECS["small"].estimated_download_bytes == 500_000_000
    assert MODEL_SPECS["small"].required_free_bytes == 1_000_000_000
    assert MODEL_SPECS["medium"].estimated_download_bytes == 1_500_000_000
    assert MODEL_SPECS["medium"].required_free_bytes == 3_000_000_000


def test_only_final_directory_with_all_required_files_is_complete(tmp_path: Path):
    cache = tmp_path / "models"
    final = model_directory(cache, "small")
    final.mkdir(parents=True)
    for name in REQUIRED_MODEL_FILES[:-1]:
        (final / name).write_text("x", encoding="utf-8")
    partial = cache / ".small.partial-test"
    partial.mkdir()
    for name in REQUIRED_MODEL_FILES:
        (partial / name).write_text("x", encoding="utf-8")

    assert not model_is_complete(cache, "small")
    (final / REQUIRED_MODEL_FILES[-1]).write_text("x", encoding="utf-8")
    assert model_is_complete(cache, "small")


def test_require_model_capacity_rejects_insufficient_space(monkeypatch, tmp_path: Path):
    import minoru_studio.transcribe.models as models

    class Usage:
        free = 999_999_999

    monkeypatch.setattr(models.shutil, "disk_usage", lambda path: Usage())

    with pytest.raises(ModelCapacityError, match="insufficient"):
        require_model_capacity(tmp_path, "small")
