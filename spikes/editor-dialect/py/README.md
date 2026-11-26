# article-render (spike)

Server-side half of the Blog Writer v2 article dialect spike: markdown-it-py
renderer, nh3 sanitizer (shared profile in `../sanitizer-profile.json`) and
dialect linter. See `../README.md` for how to run it and
`docs/v2/spikes/editor-dialect.md` for the findings.

```sh
uv sync --locked
uv run pytest                 # UPDATE_SNAPSHOTS=1 to regenerate goldens, then review
uv run ruff check src tests && uv run mypy src
```
