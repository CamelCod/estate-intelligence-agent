#!/usr/bin/env python3
"""
laptop_camera_agent.py — Live Laptop Camera CCTV Intelligence Agent
Estate Intelligence Agent

ALGORITHM: laptopCameraAgent
GOAL:      Capture laptop camera, detect motion, send frames to Claude Vision,
           classify events with Claude LLM, and display live terminal feedback.
INPUT:     Laptop webcam (device index 0 by default)
OUTPUT:    Terminal event log + optional Telegram alerts
STEPS:
  1. Open webcam — fail fast if camera not accessible.
  2. Capture baseline frame for motion comparison.
  3. Enter capture loop:
     a. Read new frame from webcam.
     b. Compute grayscale pixel diff vs. baseline frame.
     c. If diff score > MOTION_THRESHOLD and cooldown elapsed:
        i.  Encode frame as JPEG → base64 string.
        ii. Send to Claude Vision → receive scene description.
        iii. Send description to Claude LLM → receive decision dict.
        iv. Execute action: log_event | send_telegram | escalate.
        v.  Print event line to terminal live dashboard.
        vi. Update baseline frame.
     d. Update dashboard display with current stats.
     e. Sleep FRAME_SLEEP_SEC between frames.
  4. On Ctrl+C: log summary and exit cleanly.
"""

from __future__ import annotations

import os
import sys
import json
import time
import base64
import signal
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

# ── Third-party ───────────────────────────────────────────────────────────────
try:
    import cv2
except ImportError:
    print("[ERROR] OpenCV not installed. Run: pip install opencv-python --break-system-packages")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    print("[ERROR] NumPy not installed. Run: pip install numpy --break-system-packages")
    sys.exit(1)

try:
    import anthropic
except ImportError:
    print("[ERROR] Anthropic SDK not installed. Run: pip install anthropic --break-system-packages")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Optional — .env loaded manually if not available


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR I — Configuration (name : type = initial value)
# ─────────────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY   : str   = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN  : str   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    : str   = os.environ.get("TELEGRAM_CHAT_ID", "")

CAMERA_DEVICE_INDEX : int   = int(os.environ.get("CAMERA_DEVICE", "0"))
MOTION_THRESHOLD    : float = float(os.environ.get("MOTION_THRESHOLD", "0.015"))  # 1.5% pixels changed
CAPTURE_COOLDOWN_SEC: float = float(os.environ.get("CAPTURE_COOLDOWN", "5.0"))   # min secs between captures
FRAME_SLEEP_SEC     : float = float(os.environ.get("FRAME_SLEEP", "0.1"))        # 10 fps poll rate
FRAME_WIDTH         : int   = int(os.environ.get("FRAME_WIDTH", "640"))
FRAME_HEIGHT        : int   = int(os.environ.get("FRAME_HEIGHT", "480"))
JPEG_QUALITY        : int   = int(os.environ.get("JPEG_QUALITY", "75"))          # lower = smaller payload

CLAUDE_VISION_MODEL : str   = "claude-opus-4-5"
CLAUDE_LLM_MODEL    : str   = "claude-haiku-4-5-20251001"   # fast/cheap for decisions
VISION_MAX_TOKENS   : int   = 300
DECISION_MAX_TOKENS : int   = 200

OWNER_CONTEXT       : str   = os.environ.get("OWNER_CONTEXT", "UAE villa owner monitoring their home")
LOCATION_LABEL      : str   = os.environ.get("LOCATION_LABEL", "Home / Office")
SHOW_PREVIEW        : bool  = os.environ.get("SHOW_PREVIEW", "false").lower() == "true"

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level  = logging.WARNING,            # suppress noise — we have our own dashboard
    format = "%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("camera-agent")


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL STATE (PILLAR I)
# ─────────────────────────────────────────────────────────────────────────────

