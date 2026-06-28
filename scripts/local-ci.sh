#!/usr/bin/env bash
# local-ci.sh — mirrors the remote Quality + Tests workflows locally.
# Usage: ./scripts/local-ci.sh [hass|lib|all]   (default: all)
# Requires: ruff, codespell, pylint, pytest  (pip install ruff codespell pylint pytest pytest-cov pytest-timeout)
set -euo pipefail

HASS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LIB_DIR="$(cd "$HASS_DIR/../boschshcpy" && pwd)"
TARGET="${1:-all}"
PASS=0; FAIL=0

_banner() { echo; echo "=== $* ==="; }
_ok()     { echo "  ✓ $*"; PASS=$((PASS+1)); }
_fail()   { echo "  ✗ $*"; FAIL=$((FAIL+1)); }

run_hass() {
  cd "$HASS_DIR"
  _banner "ruff check"
  if ruff check custom_components; then _ok "ruff check"; else _fail "ruff check"; fi

  _banner "ruff format"
  if ruff format --check custom_components; then _ok "ruff format"; else _fail "ruff format"; fi

  _banner "codespell"
  if codespell custom_components --skip="*/translations/*" --ignore-words-list=hass,nd,childs,unparseable; then
    _ok "codespell"
  else _fail "codespell"; fi

  _banner "pylint"
  if PYTHONPATH="$HASS_DIR:$LIB_DIR" pylint --rcfile=pyproject.toml custom_components/bosch_shc; then _ok "pylint"; else _fail "pylint"; fi

  _banner "quality scale (gold)"
  if python3 scripts/check-quality-scale.py --tier gold; then _ok "quality scale"; else _fail "quality scale"; fi

  _banner "pytest (hass)"
  if PYTHONPATH="$HASS_DIR:$LIB_DIR" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
      python3 -m pytest tests/bosch_shc/ -q -o addopts="" \
      --ignore=tests/bosch_shc/test_device_trigger.py \
      --ignore=tests/bosch_shc/test_config_flow.py; then
    _ok "pytest hass"
  else _fail "pytest hass"; fi
}

run_lib() {
  cd "$LIB_DIR"
  _banner "pytest (lib)"
  if PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q -o addopts=""; then
    _ok "pytest lib"
  else _fail "pytest lib"; fi
}

case "$TARGET" in
  hass) run_hass ;;
  lib)  run_lib  ;;
  all)  run_hass; run_lib ;;
  *)    echo "Usage: $0 [hass|lib|all]"; exit 1 ;;
esac

echo
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
