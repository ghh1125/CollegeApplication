# CollegeApplication

Python project scaffold for a college application data pipeline.

## Requirements

- Python 3.11 or newer
- uv
- PostgreSQL connection string
- DashScope API key

## Setup

```bash
uv sync
cp .env.example .env
```

Fill in `DATABASE_URL` and `DASHSCOPE_API_KEY` in `.env` before running application code.

## Layout

- `app/config.py`: environment-based settings loaded with `pydantic-settings`
- `app/models/schema.sql`: PostgreSQL schema for importable admissions data
- `app/pipeline/`: pipeline stage modules
- `app/llm/`: DashScope-compatible OpenAI client setup
- `app/export/`: export modules
- `data/raw/`: raw input data
- `tests/`: test package

