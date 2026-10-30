"""Date parsing utilities for the demo repository.

This package demonstrates a small, self-contained open-source project
used to showcase the RepoLens-OSS workflow.
"""
import datetime


def parse_date(s):
    """Parse a date string in YYYY-MM-DD format.

    Args:
        s: the date string, e.g. "2024-01-01".

    Returns:
        datetime.date object.

    Note:
        The input is not stripped of surrounding whitespace, so a string
        like " 2024-01-01 " will fail to parse. This is the bug to fix:
        call s.strip() before parsing.
    """
    return datetime.datetime.strptime(s, "%Y-%m-%d").date()


def parse_datetime(s):
    """Parse a datetime string in YYYY-MM-DD HH:MM:SS format."""
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
