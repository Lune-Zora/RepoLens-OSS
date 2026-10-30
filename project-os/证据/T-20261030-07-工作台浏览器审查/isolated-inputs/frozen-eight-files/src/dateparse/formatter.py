"""Date formatting utilities."""
import datetime


def format_date(d):
    """Format a date object as YYYY-MM-DD with zero padding.

    Args:
        d: datetime.date object.

    Returns:
        String like "2024-01-05".

    Note:
        Uses plain attribute access without zero-padding, so single-digit
        months/days render as "2024-1-5". This is the bug to fix.
    """
    return f"{d.year}-{d.month}-{d.day}"


def format_iso(dt):
    """Format a datetime as an ISO 8601 string."""
    return dt.isoformat()
