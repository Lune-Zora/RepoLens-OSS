"""Subprocess-only structured adapters. Core operation needs only the stdlib.

Kept outside the tested repository so ordinary repository discovery cannot collect
the adapter itself. Pytest is imported only when the selected command uses it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import unittest


def main():
    output, framework, *arguments = sys.argv[1:]
    sys.path.insert(0, os.getcwd())
    records = {}
    collection_errors = []
    completed = False
    collected = 0
    # A teardown failure must never be overwritten by a call-phase PASS.
    priority = {'PASS': 0, 'XPASS': 1, 'XFAIL': 2, 'SKIP': 3, 'FAIL': 4, 'ERROR': 5}

    def record(test_id, status):
        old = records.get(test_id)
        if old is None or priority[status] > priority[old]:
            records[test_id] = status

    class Result(unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            record(test.id(), 'PASS')

        def addFailure(self, test, err):
            super().addFailure(test, err)
            record(test.id(), 'FAIL')

        def addError(self, test, err):
            super().addError(test, err)
            record(test.id(), 'ERROR')
            if isinstance(test, unittest.loader._FailedTest):
                collection_errors.append(test.id())

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            record(test.id(), 'SKIP')

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            record(test.id(), 'XFAIL')

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            record(test.id(), 'XPASS')

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                record(test.id(), 'FAIL' if issubclass(err[0], test.failureException) else 'ERROR')

    class Runner(unittest.TextTestRunner):
        resultclass = Result

    class PytestPlugin:
        def pytest_collectreport(self, report):
            if report.failed:
                collection_errors.append(report.nodeid)

        def pytest_runtest_logreport(self, report):
            if report.failed:
                record(report.nodeid, 'FAIL' if report.when == 'call' else 'ERROR')
            elif report.skipped:
                record(report.nodeid, 'XFAIL' if hasattr(report, 'wasxfail') else 'SKIP')
            elif report.when == 'call':
                record(report.nodeid, 'XPASS' if hasattr(report, 'wasxfail') else 'PASS')

        def pytest_sessionfinish(self, session, exitstatus):
            nonlocal collected
            collected = session.testscollected

    code = 2
    try:
        if framework == 'unittest':
            program = unittest.TestProgram(module=None, argv=['unittest'] + arguments,
                                          testRunner=Runner, exit=False)
            collected = program.result.testsRun
            code = 0 if program.result.wasSuccessful() else 1
        else:
            import pytest
            code = int(pytest.main(arguments, plugins=[PytestPlugin()]))
        completed = True
    finally:
        Path(output).write_text(json.dumps({
            'schema': 1, 'completed': completed, 'framework': framework,
            'tests': records, 'collected': collected, 'collection_errors': collection_errors,
        }, ensure_ascii=False), encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
