#!/usr/bin/env bash
set -e

export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export FLASHINFER_DISABLE_VERSION_CHECK=1
export VLLM_ATTENTION_BACKEND=FLASHINFER

# Path to autotuned GEMM kernel configurations
TUNED_CONFIG_DIR="./tuned_configs/qwen3.8"
[ -d "$TUNED_CONFIG_DIR" ] && export VLLM_TUNED_CONFIG_PATH="$TUNED_CONFIG_DIR"

exec vllm serve Qwen/Qwen3.8-27B-FP8 \
  --tensor-parallel-size 1 \
  --max-model-len 65536 \
  --max-num-seqs 128 \
  --gpu-memory-utilization 0.95 \
  --kv-cache-dtype fp8 \
  --mamba-ssm-cache-dtype float16 \
  --enable-chunked-prefill \
  --enable-prefix-caching \
  --mamba-cache-mode align \
  --max-num-batched-tokens 2048 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 \
  --language-model-only \
  --default-chat-template-kwargs '{"enable_thinking": true, "preserve_thinking": true}' \
  --override-generation-config '{"temperature": 1.0, "top_p": 0.95, "top_k": 20, "presence_penalty": 0.0, "repetition_penalty": 1.0}' \
  --speculative-config '{"method":"mtp","num_speculative_tokens":3,"draft_sample_method":"probabilistic"}' \
  --compilation-config '{"max_cudagraph_capture_size":64,"custom_ops":["+rms_norm","+silu_and_mul"]}'
