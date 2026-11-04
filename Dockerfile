# Default Runpod Serverless worker image (structRFM).
#
# Lives at the repo ROOT, named literally "Dockerfile", because Runpod's GitHub
# integration auto-detects a root Dockerfile. In the Serverless "Import Git
# Repository" flow leave the Dockerfile Path EMPTY (root) — do not point it at a
# custom path/name; that is what triggered "no Dockerfile found at that path".
#
# This image runs REAL structRFM inference: worker/run_structrfm.py invokes
# upstream's official SSP CLI (`scripts/structRFM_SSP.py --output_format dbn`)
# with a fine-tuned checkpoint, both baked in here. Without them the runner falls
# back to a clearly-marked stub (CPU/CI only).
#
# Upstream: https://github.com/heqin-zhu/structRFM  (MIT License)
# Checkpoint: structRFM_SSP_bpRNA1m.pt  (GitHub release asset)
#
# (gRNAde's separate stub worker image is worker/Dockerfile; per-method images
# are a follow-up once real gRNAde lands.)

# torch==2.0.1 per upstream; CUDA 11.7 runtime matches that build.
FROM pytorch/pytorch:2.0.1-cuda11.7-cudnn8-runtime

# TODO(M1): replace 'latest' with a pinned release tag + verified sha256 for the
# checkpoint, and pin STRUCTRFM_COMMIT to the exact upstream commit, so the image
# is reproducible (CLAUDE.md: "pinned-and-checksummed weights").
ARG STRUCTRFM_COMMIT=master
ARG STRUCTRFM_RELEASE=latest
ARG STRUCTRFM_SSP_SHA256=REPLACE_WITH_VERIFIED_SHA256

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKER_WORK_ROOT=/var/lib/sciable/jobs \
    STRUCTRFM_SSP_SCRIPT=/opt/structRFM/scripts/structRFM_SSP.py \
    STRUCTRFM_SSP_CHECKPOINT=/opt/structRFM/checkpoints/structRFM_SSP_bpRNA1m.pt \
    STRUCTRFM_GPU=0

WORKDIR /opt/worker

COPY worker/requirements-structrfm.txt ./worker/requirements-structrfm.txt
RUN pip install -r worker/requirements-structrfm.txt

# Upstream repo (for the SSP script) pinned to a commit, plus the checkpoint,
# downloaded and checksum-verified.
# NOTE on the asset URL: 'latest' uses .../releases/latest/download/<asset>; to pin
# a specific tag the form is .../releases/download/<tag>/<asset> (different shape).
RUN apt-get update && apt-get install -y --no-install-recommends git wget ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git clone https://github.com/heqin-zhu/structRFM.git /opt/structRFM \
    && git -C /opt/structRFM checkout "${STRUCTRFM_COMMIT}" \
    && mkdir -p /opt/structRFM/checkpoints \
    && wget -O /opt/structRFM/checkpoints/structRFM_SSP_bpRNA1m.pt \
        "https://github.com/heqin-zhu/structRFM/releases/${STRUCTRFM_RELEASE}/download/structRFM_SSP_bpRNA1m.pt" \
    && if [ "${STRUCTRFM_SSP_SHA256}" = "REPLACE_WITH_VERIFIED_SHA256" ]; then \
         echo "WARNING: checkpoint sha256 not pinned — skipping verification (dev build)."; \
         sha256sum /opt/structRFM/checkpoints/structRFM_SSP_bpRNA1m.pt; \
       else \
         echo "${STRUCTRFM_SSP_SHA256}  /opt/structRFM/checkpoints/structRFM_SSP_bpRNA1m.pt" | sha256sum -c -; \
       fi

COPY worker/ /opt/worker/worker/

# Non-root; writable per-job root owned by the run user.
RUN useradd --create-home --uid 10001 worker \
    && mkdir -p /var/lib/sciable/jobs \
    && chown -R worker:worker /var/lib/sciable /opt/structRFM
USER worker

# Runpod Serverless invokes the handler via this entrypoint (calls
# runpod.serverless.start() in worker/serverless_entry.py).
CMD ["python", "-m", "worker.serverless_entry"]
