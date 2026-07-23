from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "path",
    ("README.md", "docs/agent-handoff.md", "docs/media-automation-design.md"),
)
def test_current_product_docs_do_not_announce_retired_repo_demo(path):
    text = Path(path).read_text(encoding="utf-8")
    assert "repo-demo" not in text
    assert "デモ組み立て" not in text
