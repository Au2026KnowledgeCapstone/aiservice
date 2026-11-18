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


def test_operation_schemas_match_backend():
    root = os.environ.get('BLINDSPOT_BE_REPO')
    if not root:
        pytest.skip('Set BLINDSPOT_BE_REPO for operation contract comparison')
    from app.operations.schemas import ExtractionRequest, ExtractionResponse, VerificationRequest, VerificationResponse
    names = ['ExtractionRequest', 'ExtractionResponse', 'VerificationRequest', 'VerificationResponse']
    # BE still calls this module `capabilities`; accept either until its half of
    # the rename lands. The class names are identical on both sides regardless.
    script = ('import json\n'
              'try:\n'
              '    from app.schemas import operations as s\n'
              'except ImportError:\n'
              '    from app.schemas import capabilities as s\n'
              'print(json.dumps([getattr(s, n).model_json_schema() for n in ' + repr(names) + ']))')
    result = subprocess.run([sys.executable, '-c', script], cwd=Path(root),
        env={**os.environ, 'PYTHONPATH': str(Path(root).resolve())}, capture_output=True, text=True, check=True)
    assert [m.model_json_schema() for m in (ExtractionRequest, ExtractionResponse, VerificationRequest, VerificationResponse)] == json.loads(result.stdout)
