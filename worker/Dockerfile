# gRNAde Runpod Serverless worker.
#
# This image is RUNNABLE NOW with the stub runner (run_grnade.py), which proves
# the full Runpod path end to end — submit/status/cancel, billing, isolation —
# while producing placeholder designs. Swapping in real gRNAde is M1:
#   - change the base image to a CUDA image matching gRNAde's torch build,
#   - add gRNAde's pinned requirements to worker/requirements.txt,
#   - bake in / fetch + checksum the approved checkpoint,
#   - replace run_grnade.py's _design() with the real inference call.
#
# Build for the Runpod GPU hosts' architecture (linux/amd64), e.g.:
#   docker build --platform linux/amd64 -f worker/Dockerfile -t <registry>/grnade-worker:<tag> .
#   docker push <registry>/grnade-worker:<tag>
# Record the pushed image digest in methods/grnade/manifest.json (worker_image_digest).

# TODO(M1): pin to a CUDA base by digest once gRNAde's torch/CUDA versions are known.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WORKER_WORK_ROOT=/var/lib/sciable/jobs

WORKDIR /opt/worker

COPY worker/requirements.txt ./worker/requirements.txt
RUN pip install -r worker/requirements.txt

COPY worker/ /opt/worker/worker/

# Non-root; writable per-job root owned by the run user.
RUN useradd --create-home --uid 10001 grnade \
    && mkdir -p /var/lib/grnade/jobs \
    && chown -R grnade:grnade /var/lib/grnade
USER grnade

# Runpod Serverless invokes the handler via this entrypoint.
CMD ["python", "-m", "worker.serverless_entry"]