event_log       : List[Dict[str, Any]] = []   # all captured events this session
running_flag    : bool                 = True  # main loop sentry (PILLAR V)
frames_read     : int                  = 0     # total frames processed
last_capture_ts : float                = 0.0  # timestamp of last Claude call
start_time      : float                = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# ANSI TERMINAL COLOURS
# ─────────────────────────────────────────────────────────────────────────────

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    GOLD   = "\033[33m"


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR VI — Function: open_camera
# GOAL  : Open the laptop webcam and configure resolution.
# INPUT : device_index (int) — camera device number (0 = built-in)
# OUTPUT: Returns cv2.VideoCapture object; raises RuntimeError if unavailable.
# STEPS :
#   1. Create VideoCapture with device_index.
#   2. Test isOpened() — raise RuntimeError if False.
#   3. Set frame width and height properties.
#   4. Read a test frame to confirm live feed.
#   5. Return capture object.
# ─────────────────────────────────────────────────────────────────────────────
def open_camera(device_index: int) -> cv2.VideoCapture:
    capture = cv2.VideoCapture(device_index)

    if not capture.isOpened():
        raise RuntimeError(
            f"Camera device {device_index} could not be opened. "
            "Check camera permissions or try a different device index."
        )

    capture.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    success, test_frame = capture.read()
    if not success or test_frame is None:
        raise RuntimeError("Camera opened but returned no frame — is it in use by another app?")

    actual_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"{C.GREEN}✓ Camera opened{C.RESET} — device {device_index} @ {actual_w}×{actual_h}")
    return capture
# end function open_camera


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR VI — Function: compute_motion_score
# GOAL  : Compare two frames and return a normalised motion score 0.0–1.0.
# INPUT : frameA (ndarray), frameB (ndarray)
# OUTPUT: Returns motionScore (float) — fraction of pixels that changed.
# STEPS :
#   1. Convert both frames to grayscale.
#   2. Apply Gaussian blur (5×5) to reduce noise.
#   3. Compute absolute difference.
#   4. Threshold at pixel delta > 25.
#   5. Count changed pixels / total pixels.
#   6. Return score.
# ─────────────────────────────────────────────────────────────────────────────
def compute_motion_score(frame_a: np.ndarray, frame_b: np.ndarray) -> float:
    gray_a = cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)

    blurred_a = cv2.GaussianBlur(gray_a, (5, 5), 0)
    blurred_b = cv2.GaussianBlur(gray_b, (5, 5), 0)

    diff = cv2.absdiff(blurred_a, blurred_b)

    _, threshold_frame = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

    changed_pixels : int   = int(np.sum(threshold_frame > 0))
    total_pixels   : int   = threshold_frame.size
    motion_score   : float = changed_pixels / total_pixels

    return motion_score
# end function compute_motion_score


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR VI — Function: encode_frame_jpeg
# GOAL  : Encode a BGR OpenCV frame as a base64 JPEG string for API upload.
# INPUT : frame (ndarray) — BGR camera frame
# OUTPUT: Returns base64String (str) — base64-encoded JPEG bytes
# STEPS :
#   1. Encode frame to JPEG with quality setting.
#   2. Convert bytes to base64 string.
#   3. Return string.
# ─────────────────────────────────────────────────────────────────────────────
def encode_frame_jpeg(frame: np.ndarray) -> str:
    encode_params  = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    success, buffer = cv2.imencode(".jpg", frame, encode_params)

    if not success:
        raise ValueError("Failed to encode frame as JPEG")

    jpeg_bytes     : bytes = buffer.tobytes()
    base64_string  : str   = base64.standard_b64encode(jpeg_bytes).decode("utf-8")
    return base64_string
# end function encode_frame_jpeg


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR VI — Function: describe_frame_with_vision
# GOAL  : Send a frame to Claude Vision and get a scene description.
# INPUT : base64_jpeg (str) — base64-encoded JPEG frame
# OUTPUT: Returns sceneDescription (str) — 2–4 sentence scene summary
# STEPS :
#   1. Build message with image + text instruction blocks.
#   2. Call claude_client.messages.create() with vision model.
#   3. Extract text from response content blocks.
#   4. Return description string.
# ─────────────────────────────────────────────────────────────────────────────
def describe_frame_with_vision(base64_jpeg: str, claude_client: anthropic.Anthropic) -> str:
    vision_prompt : str = (
        "You are analysing a security camera frame. "
        "Describe in 2-3 sentences: (1) what you see (people, objects, environment), "
        "(2) any notable activity or movement, "
        "(3) approximate time-of-day cues if visible. "
        "Be factual and specific. If no people are visible, say so."
    )

    response = claude_client.messages.create(
        model      = CLAUDE_VISION_MODEL,
        max_tokens = VISION_MAX_TOKENS,
        messages   = [
            {
                "role": "user",
                "content": [
                    {
                        "type"  : "image",
                        "source": {
                            "type"      : "base64",
                            "media_type": "image/jpeg",
                            "data"      : base64_jpeg,
                        },
                    },
                    {
                        "type": "text",
                        "text": vision_prompt,
                    },
                ],
            }
        ],
    )

    scene_description : str = ""
    for content_block in response.content:
        if hasattr(content_block, "text"):
            scene_description = content_block.text
            break

    return scene_description.strip()
