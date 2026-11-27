# Sandbox image for the python-3.12-web stack profile (ADR-0048, ADR-0050, ADR-0057).
# A FastAPI backend with a static HTML/CSS/JS frontend. Generated code runs here offline
# (--network none): every package it may use, Node (for JavaScript syntax checks) and a headless
# Chromium (for the UI smoke test) are preinstalled. The platform's check scripts live in
# /opt/agentflow/checks and are never part of a project.
FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

COPY requirements.txt /opt/sandbox/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs libatomic1 \
    && pip install --no-cache-dir -r /opt/sandbox/requirements.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin sandbox

COPY checks/ /opt/agentflow/checks/
RUN chmod -R a+rX /opt/agentflow /opt/ms-playwright

USER 10001:10001
WORKDIR /work
