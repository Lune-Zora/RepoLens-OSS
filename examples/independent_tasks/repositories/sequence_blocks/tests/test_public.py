import unittest
import api

class PublicTests(unittest.TestCase):
    def test_exact(self): self.assertEqual(api.partition([1,2,3,4],2), [[1,2],[3,4]])
    def test_empty(self): self.assertEqual(api.partition([],2), [])
    def test_invalid(self):
        with self.assertRaises(ValueError): api.partition([1],0)
    def test_flatten(self): self.assertEqual(api.flatten([[1],[2,3]]),[1,2,3])