# end function describe_frame_with_vision


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR VI — Function: classify_event_with_llm
# GOAL  : Send scene description to Claude LLM for event classification.
# INPUT : scene_description (str), recent_events (list of last events)
# OUTPUT: Returns decision dict {action, label, summary, confidence, reasoning}
# STEPS :
#   1. Build system prompt with location context and decision rules.
#   2. Build user message with scene description + recent context.
#   3. Call LLM model.
#   4. Parse JSON response.
#   5. Return decision dict.
# ─────────────────────────────────────────────────────────────────────────────
def classify_event_with_llm(
    scene_description : str,
    recent_events     : List[Dict[str, Any]],
    claude_client     : anthropic.Anthropic,
) -> Dict[str, Any]:

    recent_text : str = ""
    if recent_events:
        recent_lines = [
            f"  [{e['time']}] {e['label']}: {e['summary'][:60]}"
            for e in recent_events[-5:]
        ]
        recent_text = "\n".join(recent_lines)
    else:
        recent_text = "  (no prior events this session)"

    system_prompt : str = f"""You are an estate intelligence agent monitoring: {LOCATION_LABEL}.
Context: {OWNER_CONTEXT}

CLASSIFY the camera event into one of these actions:
- "alert"    → person detected, unusual activity, or something requiring attention
- "log"      → normal environment, empty room, minor movement (shadows, light changes)
- "escalate" → potential intrusion, someone climbing, forced entry, aggression

RESPOND ONLY with valid JSON (no text outside):
{{
  "action"     : "alert" | "log" | "escalate",
  "label"      : "PERSON_DETECTED" | "EMPTY_SCENE" | "MOTION" | "UNUSUAL_ACTIVITY" | "INTRUSION_RISK",
  "summary"    : "one sentence max 80 chars",
  "confidence" : 0.0 to 1.0,
  "reasoning"  : "one sentence why"
}}"""

    current_time : str = datetime.now().strftime("%H:%M:%S")
    user_message : str = f"""TIME: {current_time}

CAMERA DESCRIPTION:
{scene_description}

RECENT EVENTS (last 5):
{recent_text}

Classify this event."""

    try:
        response = claude_client.messages.create(
            model      = CLAUDE_LLM_MODEL,
            max_tokens = DECISION_MAX_TOKENS,
            system     = system_prompt,
            messages   = [{"role": "user", "content": user_message}],
        )

        response_text : str = ""
        for content_block in response.content:
            if hasattr(content_block, "text"):
                response_text = content_block.text
                break

        decision : Dict[str, Any] = json.loads(response_text)
        return decision

    except json.JSONDecodeError:
        return {
            "action"    : "log",
            "label"     : "PARSE_ERROR",
            "summary"   : "LLM returned non-JSON response",
            "confidence": 0.0,
            "reasoning" : "JSON parse failed",
        }
    except Exception as api_error:
        return {
            "action"    : "log",
            "label"     : "API_ERROR",
            "summary"   : f"API error: {str(api_error)[:50]}",
            "confidence": 0.0,
            "reasoning" : str(api_error),
        }
