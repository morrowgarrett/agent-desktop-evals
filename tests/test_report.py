from agent_desktop_evals.report import render_csv, render_markdown
from agent_desktop_evals.runner_base import Mode, RunResult


def _result(scenario="a-gnome-settings", runner="openclaw", mode=Mode.BASELINE,
            success=True, tokens=1000, screenshots=2, wallclock_s=12.3):
    return RunResult(
        scenario_id=scenario,
        runner_name=runner,
        mode=mode,
        success=success,
        tokens=tokens,
        screenshots=screenshots,
        wallclock_s=wallclock_s,
        started_at_iso="2026-04-18T12:00:00+00:00",
    )


def test_render_markdown_paired_results():
    results = [
        _result(mode=Mode.BASELINE, tokens=4500, screenshots=8, wallclock_s=45.0),
        _result(mode=Mode.AUGMENTED, tokens=350, screenshots=0, wallclock_s=14.0, success=True),
    ]
    md = render_markdown(results)
    assert "a-gnome-settings" in md
    assert "openclaw" in md
    assert "baseline" in md
    assert "augmented" in md
    assert "4500" in md
    assert "350" in md
    assert "savings" in md.lower() or "delta" in md.lower()


def test_render_csv_has_header_and_rows():
    results = [_result(), _result(mode=Mode.AUGMENTED, tokens=100)]
    csv = render_csv(results)
    lines = csv.strip().splitlines()
    assert lines[0].startswith("scenario_id,runner_name,mode,")
    assert len(lines) == 3  # header + 2 rows
    assert "baseline" in lines[1]
    assert "augmented" in lines[2]


def test_render_markdown_handles_only_one_mode():
    results = [_result(mode=Mode.BASELINE)]
    md = render_markdown(results)
    assert "baseline" in md
    # No comparison row when only one mode is present — assert the absence
    # explicitly (the previous "X not in md or Y in md" was tautological).
    assert "delta" not in md.lower(), md
    assert "savings" not in md.lower(), md
    # And the table should have exactly one data row.
    table_lines = [
        line for line in md.splitlines()
        if line.startswith("| ")
        and not line.startswith("|---")
        and "mode" not in line.lower()  # exclude header
    ]
    # The header row contains the literal "mode" cell label.
    data_rows = [line for line in table_lines if "| baseline " in line or "| augmented " in line]
    assert len(data_rows) == 1, f"expected 1 data row, got {len(data_rows)}: {data_rows}"
