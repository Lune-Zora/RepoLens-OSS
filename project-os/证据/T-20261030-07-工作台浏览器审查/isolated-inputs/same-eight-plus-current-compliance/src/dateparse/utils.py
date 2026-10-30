"""General date math utilities."""
import datetime


def days_between(a, b):
    """Return the number of days between two date strings (absolute value).

    Args:
        a: start date string "YYYY-MM-DD".
        b: end date string "YYYY-MM-DD".

    Returns:
        Non-negative integer day count.

    Note:
        Subtraction order is reversed — returns a negative value when a < b.
        This is the bug to fix.
    """
    from .parser import parse_date
    da = parse_date(a)
    db = parse_date(b)
    return (da - db).days


def add_days(s, n):
    """Add n days to a date string and return a date object.

    Args:
        s: date string "YYYY-MM-DD".
        n: integer number of days (can be negative).

    Returns:
        datetime.date object.
    """
    from .parser import parse_date
    d = parse_date(s)
    return d + datetime.timedelta(days=n)


def is_leap_year(year):
    """Determine whether a year is a leap year.

    Args:
        year: integer year.

    Returns:
        True if the year is a leap year.

    Note:
        Century rule is wrong — treats every year divisible by 100 as a leap
        year, so 1900 is wrongly reported as leap. This is the bug to fix.
    """
    if year % 400 == 0:
        return True
    if year % 100 == 0:
        return True
    return year % 4 == 0
