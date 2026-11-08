# Standard workload over the digest-pinned shared base. Pinned Node.js archive
# URL/checksum arrive as build arguments from the projected tool manifest and
# the workload Python requirements install from the committed hash-locked
# requirements.lock (uv verifies every digest before installing): no runtime
# downloads and no package installation at smoke time.
ARG WORKLOAD_BASE_IMAGE
FROM ${WORKLOAD_BASE_IMAGE}

ARG NODE_URL
ARG NODE_SHA256

COPY deploy/sandbox/profiles/standard/install.sh /tmp/install.sh
COPY deploy/sandbox/profiles/standard/requirements.lock /tmp/requirements.lock

RUN sh /tmp/install.sh \
    && install -d --owner=openship --group=openship --mode=0700 /opt/openship/tmp \
    && uv pip sync --python /usr/local/bin/python3 --link-mode=copy /tmp/requirements.lock \
    && rm -f /tmp/install.sh /tmp/requirements.lock

ENV HOME=/opt/openship/tmp \
    PATH="/opt/node/current/bin:${PATH}"
WORKDIR /code
COPY deploy/sandbox/profiles/standard/smoke.py /opt/openship/smoke.py
