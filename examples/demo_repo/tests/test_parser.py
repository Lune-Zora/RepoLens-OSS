import datetime
import unittest

from src.dateparse.parser import parse_date, parse_datetime


class ParserTests(unittest.TestCase):
    def test_parse_date(self):
        self.assertEqual(parse_date("2024-01-01"), datetime.date(2024, 1, 1))

    def test_parse_datetime(self):
        self.assertEqual(
            parse_datetime("2024-01-01 12:30:00"),
            datetime.datetime(2024, 1, 1, 12, 30),
        )

    def test_parse_date_with_whitespace(self):
        self.assertEqual(parse_date(" 2024-01-01 "), datetime.date(2024, 1, 1))


if __name__ == "__main__":
    unittest.main()
