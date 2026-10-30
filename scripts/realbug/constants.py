"""tqdm reset(inf) 单样本的固定常量。"""

# 改动这些常量等于改动样本身份；任何修改都必须重跑控制端全流程并重新生成证据包。


FIX_COMMIT = '426a0988a1e9970041f5a649ae8a6376c5293fbe'
BUGGY_PARENT = '5bf58c2d61410e5a17da3440df921fe34c6c38bf'
SOURCE_PATH = 'tqdm/std.py'
TEST_PATH = 'tests/tests_tqdm.py'
ISSUE = ("When a progress bar's counters are cleared and re-initialized with an "
         "infinite total, the bar must treat it as unknown, as it does at construction.")
# These synchronous tests do not need the upstream asyncio/timeout plugins.
# Clear only inherited CLI options; keep upstream tests and fixtures unchanged.
MAIN_COMMAND = 'python -m pytest -o addopts= tests/tests_tqdm.py -k reset'
ACCEPTANCE_PATH = '__replens_acceptance__/test_reset_infinity_contract.py'
ACCEPTANCE_COMMAND = 'python -m pytest -o addopts= ' + ACCEPTANCE_PATH + ' -q'
ACCEPTANCE_SOURCE = '''import math
import unittest
from contextlib import closing
from io import StringIO

from tqdm import tqdm


class ResetInfiniteTotalContract(unittest.TestCase):
    def test_reset_infinity_matches_unknown_total_behavior(self):
        with closing(StringIO()) as output:
            with tqdm(total=8, file=output, miniters=1, mininterval=0, maxinterval=0) as bar:
                bar.update(3)
                bar.reset(total=math.inf)
                bar.update(1)
                rendered = bar.format_meter(bar.n, bar.total, 0)
                self.assertIsNone(bar.total)
                self.assertEqual(bar.n, 1)
        self.assertIn("1it", rendered)
        self.assertNotIn("inf", rendered.lower())

    def test_reset_finite_total_keeps_normal_counting(self):
        with closing(StringIO()) as output:
            with tqdm(total=8, file=output, miniters=1, mininterval=0, maxinterval=0) as bar:
                bar.reset(total=3)
                bar.update(1)
                self.assertEqual((bar.n, bar.total), (1, 3))


if __name__ == "__main__":
    unittest.main()
'''
