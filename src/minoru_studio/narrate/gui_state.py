from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from minoru_studio.narrate.models import NarrateRequest


@dataclass(frozen=True, slots=True)
class NarrateFormValues:
    input_path: str
    script_path: str
    name: str
    output_dir: str
    preview: bool

    def to_request(self) -> NarrateRequest:
        if type(self.preview) is not bool:
            raise ValueError("preview must be a bool")
        return NarrateRequest(
            input_path=Path(_required(self.input_path, "input")),
            script_path=Path(_required(self.script_path, "script")),
            name=_required(self.name, "name"),
            output_dir=Path(_required(self.output_dir, "output directory")),
            preview=self.preview,
        )


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")
    return value.strip()
