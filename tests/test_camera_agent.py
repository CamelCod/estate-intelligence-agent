#!/usr/bin/env python3
"""
test_camera_agent.py — End-to-End Test Suite for Laptop Camera Agent
Estate Intelligence Agent

Tests each pipeline stage independently so you can validate your setup
before running the full agent. Run with: python tests/test_camera_agent.py

Tests:
  1. Camera availability
  2. Frame capture & motion detection
  3. JPEG encoding & base64
  4. Claude Vision API (single frame)
  5. Claude LLM classification
  6. Full pipeline (motion → vision → decision)

Requires ANTHROPIC_API_KEY in environment or .env file.
"""

from __future__ import annotations

import os
import sys
import json
import time
import base64
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

# ── Add src/ to path ─────────────────────────────────────────────────────────
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

# ── Load .env if present ─────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass


# ── Colour helpers ────────────────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    DIM    = "\033[2m"
    GOLD   = "\033[33m"


def ok(msg: str)   -> None: print(f"  {C.GREEN}✓{C.RESET} {msg}")
def fail(msg: str) -> None: print(f"  {C.RED}✗{C.RESET} {msg}")
def info(msg: str) -> None: print(f"  {C.CYAN}ℹ{C.RESET} {msg}")
def warn(msg: str) -> None: print(f"  {C.YELLOW}⚠{C.RESET} {msg}")

