"""Shared fixtures for tests/bosch_shc.

Provides an autouse patch for async_get_clientsession so that all tests that
call async_setup_entry (which now calls async_get_clientsession as part of
Phase 3c inject-websession) work without a real HA HTTP stack.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_async_get_clientsession():
    """Patch async_get_clientsession for all tests in this package.

    async_setup_entry calls async_get_clientsession(hass, verify_ssl=False) to
    obtain HA's managed aiohttp ClientSession (Phase 3c inject-websession).
    The tests use a MagicMock hass that does not have the real HA network
    internals, so without this patch the call raises KeyError: 'network'.

    Two patch targets are required because:
    - Some test files import via ``from custom_components.bosch_shc import ...``
      → async_setup_entry.__globals__ points at the bosch_shc package module dict
    - test_init_setup.py imports via ``from custom_components.bosch_shc.__init__ import ...``
      → async_setup_entry.__globals__ points at the bosch_shc.__init__ module dict
      (these are different Python module objects with separate __dict__s)
    Patching both ensures coverage regardless of import style.
    """
    mock_session = MagicMock()
    with (
        patch(
            "custom_components.bosch_shc.async_get_clientsession",
            return_value=mock_session,
        ),
        patch(
            "custom_components.bosch_shc.__init__.async_get_clientsession",
            return_value=mock_session,
        ),
    ):
        yield
