"""Each public producer module must be usable without a preloaded service facade."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["templates", "service", "evidence", "retrieval"])
def test_public_producer_modules_import_independently(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import app.modules.knowledge.{module}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_document_facade_preserves_public_evidence_validation():
    script = """from app.modules.knowledge.service import assert_current_evidence
from app.trust.knowledge import assert_current_evidence as legacy
assert callable(assert_current_evidence) and legacy is assert_current_evidence
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
