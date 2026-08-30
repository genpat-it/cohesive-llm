# Complete Deployment & Optimization Guide: Qwen3.8-27B-FP8 on NVIDIA L40S (48GB)

This guide covers everything required to set up, tune, and serve `Qwen/Qwen3.8-27B-FP8` on an NVIDIA L40S (48GB) or compatible modern GPU from scratch.

---

## 1. Prerequisites & System Requirements

- **GPU**: 1x NVIDIA L40S (48GB GDDR6, Ada Lovelace SM89) or any GPU with Compute Capability $\ge 8.9$ (e.g. RTX 4090, H100, etc.).
- **OS**: Linux (Ubuntu 22.04 / RHEL 8+) with NVIDIA Driver $\ge 535$.
- **CUDA**: CUDA 12.1 or newer.
- **Python**: Python 3.10, 3.11, or 3.12.

---

## 2. Environment Installation

### Step 2.1: Create a Dedicated Virtual Environment
```bash
cd ~
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
```

### Step 2.2: Install PyTorch, vLLM, and FlashInfer
```bash
# Install PyTorch with CUDA 12 support
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# Install vLLM, FlashInfer, and Hugging Face dependencies
pip install vllm flashinfer -U
pip install "huggingface_hub[cli]" pydantic openai

# Alternatively, install via the included requirements file:
# pip install -r vllm_setup/vllm_requirements.txt
```

---

## 3. Download the Model

Download `Qwen/Qwen3.8-27B-FP8` using the Hugging Face CLI:

```bash
# (Optional) Export your Hugging Face Token if needed
export HF_TOKEN="your_hf_token_here"

# Download model snapshot with fast multi-threaded transfer
huggingface-cli download Qwen/Qwen3.8-27B-FP8 \
  --local-dir ./models/Qwen3.8-27B-FP8 \
  --local-dir-use-symlinks False
```

---

## 4. Install the Tuned FP8 Kernel Configurations

Tuned Triton kernel configurations ensure that matrix multiplication (GEMM) operations utilize optimal tile sizes (`BLOCK_SIZE_M`, `num_warps`, `stages`) on the L40S Tensor Cores.

### Using the Pre-Tuned Archive (`vllm_tuned_configs_l40s.tar.gz`)
```bash
# 1. Create the tuned configs directory
mkdir -p ./tuned_configs/qwen3.8

# 2. Extract into the local folder
tar -xzvf vllm_setup/vllm_tuned_configs_l40s.tar.gz -C ./tuned_configs/qwen3.8

# 3. Also extract directly into vLLM's internal package config path
VLLM_CONFIG_DIR=$(python -c "import vllm, os; print(os.path.join(os.path.dirname(vllm.__file__), 'model_executor/layers/quantization/utils/configs'))")
mkdir -p "$VLLM_CONFIG_DIR"
cp ./tuned_configs/qwen3.8/*.json "$VLLM_CONFIG_DIR/"

# Verify configs are in place
ls -lh "$VLLM_CONFIG_DIR" | grep NVIDIA_L40S
```

---

## 5. Production Launch Script (`serve_qwen3.8.sh`)

Create and run the server launch script:

```bash
#!/usr/bin/env bash
set -e

export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export FLASHINFER_DISABLE_VERSION_CHECK=1
export VLLM_ATTENTION_BACKEND=FLASHINFER

# Path to your autotuned GEMM kernel configurations
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
```

---

## 6. Expected Performance & Verification

With the tuned FP8 kernels, FlashInfer attention backend, and speculative decoding (MTP $k=3$) enabled on an NVIDIA L40S (48GB), you should expect:

- **Generation Throughput**: **~30 – 50 tokens/sec** during active autoregressive generation.
- **Prefix Cache Hit Rate**: $>70\%$ on multi-turn conversations due to automatic block-level prefix caching (`--enable-prefix-caching`).
- **Context Window**: 65,536 tokens with FP8 KV cache (`--kv-cache-dtype fp8`) utilizing under 44 GB VRAM.

### Health & Speed Verification:
Verify the server throughput using a quick completion query:
```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3.8-27B-FP8",
    "messages": [{"role": "user", "content": "Write a Nextflow DSL2 process template."}],
    "max_tokens": 256
  }'
```

---

## 7. Integration with Cohesive-LLM Backend

vLLM provides an **OpenAI-compatible** `/v1/chat/completions` API. Therefore, configure the backend `.env` with `LLM_PROVIDER=openai`:

```ini
# Use OpenAI provider adapter (vLLM natively implements the OpenAI API protocol)
LLM_PROVIDER=openai
LLM_MODEL=Qwen/Qwen3.8-27B-FP8

# Point to your local or remote vLLM instance URL
LOCAL_LLM_URL=http://localhost:8000/v1
OPENAI_API_KEY=dummy-key

# Context & Memory Settings
MAX_COMPLETION_TOKENS=16384
MEMORY_KEEP_LAST_N=60
```
