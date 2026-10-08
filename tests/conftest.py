import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The Lambda folders are deployed as flat zips, so their modules import each other by
# plain name (e.g. "from validation import ..."). Mirror that here.
sys.path.insert(0, str(ROOT / "lambdas" / "ingest"))
sys.path.insert(0, str(ROOT / "lambdas" / "query"))

os.environ.setdefault("TABLE_NAME", "test-table")
os.environ.setdefault("EVENT_TTL_DAYS", "30")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-3")
