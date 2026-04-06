#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# run_camera_agent.sh — Quick launcher for the laptop camera agent
# Usage: ./run_camera_agent.sh [--test] [--preview]
#
# Options:
#   --test      Run the test suite instead of the live agent
#   --preview   Show live camera preview window (requires display)
#   --help      Show this help
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; GOLD='\033[0;33m'; RESET='\033[0m'; BOLD='\033[1m'

echo -e "\n${GOLD}${BOLD}🏡 Estate Intelligence — Camera Agent Launcher${RESET}"
echo -e "${GOLD}$(printf '─%.0s' {1..50})${RESET}\n"

# ── Parse args ────────────────────────────────────────────────────────────────
RUN_TEST=false
SHOW_PREVIEW=false

for arg in "$@"; do
  case $arg in
    --test)    RUN_TEST=true ;;
    --preview) SHOW_PREVIEW=true ;;
    --help)
      echo "Usage: ./run_camera_agent.sh [--test] [--preview]"
      echo "  --test     Run the test suite"
      echo "  --preview  Show live camera window"
      exit 0 ;;
    *) echo -e "${RED}Unknown argument: $arg${RESET}"; exit 1 ;;
  esac
done

# ── Load .env if present ──────────────────────────────────────────────────────
if [[ -f ".env" ]]; then
  echo -e "${GREEN}✓ Loading .env${RESET}"
  set -o allexport
  source .env
  set +o allexport
else
  echo -e "${YELLOW}⚠ No .env file found — using exported environment variables${RESET}"
fi

# ── Validate API key ──────────────────────────────────────────────────────────
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo -e "${RED}✗ ANTHROPIC_API_KEY is not set.${RESET}"
  echo -e "  Create a .env file with:"
  echo -e "  ${CYAN}ANTHROPIC_API_KEY=sk-ant-...${RESET}"
  exit 1
fi
echo -e "${GREEN}✓ ANTHROPIC_API_KEY found${RESET}"

# ── Check Python ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo -e "${RED}✗ python3 not found${RESET}"
  exit 1
fi
PYTHON_VER=$(python3 --version 2>&1)
echo -e "${GREEN}✓ ${PYTHON_VER}${RESET}"

# ── Install dependencies ──────────────────────────────────────────────────────
echo -e "\n${CYAN}Installing dependencies…${RESET}"
pip install opencv-python numpy anthropic python-dotenv --break-system-packages -q 2>&1 | tail -3 || true
echo -e "${GREEN}✓ Dependencies ready${RESET}\n"

# ── Run ───────────────────────────────────────────────────────────────────────
if [[ "$SHOW_PREVIEW" == "true" ]]; then
  export SHOW_PREVIEW=true
fi

if [[ "$RUN_TEST" == "true" ]]; then
  echo -e "${GOLD}Running test suite…${RESET}\n"
  python3 tests/test_camera_agent.py
else
  echo -e "${GOLD}Starting live camera agent…${RESET}"
  echo -e "${YELLOW}Press Ctrl+C to stop.${RESET}\n"
  python3 src/laptop_camera_agent.py
fi
