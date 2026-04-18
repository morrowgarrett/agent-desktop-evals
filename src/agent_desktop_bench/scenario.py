from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class ScenarioError(ValueError):
    """Raised when a scenario directory is malformed."""


class _Check(BaseModel):
    script: str
    expect_exit_code: int = 0


class _ScenarioToml(BaseModel):
    id: str
    title: str
    target_app: str
    timeout_seconds: int = Field(gt=0)
    check: _Check


class Scenario(BaseModel):
    id: str
    title: str
    target_app: str
    timeout_seconds: int
    prompt: str
    check_script: Path
    expect_exit_code: int

    @classmethod
    def load(cls, directory: Path) -> Scenario:
        directory = Path(directory)
        toml_path = directory / "scenario.toml"
        if not toml_path.exists():
            raise ScenarioError(f"missing scenario.toml in {directory}")

        prompt_path = directory / "prompt.md"
        if not prompt_path.exists():
            raise ScenarioError(f"missing prompt.md in {directory}")

        with toml_path.open("rb") as f:
            raw = tomllib.load(f)

        try:
            parsed = _ScenarioToml.model_validate(raw)
        except Exception as e:
            raise ScenarioError(f"invalid scenario.toml: {e}") from e

        if parsed.id != directory.name:
            raise ScenarioError(
                f"scenario.toml id {parsed.id!r} must match directory name {directory.name!r}"
            )

        check_script = directory / parsed.check.script
        if not check_script.exists():
            raise ScenarioError(f"missing check script: {check_script}")

        return cls(
            id=parsed.id,
            title=parsed.title,
            target_app=parsed.target_app,
            timeout_seconds=parsed.timeout_seconds,
            prompt=prompt_path.read_text(encoding="utf-8"),
            check_script=check_script,
            expect_exit_code=parsed.check.expect_exit_code,
        )
