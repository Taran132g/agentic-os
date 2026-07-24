import sys
from pathlib import Path

# Make repo-root modules (execution_workflow, tools/, ...) importable from tests/.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
