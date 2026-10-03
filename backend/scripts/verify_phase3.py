#!/usr/bin/env python3
"""
Verification script for Phase 3: Configuration and startup improvements.

Tests:
1. Config module loads correctly
2. Default values are production-safe
3. Health check endpoints are available
"""

import os
import sys

# Add project root to path so `core.*` package imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_config():
    """Test configuration module."""
    print("Testing configuration module...")

    from core.config import config

    # Check default values
    assert not config.RELOAD, "RELOAD should default to False for production safety"
    assert config.HOST == "0.0.0.0", "HOST should default to 0.0.0.0"
    assert config.PORT == 8000, "PORT should default to 8000"
    assert config.LOG_LEVEL == "INFO", "LOG_LEVEL should default to INFO"

    print("✓ Config defaults are correct")

    # Check paths
    assert config.project_root.exists(), "project_root should exist"
    print(f"✓ Project root: {config.project_root}")

    # Test summary
    summary = config.summary()
    assert "BibleLive Configuration:" in summary
    assert str(config.PORT) in summary
    print("✓ Config summary works")

    print()
    return True


def test_server_import():
    """Test that server imports without errors."""
    print("Testing server imports...")

    try:
        # Build the app via the factory (no module-level global anymore)
        from core.server import create_app

        app = create_app()

        print("✓ Server module imports successfully")

        # Check that health check routes exist
        routes = [route.path for route in app.routes]
        assert "/healthz" in routes, "/healthz endpoint should exist"
        assert "/readyz" in routes, "/readyz endpoint should exist"
        print("✓ Health check endpoints registered")

        print()
        return True
    except Exception as e:
        print(f"✗ Server import failed: {e}")
        return False


def main():
    """Run all verification tests."""
    print("=" * 60)
    print("Phase 3 Verification")
    print("=" * 60)
    print()

    results = []

    try:
        results.append(("Configuration", test_config()))
    except Exception as e:
        print(f"✗ Configuration test failed: {e}")
        results.append(("Configuration", False))

    try:
        results.append(("Server Import", test_server_import()))
    except Exception as e:
        print(f"✗ Server import test failed: {e}")
        results.append(("Server Import", False))

    print("=" * 60)
    print("Results:")
    print("=" * 60)
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")

    all_passed = all(passed for _, passed in results)
    print()
    if all_passed:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
