# Default Runpod Serverless worker image (structRFM).
#
# Lives at the repo ROOT, named literally "Dockerfile", because Runpod's GitHub
# integration auto-detects a root Dockerfile. In the Serverless "Import Git
# Repository" flow leave the Dockerfile Path EMPTY (root).
#
# Runs REAL structRFM via its programmatic API (structRFM.infer.structRFM_infer) —
# NOT the upstream SSP script (its extra imports param_turner2004/utils aren't in
# the documented install). worker/run_structrfm.py loads the BASE checkpoint baked
# in here and returns the last-layer attention map (L×L). Without the checkpoint
# the runner falls back to a clearly-marked stub (CPU/CI only).
#
# Upstream: https://github.com/heqin-zhu/structRFM  (MIT License)
# Base checkpoint: structRFM_checkpoint.tar.gz  (GitHub release asset, HF model dir)
#
# (gRNAde's separate stub worker image is worker/Dockerfile.)

# torch==2.0.1 per upstream; CUDA 11.7 runtime matches that build.
FROM pytorch/pytorch:2.0.1-cuda11.7-cudnn8-runtime

# TODO(M1): replace 'latest' with a pinned release tag + verified sha256, and pin
# any version drift, so the image is reproducible.
ARG STRUCTRFM_RELEASE=latest
ARG STRUCTRFM_CKPT_SHA256=REPLACE_WITH_VERIFIED_SHA256

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKER_WORK_ROOT=/var/lib/sciable/jobs \
    STRUCTRFM_CHECKPOINT=/opt/structRFM_ckpt

WORKDIR /opt/worker

COPY worker/requirements-structrfm.txt ./worker/requirements-structrfm.txt
RUN pip install -r worker/requirements-structrfm.txt

# Download + extract the base checkpoint (an HF model dir) and symlink the dir that
# actually contains config.json to the stable STRUCTRFM_CHECKPOINT path.
RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && wget -O /tmp/ckpt.tar.gz \
        "https://github.com/heqin-zhu/structRFM/releases/${STRUCTRFM_RELEASE}/download/structRFM_checkpoint.tar.gz" \
    && if [ "${STRUCTRFM_CKPT_SHA256}" = "REPLACE_WITH_VERIFIED_SHA256" ]; then \
         echo "WARNING: checkpoint sha256 not pinned — skipping verification (dev build)."; \
         sha256sum /tmp/ckpt.tar.gz; \
       else \
         echo "${STRUCTRFM_CKPT_SHA256}  /tmp/ckpt.tar.gz" | sha256sum -c -; \
       fi \
    && mkdir -p /opt/ckpt_raw && tar -xzf /tmp/ckpt.tar.gz -C /opt/ckpt_raw && rm /tmp/ckpt.tar.gz \
    && CFG="$(find /opt/ckpt_raw -name config.json | head -1)" \
    && if [ -z "$CFG" ]; then echo "ERROR: no config.json in checkpoint archive"; ls -R /opt/ckpt_raw; exit 1; fi \
    && ln -s "$(dirname "$CFG")" /opt/structRFM_ckpt \
    && echo "checkpoint dir: $(readlink -f /opt/structRFM_ckpt)"

COPY worker/ /opt/worker/worker/
COPY handler.py /opt/worker/handler.py

# Non-root; writable per-job root owned by the run user.
RUN useradd --create-home --uid 10001 worker \
    && mkdir -p /var/lib/sciable/jobs \
    && chown -R worker:worker /var/lib/sciable /opt/ckpt_raw
USER worker

# Runpod Serverless entrypoint: root handler.py with a module-level
# runpod.serverless.start() — the form Runpod's handler detection expects.
CMD ["python", "-u", "handler.py"]
