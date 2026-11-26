# Blog Writer v2 server

Python 3.12 package `blogwriter` (src layout, managed with `uv`). It currently holds the LinkedIn
little-text serializer; the rest of the v2 server lands here phase by phase.

| Module | What it does |
|---|---|
| `blogwriter.linkedin.littletext` | Token model (`Text`, `Hashtag`, `Mention`, `Link`), `display_text`, `api_commentary` (escaped `commentary` payload for `/rest/posts`), `parse_display` (approved hashtags only), `escape` / `unescape` |

## Run

```sh
cd server
uv sync                       # Python 3.12 venv in server/.venv, pinned deps from uv.lock
uv run ruff check .
uv run pytest -q -x
PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright   # strict; uses the bundled pyright, no update check
```

The tests make no network calls.
