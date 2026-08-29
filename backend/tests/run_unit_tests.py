#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

# Add project root to python path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

def run_suite():
    print("============================================================")
    print("Running evaluation suite unit tests using standard unittest")
    print("============================================================")

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(project_root / "tests" / "unit"), pattern="test_*.py")

    # Also load the offline agent competencies test
    comp_suite = loader.loadTestsFromName("tests.evaluation.test_agent_competencies")
    suite.addTests(comp_suite)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    if not result.wasSuccessful():
        print("\n❌ Offline unit/competency tests failed!")
        sys.exit(1)
    else:
        print("\n✅ All offline unit/competency tests passed successfully!")
        sys.exit(0)

if __name__ == "__main__":
    run_suite()
