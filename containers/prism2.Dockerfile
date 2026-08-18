# PRISM2 slide encoder + Phi-3-mini decoder inference image
#
#   docker build -f containers/prism2.Dockerfile -t nf-prism2-prism2:1.0.0 containers
#
# flash-attn is installed from a PREBUILT wheel: a source build takes ~1 h and needs
# >16 GB RAM. The wheel must match torch version / CUDA major / cpython tag / C++ ABI.
# Check the ABI of the base image with:
#   python -c "import torch; print(torch.__version__, torch._C._GLIBCXX_USE_CXX11_ABI)"
# and pick the matching asset from https://github.com/Dao-AILab/flash-attention/releases
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel

ARG FLASH_ATTN_WHEEL=https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/flash_attn-2.7.4.post1+cu12torch2.6cxx11abiFALSE-cp311-cp311-linux_x86_64.whl

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        "transformers>=4.51,<5" \
        "huggingface_hub[cli]>=0.34" \
        accelerate \
        einops \
        safetensors \
        h5py \
        pyyaml \
        numpy

RUN pip install --no-cache-dir "${FLASH_ATTN_WHEEL}"

# Fail fast at build time instead of on an expensive GPU task
RUN python - <<'PY'
import flash_attn, torch, transformers
print("flash_attn", flash_attn.__version__, "torch", torch.__version__, "transformers", transformers.__version__)
PY

WORKDIR /work
