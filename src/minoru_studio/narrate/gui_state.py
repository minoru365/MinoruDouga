from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from minoru_studio.narrate.models import NarrateRequest, StoryboardRequest


@dataclass(frozen=True, slots=True)
class NarrateFormValues:
    input_path: str
    script_path: str
    name: str
    output_dir: str
    preview: bool

    def to_request(self) -> NarrateRequest | StoryboardRequest:
        if type(self.preview) is not bool:
            raise ValueError("preview must be a bool")
        source = Path(_required(self.input_path, "input"))
        if source.suffix.casefold() == ".json":
            if not isinstance(self.script_path, str) or self.script_path.strip():
                raise ValueError("storyboard JSON includes narration; leave script blank")
            return StoryboardRequest(
                input_path=source, name=_required(self.name, "name"),
                output_dir=Path(_required(self.output_dir, "output directory")),
                preview=self.preview,
            )
        return NarrateRequest(
            input_path=source,
            script_path=Path(_required(self.script_path, "script")),
            name=_required(self.name, "name"),
            output_dir=Path(_required(self.output_dir, "output directory")),
            preview=self.preview,
        )


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")
    return value.strip()
