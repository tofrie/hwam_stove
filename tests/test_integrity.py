"""The independent static checks also run in the full suite."""

import pytest

from scripts.check_integrity import validate


@pytest.mark.contract
def test_runtime_manifest_structure_and_translations():
    assert validate()["runtime_files_byte_equal"] == 16
