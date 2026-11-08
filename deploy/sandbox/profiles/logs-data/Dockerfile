ARG WORKLOAD_BASE_IMAGE
FROM ${WORKLOAD_BASE_IMAGE}

COPY deploy/sandbox/profiles/logs-data/requirements.lock /tmp/requirements.lock
RUN uv pip install --python /usr/local/bin/python3 --link-mode=copy \
        --require-hashes --no-deps --no-build-isolation -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock \
    && install -d --owner=openship --group=openship --mode=0700 /mpl /opt/openship/home
ENV MPLBACKEND=Agg MPLCONFIGDIR=/mpl HOME=/opt/openship/home \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
COPY deploy/sandbox/profiles/logs-data/smoke.py /opt/openship/smoke.py
WORKDIR /code