def section(title: str) -> None:
    print(f"\n{C.GOLD}{'─' * 60}{C.RESET}")
    print(f"{C.GOLD}{C.BOLD}  TEST: {title}{C.RESET}")
    print(f"{C.GOLD}{'─' * 60}{C.RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — Camera availability
# ─────────────────────────────────────────────────────────────────────────────

def test_camera_availability() -> Optional[Any]:
    """
    Goal  : Verify laptop camera can be opened and returns frames.
    Input : None (uses CAMERA_DEVICE env or defaults to 0)
    Output: Returns cv2.VideoCapture object if OK, None if failed.
    """
    section("Camera Availability")

    try:
        import cv2
        ok(f"OpenCV imported — version {cv2.__version__}")
    except ImportError as exc:
        fail(f"OpenCV not installed: {exc}")
        info("Fix: pip install opencv-python --break-system-packages")
        return None

    device_index : int = int(os.environ.get("CAMERA_DEVICE", "0"))
    info(f"Opening camera device {device_index}…")

    capture = cv2.VideoCapture(device_index)
    if not capture.isOpened():
        fail(f"Camera device {device_index} could not be opened")
        warn("Check system camera permissions or try CAMERA_DEVICE=1")
        return None

    ok(f"Camera opened on device {device_index}")

    success, frame = capture.read()
    if not success or frame is None:
        fail("Camera opened but returned no frame")
        capture.release()
        return None

    height, width = frame.shape[:2]
    ok(f"Frame captured — {width}×{height} pixels, {frame.dtype}")

    return capture
# end function test_camera_availability


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — Motion detection
# ─────────────────────────────────────────────────────────────────────────────

def test_motion_detection(capture: Any) -> bool:
    """
    Goal  : Verify motion detection produces a score from 0.0 to 1.0.
    Input : open cv2.VideoCapture
    Output: True if test passes.
    """
    section("Motion Detection")

    from laptop_camera_agent import compute_motion_score
    import numpy as np

    # Test 1: identical frames → score should be 0
    _, frame_a = capture.read()
    _, frame_b = capture.read()
    if frame_a is None or frame_b is None:
        fail("Could not read two frames")
        return False

    score_identical : float = compute_motion_score(frame_a, frame_a)  # same frame
    ok(f"Identical frames → motion score: {score_identical:.4f} (expect ≈ 0)")
    assert score_identical < 0.001, f"Expected ~0, got {score_identical}"

    # Test 2: blank black vs white → score should be ~1.0
    black_frame : Any = np.zeros_like(frame_a)
    white_frame : Any = np.ones_like(frame_a) * 255
    score_max   : float = compute_motion_score(black_frame, white_frame)
    ok(f"Black vs white → motion score: {score_max:.4f} (expect ≈ 1.0)")
    assert score_max > 0.8, f"Expected ~1.0, got {score_max}"

    # Test 3: real consecutive frames (should be low if camera is still)
    score_live : float = compute_motion_score(frame_a, frame_b)
    ok(f"Two live frames → motion score: {score_live:.4f}")
    info(f"Threshold is {float(os.environ.get('MOTION_THRESHOLD', '0.015')):.1%}")

    return True
# end function test_motion_detection


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — JPEG encoding
# ─────────────────────────────────────────────────────────────────────────────

def test_jpeg_encoding(capture: Any) -> Optional[str]:
    """
    Goal  : Verify a frame can be encoded to base64 JPEG for API upload.
    Input : open cv2.VideoCapture
    Output: base64 JPEG string if OK, None if failed.
    """
    section("JPEG Encoding & Base64")

    from laptop_camera_agent import encode_frame_jpeg

    _, frame = capture.read()
    if frame is None:
        fail("Could not read frame")
        return None

    try:
        b64_string : str = encode_frame_jpeg(frame)
    except Exception as exc:
        fail(f"encode_frame_jpeg failed: {exc}")
        return None

    # Verify it decodes back to valid JPEG
    try:
        decoded_bytes : bytes = base64.b64decode(b64_string)
        size_kb       : float = len(decoded_bytes) / 1024
        ok(f"JPEG encoded — {size_kb:.1f} KB base64 ({len(b64_string)} chars)")
    except Exception as exc:
        fail(f"Base64 decode check failed: {exc}")
        return None

    # Verify first bytes are JPEG magic
    jpeg_magic : bool = decoded_bytes[:2] == b"\xff\xd8"
    if jpeg_magic:
        ok("JPEG magic bytes verified (0xff 0xd8)")
    else:
        warn("Unexpected file header — may not be valid JPEG")

    return b64_string
# end function test_jpeg_encoding


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — Claude Vision API
# ─────────────────────────────────────────────────────────────────────────────

def test_claude_vision(b64_jpeg: str) -> Optional[str]:
    """
    Goal  : Send a real frame to Claude Vision and verify a text description.
    Input : base64 JPEG string
    Output: Scene description string if OK, None if failed.
    """
    section("Claude Vision API")

    api_key : str = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        fail("ANTHROPIC_API_KEY not set")
        info("Fix: export ANTHROPIC_API_KEY=sk-ant-... or add to .env")
        return None

    ok(f"ANTHROPIC_API_KEY found (ends …{api_key[-6:]})")

    import anthropic
    from laptop_camera_agent import describe_frame_with_vision

    client = anthropic.Anthropic(api_key=api_key)
    info("Sending frame to Claude Vision — please wait…")

    t0 : float = time.time()
    try:
        description : str = describe_frame_with_vision(b64_jpeg, client)
        elapsed      : float = time.time() - t0
    except Exception as exc:
        fail(f"Vision API call failed: {exc}")
        traceback.print_exc()
        return None

    ok(f"Vision response received in {elapsed:.1f}s")
    ok(f"Description ({len(description.split())} words):")
    print(f"\n{C.DIM}  {description}{C.RESET}\n")

    assert len(description) > 20, "Description too short — possible API issue"
    return description
# end function test_claude_vision


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — Claude LLM classification
# ─────────────────────────────────────────────────────────────────────────────

def test_llm_classification(description: str) -> Optional[Dict[str, Any]]:
    """
    Goal  : Verify the LLM classification layer returns a valid decision dict.
    Input : Scene description from vision test
    Output: Decision dict if OK, None if failed.
    """
    section("Claude LLM Event Classification")

    api_key : str = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        fail("ANTHROPIC_API_KEY not set — skipping")
        return None

    import anthropic
    from laptop_camera_agent import classify_event_with_llm

    client     = anthropic.Anthropic(api_key=api_key)
    info("Sending description to LLM classifier…")

    t0       : float = time.time()
    decision : Dict[str, Any] = classify_event_with_llm(description, [], client)
    elapsed  : float = time.time() - t0

    ok(f"LLM response received in {elapsed:.1f}s")

    # Validate structure
    required_keys = ["action", "label", "summary", "confidence", "reasoning"]
    missing_keys  = [k for k in required_keys if k not in decision]
    if missing_keys:
        fail(f"Decision missing keys: {missing_keys}")
        return None

    ok(f"Decision structure valid — all {len(required_keys)} keys present")

    action     : str   = decision["action"]
    label      : str   = decision["label"]
    summary    : str   = decision["summary"]
    confidence : float = decision["confidence"]

    valid_actions : list = ["alert", "log", "escalate"]
    if action not in valid_actions:
        fail(f"Invalid action: '{action}' — expected one of {valid_actions}")
    else:
        ok(f"Action: {C.YELLOW}{action.upper()}{C.RESET}")

    ok(f"Label     : {label}")
    ok(f"Confidence: {confidence:.0%}")
    ok(f"Summary   : {summary}")
    info(f"Reasoning : {decision.get('reasoning', '')}")

    return decision
# end function test_llm_classification


# ─────────────────────────────────────────────────────────────────────────────
# TEST 6 — Full pipeline integration
# ─────────────────────────────────────────────────────────────────────────────

def test_full_pipeline(capture: Any) -> bool:
    """
    Goal  : Run the complete pipeline on one frame: capture→vision→LLM→event.
    Input : open cv2.VideoCapture
    Output: True if pipeline completes without error.
    """
    section("Full Pipeline Integration Test")

    api_key : str = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        fail("ANTHROPIC_API_KEY not set — cannot run full pipeline test")
        return False

    import anthropic
    from laptop_camera_agent import process_motion_frame

    client = anthropic.Anthropic(api_key=api_key)

    _, frame = capture.read()
    if frame is None:
        fail("Could not read frame for integration test")
        return False

    info("Running full pipeline on one frame — this takes ~5s…")
    t0 : float = time.time()

    try:
        # Force trigger at motion_score=1.0 to test the full path
        process_motion_frame(frame, motion_score=1.0, claude_client=client)
        elapsed : float = time.time() - t0
        ok(f"Full pipeline completed in {elapsed:.1f}s")
        return True
    except Exception as exc:
        fail(f"Pipeline failed: {exc}")
        traceback.print_exc()
        return False
# end function test_full_pipeline


# ─────────────────────────────────────────────────────────────────────────────
# MAIN — Run all tests
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{C.GOLD}{'═' * 60}{C.RESET}")
    print(f"{C.GOLD}{C.BOLD}  🏡 ESTATE INTELLIGENCE — CAMERA AGENT TEST SUITE{C.RESET}")
    print(f"{C.GOLD}{'═' * 60}{C.RESET}")

    results    : Dict[str, bool] = {}
    capture    = None
    b64_jpeg   = None
    description= None

    # ── Test 1: Camera ────────────────────────────────────────────────────────
    capture = test_camera_availability()
    results["1. Camera availability"] = capture is not None

    if capture is None:
        warn("Camera unavailable — skipping remaining tests")
        _print_summary(results)
        return

    # ── Test 2: Motion ────────────────────────────────────────────────────────
    results["2. Motion detection"] = test_motion_detection(capture)

    # ── Test 3: JPEG encoding ─────────────────────────────────────────────────
    b64_jpeg = test_jpeg_encoding(capture)
    results["3. JPEG encoding"] = b64_jpeg is not None

    if b64_jpeg is None:
        warn("Encoding failed — skipping API tests")
        _print_summary(results)
        capture.release()
        return

    # ── Test 4: Claude Vision ─────────────────────────────────────────────────
    description = test_claude_vision(b64_jpeg)
    results["4. Claude Vision API"] = description is not None

    # ── Test 5: LLM classification ────────────────────────────────────────────
    if description:
        decision = test_llm_classification(description)
        results["5. LLM classification"] = decision is not None
    else:
        results["5. LLM classification"] = False
        warn("Skipping LLM test — no description from vision")

    # ── Test 6: Full pipeline ─────────────────────────────────────────────────
    results["6. Full pipeline"] = test_full_pipeline(capture)

    capture.release()
    _print_summary(results)
# end function main


def _print_summary(results: Dict[str, bool]) -> None:
    """Print the final pass/fail summary."""
    total   : int = len(results)
    passed  : int = sum(1 for v in results.values() if v)

    print(f"\n{C.GOLD}{'═' * 60}{C.RESET}")
    print(f"{C.GOLD}{C.BOLD}  RESULTS: {passed}/{total} tests passed{C.RESET}")
    print(f"{C.GOLD}{'─' * 60}{C.RESET}")

    for test_name, result in results.items():
        status : str = f"{C.GREEN}PASS{C.RESET}" if result else f"{C.RED}FAIL{C.RESET}"
        print(f"  {status}  {test_name}")

    print(f"{C.GOLD}{'═' * 60}{C.RESET}")

    if passed == total:
        print(f"\n{C.GREEN}{C.BOLD}  ✓ All tests passed! Run the agent with:{C.RESET}")
        print(f"  {C.CYAN}  python src/laptop_camera_agent.py{C.RESET}")
    else:
        failed_tests = [k for k, v in results.items() if not v]
        print(f"\n{C.YELLOW}  ⚠ Fix failing tests before running the agent:{C.RESET}")
        for test in failed_tests:
            print(f"  {C.RED}  - {test}{C.RESET}")

    print()
# end function _print_summary


if __name__ == "__main__":
    main()
