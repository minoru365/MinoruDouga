"""User-owned output locations, independent of a source checkout."""

from pathlib import Path


def default_output_dir() -> Path:
    return Path.home() / "Videos" / "MinoruStudio"
