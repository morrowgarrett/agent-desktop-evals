from agent_desktop_evals.report import _format_tool_calls, render_csv, render_markdown
from agent_desktop_evals.runner_base import Mode, RunResult


def _result(scenario="a-gnome-settings", runner="openclaw", mode=Mode.BASELINE,
            success=True, tokens=1000, screenshots=2, wallclock_s=12.3,
            tool_calls=None):
    return RunResult(
        scenario_id=scenario,
        runner_name=runner,
        mode=mode,
        success=success,
        tokens=tokens,
        screenshots=screenshots,
        wallclock_s=wallclock_s,
        started_at_iso="2026-04-18T12:00:00+00:00",
        tool_calls=tool_calls or {},
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


def test_render_markdown_warns_on_duplicate_results():
    """Two results with same (scenario, runner, mode) should surface a warning,
    not silently overwrite."""
    results = [
        _result(tokens=100),  # scenario=a-gnome-settings, runner=openclaw, mode=BASELINE
        _result(tokens=200),  # same key — duplicate
    ]
    md = render_markdown(results)
    assert (
        "duplicate" in md.lower()
        or "collision" in md.lower()
        or "warning" in md.lower()
    ), f"expected duplicate warning in output:\n{md}"
    # The latest values should still be in the table (200, not 100), but the
    # collision must be flagged.
    assert "200" in md


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


def test_render_csv_includes_tool_calls():
    results = [_result(tool_calls={"exec": 5, "read": 1})]
    csv = render_csv(results)
    lines = csv.strip().splitlines()
    assert "tool_calls" in lines[0]
    # CSV will quote the value containing a comma
    assert "exec:5,read:1" in lines[1] or '"exec:5,read:1"' in lines[1]


def test_render_csv_handles_empty_tool_calls():
    import csv as csv_module
    import io
    results = [_result(tool_calls={})]
    csv = render_csv(results)
    lines = csv.strip().splitlines()
    headers = lines[0].split(",")
    tc_idx = headers.index("tool_calls")
    row = next(csv_module.reader(io.StringIO(lines[1])))
    assert row[tc_idx] == "", f"expected empty tool_calls, got {row[tc_idx]!r}"


def test_render_markdown_includes_tool_calls_column():
    results = [_result(tool_calls={"exec": 4, "read": 7, "process": 2})]
    md = render_markdown(results)
    assert "tool calls" in md
    # Sorted by count desc, then by name asc on ties
    assert "read:7,exec:4,process:2" in md


def test_format_tool_calls_sorted_desc_then_name():
    """Ties broken by name ascending."""
    assert _format_tool_calls({"a": 3, "b": 5, "c": 5}) == "b:5,c:5,a:3"


def test_format_tool_calls_empty():
    assert _format_tool_calls({}) == ""
