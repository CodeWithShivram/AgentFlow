# Sandbox image for the node-24-typescript stack profile (ADR-0048, ADR-0050).
# Generated code runs here offline (--network none): every package it may use is preinstalled
# from the committed lockfile, with install scripts disabled.
FROM node:24-slim@sha256:873f586f3794f52281bd039bf02c048a0be08fde619338f9941bad1eb6b34530

ENV NODE_ENV=development \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    PATH=/opt/deps/node_modules/.bin:$PATH \
    NODE_PATH=/opt/deps/node_modules

COPY package.json package-lock.json /opt/deps/
RUN cd /opt/deps \
    && npm ci --ignore-scripts --no-audit --no-fund \
    && npm cache clean --force \
    && useradd --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin sandbox

USER 10001:10001
WORKDIR /work
