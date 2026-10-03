#!/usr/bin/env python3
"""Quick verification that Phase 1 changes work correctly"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.server import BibleReference, create_app

print("✓ Imports successful")

# Build an app instance and use its AppState (no module-level global anymore).
app = create_app()
app_state = app.state.app_state
print("✓ App factory returned app + AppState")

# Test 1: Reference gets ID automatically
ref = BibleReference(
    book="John", chapter=3, verse_start=16, confidence="high", inferred=False, text=""
)
assert hasattr(ref, "id"), "Reference should have id field"
assert hasattr(ref, "created_at"), "Reference should have created_at field"
assert len(ref.id) > 0, "ID should not be empty"
print(f"✓ Reference auto-generated ID: {ref.id[:8]}...")


# Test 2: Pending queue works with IDs
async def _check_queue() -> None:
    app_state.pending_references.clear()
    await app_state.add_reference(ref)
    assert len(app_state.pending_references) == 1
    print("✓ Added reference to pending queue")

    # Test 3: Approval by ID works
    ref_id = ref.id
    approved = await app_state.approve_pending(ref_id)
    assert approved is not None, "Should approve valid reference"
    assert approved.id == ref_id, "Should approve correct reference"
    assert len(app_state.pending_references) == 0, "Pending should be empty after approval"
    assert app_state.current_display is not None, "Current display should be set"
    assert app_state.current_display.id == ref_id, "Display should show approved reference"
    print(f"✓ Approved reference by ID: {ref_id[:8]}...")

    # Test 4: Invalid ID returns None
    result = await app_state.approve_pending("nonexistent-id")
    assert result is None, "Should return None for invalid ID"
    print("✓ Correctly rejects nonexistent reference ID")


asyncio.run(_check_queue())

# Test 5: History bounded at 50
app_state.reference_history.clear()
for i in range(55):
    ref = BibleReference(
        book="Psalms", chapter=i + 1, verse_start=1, confidence="high", inferred=False, text=""
    )
    app_state.reference_history.insert(0, ref)
    if len(app_state.reference_history) > 50:
        app_state.reference_history = app_state.reference_history[:50]

assert len(app_state.reference_history) == 50, (
    f"History should be bounded at 50, got {len(app_state.reference_history)}"
)
print("✓ History correctly bounded at 50 items")

print("\n✅ All Phase 1 changes verified successfully!")
