# RG10 harness sandbox image (Freeze Decision 1, Option B; section 9 env_fingerprint).
# Python 3.11 and a pinned pytest; nothing else. The harness records the built image's
# digest in every bundle, so a rebuild that changes anything gives a new fingerprint.
# Projects whose own tests need third-party packages need those packages added here,
# pinned, before the bundle is assembled; one image serves the whole bundle.
FROM python:3.11.11-slim-bookworm
RUN python -m pip install --no-cache-dir "pytest==8.3.3"
USER 65534:65534
WORKDIR /tmp
