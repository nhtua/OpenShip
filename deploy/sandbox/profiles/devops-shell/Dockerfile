ARG WORKLOAD_BASE_IMAGE
FROM ${WORKLOAD_BASE_IMAGE}
ARG AWS_URL
ARG AWS_SHA256
ARG AWS_VERSION
ARG GCLOUD_URL
ARG GCLOUD_SHA256
ARG GCLOUD_VERSION
ARG TERRAFORM_URL
ARG TERRAFORM_SHA256
ARG TERRAFORM_VERSION
ARG TERRAGRUNT_URL
ARG TERRAGRUNT_SHA256
ARG TERRAGRUNT_VERSION
ARG DEBIAN_SNAPSHOT

COPY deploy/sandbox/profiles/devops-shell/os-packages.lock /opt/openship/os-packages.lock
RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && echo "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} trixie main" > /etc/apt/sources.list \
    && apt-get update -qq \
    && grep -v '^#' /opt/openship/os-packages.lock | xargs apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/sandbox/profiles/devops-shell/install.py /tmp/install.py
RUN python /tmp/install.py && rm -f /tmp/install.py

COPY deploy/sandbox/build-requirements.lock /tmp/build-requirements.lock
COPY deploy/sandbox/profiles/devops-shell/azure-cli.lock /tmp/azure-cli.lock
COPY deploy/sandbox/profiles/devops-shell/requirements.lock /tmp/requirements.lock
RUN uv venv --python /usr/local/bin/python3 /opt/azure-cli \
    && uv pip install --python /opt/azure-cli/bin/python --link-mode=copy \
        --require-hashes --no-deps --only-binary :all: -r /tmp/build-requirements.lock \
    && uv pip install --python /opt/azure-cli/bin/python --link-mode=copy \
        --require-hashes --no-deps --no-build-isolation -r /tmp/azure-cli.lock \
    && uv pip install --python /usr/local/bin/python3 --link-mode=copy \
        --require-hashes --no-deps --no-build-isolation -r /tmp/requirements.lock \
    && rm -f /tmp/build-requirements.lock /tmp/azure-cli.lock /tmp/requirements.lock \
    && install -d --owner=openship --group=openship --mode=0700 /opt/openship/home
COPY deploy/sandbox/profiles/devops-shell/az /usr/local/bin/az
RUN chmod 0755 /usr/local/bin/az
ENV HOME=/opt/openship/home \
    PATH="/opt/gcloud/google-cloud-sdk/bin:${PATH}" \
    AZURE_CONFIG_DIR=/opt/openship/home/.azure AZURE_CORE_COLLECT_TELEMETRY=0 \
    AZURE_EXTENSION_USE_DYNAMIC_INSTALL=no \
    CLOUDSDK_CONFIG=/opt/openship/home/.gcloud CLOUDSDK_CORE_DISABLE_PROMPTS=1 \
    CLOUDSDK_CORE_DISABLE_USAGE_REPORTING=true CLOUDSDK_COMPONENT_MANAGER_DISABLE_UPDATE_CHECK=true \
    CHECKPOINT_DISABLE=1 TF_INPUT=0 AWS_EC2_METADATA_DISABLED=true AWS_CLI_AUTO_PROMPT=off AWS_PAGER=""
COPY deploy/sandbox/profiles/devops-shell/smoke.py /opt/openship/smoke.py
WORKDIR /code
