"""Harness tests run WITH pytest plugin autoload (pytest-homeassistant-custom-component)
and asyncio_mode=auto. Kept separate from tests/bosch_shc (which runs autoload-OFF for
the plain isolation unit tests). Run via scripts/local-ci.sh (harness leg)."""
import os
import sys

import pytest

# Shim HA-core test helpers that pytest-homeassistant-custom-component does not
# re-export, so the adapted core-layout tests import unchanged.
import pytest_homeassistant_custom_component.common as _phccc_common

if not hasattr(_phccc_common, "assert_lists_same"):
    def assert_lists_same(a, b):
        assert len(a) == len(b)
        assert all(i in b for i in a)
        assert all(i in a for i in b)

    _phccc_common.assert_lists_same = assert_lists_same

# Repo root must be importable so `custom_components.bosch_shc` resolves
# (tests/harness has no __init__.py, so pytest does not auto-insert the root).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.fixture
def hass_config_dir(tmp_path):
    """Point HA's config dir at a tmp dir that exposes our repo's custom_components,
    so HA's loader finds bosch_shc here instead of phccc's bundled test config dir."""
    link = tmp_path / "custom_components"
    if not link.exists():
        link.symlink_to(os.path.join(_ROOT, "custom_components"))
    return str(tmp_path)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
