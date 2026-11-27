#!/usr/bin/env bash
# gpt-oss first (fast), then Gemma 4 (dense, slower), on the Mac Studio.
cd "$(dirname "$0")"
./run_mac.sh gpt-oss-120b-mxfp4-bf16 gptoss-medium --reasoning-effort medium
./run_mac.sh gemma-4-31b-it-8bit gemma-think
