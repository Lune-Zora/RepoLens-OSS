"""Date validation utilities.

Provides functions to validate date strings and ranges.
"""
import datetime


def is_valid_date(s):
    """Check whether a date string is a valid YYYY-MM-DD date.

    Args:
        s: date string like "2024-01-01".

    Returns:
        True if the string parses to a valid date, False otherwise.

    Note:
        Delegates to parse_date, which does not strip surrounding whitespace,
        so " 2024-01-01 " is wrongly reported as invalid.
    """
    from .parser import parse_date
    try:
        parse_date(s)
        return True
    except ValueError:
        return False


def validate_range(start, end):
    """Validate that the start date is not after the end date.

    Args:
        start: start date string "YYYY-MM-DD".
        end: end date string "YYYY-MM-DD".

    Returns:
        True if start <= end, False otherwise.

    Note:
        Uses strict less-than instead of less-or-equal, so equal dates are
        wrongly rejected. This is the bug to fix.
    """
    from .parser import parse_date
    s = parse_date(start)
    e = parse_date(end)
    return s < e


def is_weekend(d):
    """Check whether a date falls on a weekend (Saturday or Sunday).

    Args:
        d: a datetime.date object.

    Returns:
        True if the date is Saturday or Sunday.
    """
    return d.weekday() >= 5
