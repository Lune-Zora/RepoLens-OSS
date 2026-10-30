# dateparse-demo

A tiny date-parsing library used as the demo target repository for
**RepoLens-OSS**.

## Usage

```python
from dateparse.parser import parse_date

parse_date("2024-01-01")  # -> datetime.date(2024, 1, 1)
```

## Run tests

```bash
pytest
```

## Known issue

`parse_date` does not strip surrounding whitespace, so inputs like
`" 2024-01-01 "` fail to parse. This is intentional — it is the bug that
RepoLens-OSS is meant to locate and fix.
