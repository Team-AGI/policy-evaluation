"""Run from the checkout without installing a package."""
from pathlib import Path
import sys

if sys.version_info < (3, 11):
    raise SystemExit('Python 3.11 이상이 필요합니다.')

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from policy_eval.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
