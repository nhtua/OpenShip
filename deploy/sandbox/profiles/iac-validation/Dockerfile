ARG WORKLOAD_BASE_IMAGE
FROM ${WORKLOAD_BASE_IMAGE}
ARG TFLINT_URL
ARG TFLINT_SHA256
ARG DEBIAN_SNAPSHOT
COPY deploy/sandbox/profiles/iac-validation/os-packages.lock /tmp/os-packages.lock
COPY deploy/sandbox/profiles/iac-validation/install.sh /tmp/install.sh
COPY deploy/sandbox/profiles/iac-validation/requirements.lock /tmp/requirements.lock
RUN sh /tmp/install.sh \
    && uv pip install --python /usr/local/bin/python3 --link-mode=copy \
        --require-hashes --no-deps --no-build-isolation -r /tmp/requirements.lock \
    && rm -f /tmp/install.sh /tmp/requirements.lock /tmp/os-packages.lock \
    && install -d --owner=openship --group=openship --mode=0700 /opt/openship/home
ENV HOME=/opt/openship/home \
    BC_SKIP_MAPPING=TRUE CKV_SKIP_PACKAGE_UPDATE_CHECK=True DOWNLOAD_EXTERNAL_MODULES=false \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
COPY deploy/sandbox/profiles/iac-validation/smoke.py /opt/openship/smoke.py
WORKDIR /code
