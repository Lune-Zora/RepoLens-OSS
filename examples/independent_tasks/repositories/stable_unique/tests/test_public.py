import unittest
import api

class PublicTests(unittest.TestCase):
    def test_one(self): self.assertEqual(api.unique_values([2,2]),[2])
    def test_empty(self): self.assertEqual(api.unique_values([]),[])
    def test_count(self): self.assertEqual(api.count_values([1,1,2]),3)
