from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from minoru_studio.script_draft.models import ScriptDraftRequest


@dataclass(frozen=True, slots=True)
class ScriptDraftFormValues:
    input_path: str
    name: str
    output_dir: str

    def to_request(self) -> ScriptDraftRequest:
        return ScriptDraftRequest(
            input_path=Path(_required(self.input_path, "input")),
            name=_required(self.name, "name"),
            output_dir=Path(_required(self.output_dir, "output directory")),
        )


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")
    return value.strip()
