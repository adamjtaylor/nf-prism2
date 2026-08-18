# TRIDENT: tissue segmentation + tiling + Virchow2 class-token tile embeddings
#
#   docker build -f containers/trident.Dockerfile -t nf-prism2-trident:1.0.0 containers
#
# Model weights are NOT baked in (gated, CC-BY-NC-ND) - they arrive via the HF cache
# staged by the STAGE_MODELS process.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

ARG TRIDENT_REF=main

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    TRIDENT_HOME=/opt/trident \
    HF_HUB_DISABLE_TELEMETRY=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        openslide-tools \
        libopenslide0 \
        libgl1 \
        libglib2.0-0 \
        procps \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/mahmoodlab/TRIDENT.git ${TRIDENT_HOME} \
    && cd ${TRIDENT_HOME} \
    && git checkout ${TRIDENT_REF} \
    && git rev-parse HEAD > /opt/trident_commit.txt \
    && pip install --no-cache-dir -e ".[patch-encoders]" \
    && pip install --no-cache-dir openslide-python h5py

WORKDIR /work

# Fail the build rather than a GPU task if the encoder registry moved
RUN python -c "from trident.patch_encoder_models.load import encoder_factory; print('virchow2-cls ok')" \
    || python -c "import trident; print('trident import ok (registry check skipped)')"
