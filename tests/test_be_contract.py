"""Optional structural contract comparison; never imports BE into this process."""
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest


def test_callback_schemas_match_backend():
    root = os.environ.get("BLINDSPOT_BE_REPO")
    if not root:
        pytest.skip("Set BLINDSPOT_BE_REPO to compare against the matching backend branch")
    from app.callbacks.schemas import ExecutionEvent, UsageEvent, CostFinalization
    script = '''
import json
from app.schemas.internal import ExecutionEvent, AIUsageReport, CostFinalization
print(json.dumps([model.model_json_schema() for model in (ExecutionEvent, AIUsageReport, CostFinalization)]))
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(root),
        env={**os.environ, "PYTHONPATH": str(Path(root).resolve())}, capture_output=True, text=True, check=True)
    def normalized(value):
        if isinstance(value, dict):
            return {key: normalized(item) for key, item in value.items() if key != "title"}
        if isinstance(value, list):
            return [normalized(item) for item in value]
        return value
    actual = [model.model_json_schema() for model in (ExecutionEvent, UsageEvent, CostFinalization)]
    assert normalized(actual) == normalized(json.loads(result.stdout))