# end function classify_event_with_llm


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR VI — Function: send_telegram_alert
# GOAL  : Send Telegram notification for alert/escalate events.
# INPUT : message (str), label (str)
# OUTPUT: Returns True (bool) if sent, False otherwise.
# STEPS :
#   1. Check tokens are set — return False if not.
#   2. Build Telegram message with emoji and label.
#   3. POST to Telegram Bot API.
#   4. Return success bool.
# ─────────────────────────────────────────────────────────────────────────────
def send_telegram_alert(message: str, label: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    import urllib.request

    emoji_map : Dict[str, str] = {
        "PERSON_DETECTED" : "👤",
        "UNUSUAL_ACTIVITY": "⚠️",
        "INTRUSION_RISK"  : "🚨",
        "MOTION"          : "🏃",
    }
    emoji      : str = emoji_map.get(label, "📷")
    full_text  : str = f"{emoji} *Camera Alert — {label}*\n\n{message}\n\n_{datetime.now().strftime('%H:%M:%S')} · Estate Intelligence_"

    payload_bytes : bytes = json.dumps({
        "chat_id"   : TELEGRAM_CHAT_ID,
        "text"      : full_text,
        "parse_mode": "Markdown",
    }).encode("utf-8")

    url     : str = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    request = urllib.request.Request(url, data=payload_bytes, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(request, timeout=8) as resp:
            result = json.loads(resp.read().decode())
            return result.get("ok", False)
    except Exception:
        return False
# end function send_telegram_alert


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR VI — Function: print_event
# GOAL  : Print a formatted event line to the terminal dashboard.
# INPUT : event (dict — event record)
# OUTPUT: Prints one formatted line to stdout.
# STEPS :
#   1. Choose colour based on event action.
#   2. Format timestamp, label, confidence, summary.
#   3. Print coloured line.
# ─────────────────────────────────────────────────────────────────────────────
def print_event(event: Dict[str, Any]) -> None:
    action     : str   = event.get("action", "log")
    label      : str   = event.get("label", "UNKNOWN")
    summary    : str   = event.get("summary", "")
    confidence : float = event.get("confidence", 0.0)
    timestamp  : str   = event.get("time", "??:??:??")
    motion_pct : float = event.get("motion_pct", 0.0)

    colour_map : Dict[str, str] = {
        "escalate": C.RED,
        "alert"   : C.YELLOW,
        "log"     : C.DIM,
    }
    colour : str = colour_map.get(action, C.WHITE)

    prefix_map : Dict[str, str] = {
        "escalate": "🚨",
        "alert"   : "👤",
        "log"     : "·",
    }
    prefix : str = prefix_map.get(action, "·")

    conf_bar : str = "█" * int(confidence * 10) + "░" * (10 - int(confidence * 10))

    print(
        f"{colour}{prefix} [{timestamp}] "
        f"{C.BOLD}{label:<20}{C.RESET}{colour} "
        f"conf:[{conf_bar}] {confidence:.0%}  "
        f"motion:{motion_pct:.1%}  "
        f"{summary[:70]}{C.RESET}"
    )
# end function print_event


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR VI — Function: print_dashboard_header
# GOAL  : Print the static dashboard header and stats bar.
# INPUT : None
# OUTPUT: Prints header to stdout.
# ─────────────────────────────────────────────────────────────────────────────
def print_dashboard_header() -> None:
    uptime_sec : float = time.time() - start_time
    uptime_str : str   = time.strftime("%H:%M:%S", time.gmtime(uptime_sec))
    alerts     : int   = sum(1 for e in event_log if e.get("action") in ("alert", "escalate"))

    print(f"\n{C.GOLD}{'═' * 70}{C.RESET}")
    print(f"{C.GOLD}{C.BOLD}  🏡 ESTATE INTELLIGENCE — LIVE CAMERA AGENT{C.RESET}")
    print(f"{C.GOLD}{'─' * 70}{C.RESET}")
    print(
        f"  Camera: {C.CYAN}device {CAMERA_DEVICE_INDEX}{C.RESET}  "
        f"│  Location: {C.CYAN}{LOCATION_LABEL}{C.RESET}  "
        f"│  Uptime: {C.CYAN}{uptime_str}{C.RESET}"
    )
    print(
        f"  Events: {C.WHITE}{len(event_log)}{C.RESET}  "
        f"│  Alerts: {C.YELLOW}{alerts}{C.RESET}  "
        f"│  Frames: {C.WHITE}{frames_read}{C.RESET}  "
        f"│  Cooldown: {C.WHITE}{CAPTURE_COOLDOWN_SEC}s{C.RESET}  "
        f"│  Motion threshold: {C.WHITE}{MOTION_THRESHOLD:.1%}{C.RESET}"
    )
    print(f"{C.GOLD}{'─' * 70}{C.RESET}")
    print(f"  {C.DIM}Ctrl+C to stop  │  🟡 log  🟠 alert  🔴 escalate{C.RESET}")
    print(f"{C.GOLD}{'═' * 70}{C.RESET}\n")
# end function print_dashboard_header


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR VI — Function: process_motion_frame
# GOAL  : Full pipeline for one motion-triggered frame.
# INPUT : frame (ndarray), motion_score (float), claude_client (Anthropic)
# OUTPUT: Appends event to event_log, prints to dashboard.
# STEPS :
#   1. Encode frame to JPEG base64.
#   2. Call Claude Vision → scene description.
#   3. Call Claude LLM → decision dict.
#   4. Build event record.
#   5. Print event to dashboard.
#   6. If action is alert/escalate: send Telegram if configured.
#   7. Append to event_log.
# ─────────────────────────────────────────────────────────────────────────────
def process_motion_frame(
    frame        : np.ndarray,
    motion_score : float,
    claude_client: anthropic.Anthropic,
) -> None:
    timestamp : str = datetime.now().strftime("%H:%M:%S")
    print(f"{C.CYAN}  ⟳ Motion detected ({motion_score:.1%}) — analysing frame…{C.RESET}", end="\r")

    # Step 1 — encode frame
    try:
        base64_jpeg : str = encode_frame_jpeg(frame)
    except ValueError as encode_error:
        print(f"{C.RED}  ✗ Frame encode failed: {encode_error}{C.RESET}")
        return

    # Step 2 — vision description
    try:
        scene_description : str = describe_frame_with_vision(base64_jpeg, claude_client)
    except Exception as vision_error:
        scene_description = f"[Vision API error: {str(vision_error)[:60]}]"
        print(f"{C.RED}  ✗ Vision API error: {vision_error}{C.RESET}")

    # Step 3 — LLM classification
    decision : Dict[str, Any] = classify_event_with_llm(scene_description, event_log, claude_client)

    # Step 4 — build event record
    event_record : Dict[str, Any] = {
        "time"       : timestamp,
        "action"     : decision.get("action", "log"),
        "label"      : decision.get("label", "UNKNOWN"),
        "summary"    : decision.get("summary", ""),
        "description": scene_description,
        "confidence" : decision.get("confidence", 0.0),
        "reasoning"  : decision.get("reasoning", ""),
        "motion_pct" : motion_score,
    }

    # Step 5 — print to dashboard
    print_event(event_record)

    # Step 6 — Telegram if needed
    action : str = event_record["action"]
    if action in ("alert", "escalate") and TELEGRAM_BOT_TOKEN:
        telegram_text : str = f"{scene_description}\n\nReasoning: {decision.get('reasoning', '')}"
        sent           : bool = send_telegram_alert(telegram_text, event_record["label"])
        if sent:
            print(f"  {C.GREEN}  ↗ Telegram alert sent{C.RESET}")

    # Step 7 — append to log
    event_log.append(event_record)
# end function process_motion_frame


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR VI — Function: graceful_shutdown
# GOAL  : Handle Ctrl+C / SIGTERM — print summary and exit cleanly.
# INPUT : signum (int), frame (stack frame)
# OUTPUT: Prints session summary, exits with code 0.
# ─────────────────────────────────────────────────────────────────────────────
def graceful_shutdown(signum: int, frame: Any) -> None:
    global running_flag
    running_flag = False

    print(f"\n{C.GOLD}\n{'═' * 70}")
    print(f"  SESSION SUMMARY")
    print(f"{'─' * 70}{C.RESET}")

    uptime_sec : float = time.time() - start_time
    uptime_str : str   = time.strftime("%H:%M:%S", time.gmtime(uptime_sec))
    alerts     : int   = sum(1 for e in event_log if e.get("action") == "alert")
    escalated  : int   = sum(1 for e in event_log if e.get("action") == "escalate")

    print(f"  Uptime          : {uptime_str}")
    print(f"  Frames processed: {frames_read}")
    print(f"  Events captured : {len(event_log)}")
    print(f"  Alerts          : {C.YELLOW}{alerts}{C.RESET}")
    print(f"  Escalations     : {C.RED}{escalated}{C.RESET}")

    if event_log:
        print(f"\n{C.GOLD}  Last 5 events:{C.RESET}")
        for event in event_log[-5:]:
            print(f"  [{event['time']}] {event['label']}: {event['summary']}")

    print(f"\n{C.GOLD}{'═' * 70}{C.RESET}")
    print(f"{C.GREEN}  Agent stopped cleanly.{C.RESET}\n")
    sys.exit(0)
# end function graceful_shutdown


# ─────────────────────────────────────────────────────────────────────────────
# PILLAR VI — Function: main
# GOAL  : Entry point — initialise and run the capture loop.
# INPUT : None
# OUTPUT: Never returns (runs until Ctrl+C).
# STEPS :
#   1. Validate ANTHROPIC_API_KEY is set.
#   2. Open camera.
#   3. Initialise Anthropic client.
#   4. Register SIGINT/SIGTERM handlers.
#   5. Print dashboard header.
#   6. Capture baseline frame.
#   7. Enter main capture loop (PILLAR V — keepGoing sentry).
#   8. On each iteration:
#      a. Read frame.
#      b. Compute motion vs baseline.
#      c. If motion > threshold AND cooldown elapsed: call process_motion_frame().
#      d. Update baseline every 10 frames (slow drift correction).
#      e. Optional: show preview window.
#      f. Sleep FRAME_SLEEP_SEC.
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    global running_flag, frames_read, last_capture_ts

    # ── Validate API key ─────────────────────────────────────────────────────
    if not ANTHROPIC_API_KEY:
        print(f"{C.RED}[ERROR] ANTHROPIC_API_KEY not set in environment.{C.RESET}")
        print(f"        Create a .env file or export the variable:")
        print(f"        {C.CYAN}export ANTHROPIC_API_KEY=sk-ant-...{C.RESET}")
        sys.exit(1)

    # ── Open camera ──────────────────────────────────────────────────────────
    try:
        camera : cv2.VideoCapture = open_camera(CAMERA_DEVICE_INDEX)
    except RuntimeError as camera_error:
        print(f"{C.RED}[ERROR] {camera_error}{C.RESET}")
        sys.exit(1)

    # ── Initialise Anthropic client ──────────────────────────────────────────
    claude_client : anthropic.Anthropic = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    print(f"{C.GREEN}✓ Claude API client ready{C.RESET} — vision: {CLAUDE_VISION_MODEL} · LLM: {CLAUDE_LLM_MODEL}")

    # ── Signal handlers ───────────────────────────────────────────────────────
    signal.signal(signal.SIGINT,  graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    # ── Dashboard header ─────────────────────────────────────────────────────
    print_dashboard_header()

    # ── Baseline frame ────────────────────────────────────────────────────────
    success, baseline_frame = camera.read()
    if not success or baseline_frame is None:
        print(f"{C.RED}[ERROR] Could not read baseline frame from camera.{C.RESET}")
        camera.release()
        sys.exit(1)

    print(f"{C.DIM}  Monitoring started — move in front of camera to trigger analysis.{C.RESET}\n")

    baseline_update_counter : int = 0   # Integer — tracks frames since last baseline update

    # ── Main capture loop (PILLAR V — keepGoing sentry) ──────────────────────
    while running_flag:
        success, current_frame = camera.read()

        if not success or current_frame is None:
            print(f"{C.YELLOW}  ⚠ Camera read failed — retrying...{C.RESET}")
            time.sleep(0.5)
            continue

        frames_read += 1

        # ── Motion detection ─────────────────────────────────────────────────
        motion_score  : float = compute_motion_score(baseline_frame, current_frame)
        current_time  : float = time.time()
        cooldown_ok   : bool  = (current_time - last_capture_ts) >= CAPTURE_COOLDOWN_SEC
        motion_trigger: bool  = motion_score > MOTION_THRESHOLD

        if motion_trigger and cooldown_ok:
            last_capture_ts = current_time
            process_motion_frame(current_frame, motion_score, claude_client)
            baseline_frame         = current_frame.copy()
            baseline_update_counter = 0

        # ── Slow baseline drift correction (every 30 quiet frames) ───────────
        if not motion_trigger:
            baseline_update_counter += 1
            if baseline_update_counter >= 30:
                # Blend 90% old baseline + 10% current (smooth lighting drift)
                baseline_frame          = cv2.addWeighted(baseline_frame, 0.9, current_frame, 0.1, 0)
                baseline_update_counter = 0

        # ── Optional preview window ───────────────────────────────────────────
        if SHOW_PREVIEW:
            preview_label : str = f"Motion: {motion_score:.1%} | Events: {len(event_log)}"
            cv2.putText(current_frame, preview_label, (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.imshow("Estate Intelligence — Camera Feed", current_frame)

            key : int = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break

        time.sleep(FRAME_SLEEP_SEC)
    # end while running_flag

    camera.release()
    if SHOW_PREVIEW:
        cv2.destroyAllWindows()
# end function main


if __name__ == "__main__":
    main()
