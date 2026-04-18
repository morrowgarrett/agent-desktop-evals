# agent-desktop-bench

Reproducible benchmark comparing AI agents controlling Linux native desktop applications **with** vs. **without** the [`agent-desktop`](https://github.com/crowecawcaw/agent-desktop) accessibility-first CLI.

**Status:** M1 — scenario A (GNOME Settings) only. Multi-scenario, multi-DE, and multi-agent expansion lands in subsequent milestones.

**Headline metrics:** success, wall-clock seconds, tokens consumed, screenshots sent, steps taken.

## Quick start

```bash
uv sync
uv run bench run a-gnome-settings --runner openclaw --mode baseline
uv run bench run a-gnome-settings --runner openclaw --mode augmented
```

## Methodology

See [METHODOLOGY.md](METHODOLOGY.md). In short: borrows OSWorld's execution-based eval pattern; carves out a complementary "native Linux apps × display server" track that OSWorld's single-VM Ubuntu setup doesn't exercise.

## License

MIT.
