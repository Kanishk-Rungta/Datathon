#!/usr/bin/env bash
# =====================================================================
# GPU Launch Script for AI4Bharat Speech Service
#
# Configures CUDA acceleration environment and launches FastAPI worker
# with Uvicorn on port 9100.
# =====================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== KSP-CIP AI4Bharat Speech Service GPU Launcher ==="

# Check Python environment
if [ -d ".venv" ]; then
    echo "Activating virtual environment (.venv)..."
    source .venv/bin/activate
fi

# Set default GPU & CUDA environment variables
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export SPEECH_ASR_MODEL="${SPEECH_ASR_MODEL:-ai4bharat/indic-conformer-600m-multilingual}"
export SPEECH_TTS_MODEL="${SPEECH_TTS_MODEL:-ai4bharat/indic-parler-tts}"
export SPEECH_NMT_EN_INDIC="${SPEECH_NMT_EN_INDIC:-ai4bharat/indictrans2-en-indic-dist-200M}"
export SPEECH_NMT_INDIC_EN="${SPEECH_NMT_INDIC_EN:-ai4bharat/indictrans2-indic-en-dist-200M}"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-9100}"

echo "Checking PyTorch & CUDA availability..."
python -c "
import torch
print(f'PyTorch version : {torch.__version__}')
print(f'CUDA available  : {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'GPU Device Name : {torch.cuda.get_device_name(0)}')
    print(f'VRAM Total (MB) : {torch.cuda.get_device_properties(0).total_memory // (1024*1024)}')
else:
    print('WARNING: CUDA is not available. Speech service will run on CPU.')
"

echo "Starting speech service on http://${HOST}:${PORT}..."
exec uvicorn app:app --host "$HOST" --port "$PORT" --workers 1
