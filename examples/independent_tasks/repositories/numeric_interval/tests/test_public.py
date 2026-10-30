import unittest
import api

class PublicTests(unittest.TestCase):
    def test_inside(self): self.assertTrue(api.contains(0,10,5))
    def test_outside(self): self.assertFalse(api.contains(0,10,20))
    def test_width(self): self.assertEqual(api.interval_width(-1,3),4)
