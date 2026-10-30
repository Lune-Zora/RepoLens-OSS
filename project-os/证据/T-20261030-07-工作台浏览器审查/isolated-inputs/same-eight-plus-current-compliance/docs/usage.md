# Usage

## parse_date

Parse a date string in `YYYY-MM-DD` format.

```python
from dateparse.parser import parse_date
parse_date("2024-01-01")
```

Whitespace around the date string is not handled and will cause a
`ValueError` — see the known issue.
