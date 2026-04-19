from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class ScenarioError(ValueError):
    """Raised when a scenario directory is malformed."""


class _Check(BaseModel):
    script: str
    expect_exit_code: int = 0
    timeout_seconds: int = Field(default=30, gt=0)


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
    check_timeout_seconds: int

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

        # Validate check.script is a safe relative path (no absolute, no traversal).
        script_path = Path(parsed.check.script)
        if script_path.is_absolute():
            raise ScenarioError(
                f"check.script must be a relative path, got absolute: {parsed.check.script!r}"
            )
        if ".." in script_path.parts:
            raise ScenarioError(
                f"check.script must not contain '..' segments: {parsed.check.script!r}"
            )

        check_script = directory / parsed.check.script
        # Defence in depth: ensure the resolved path stays inside the scenario dir.
        resolved_dir = directory.resolve()
        resolved_script = check_script.resolve()
        try:
            resolved_script.relative_to(resolved_dir)
        except ValueError as e:
            raise ScenarioError(
                f"check.script resolves outside scenario directory: {parsed.check.script!r}"
            ) from e
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
            check_timeout_seconds=parsed.check.timeout_seconds,
        )
