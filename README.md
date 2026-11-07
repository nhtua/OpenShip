# OpenShip

[![Backend CI](https://img.shields.io/github/actions/workflow/status/nhtua/OpenShip/python-ci.yml?branch=main&style=flat&label=backend%20CI)](https://github.com/nhtua/OpenShip/actions/workflows/python-ci.yml)
[![Frontend CI](https://img.shields.io/github/actions/workflow/status/nhtua/OpenShip/frontend-ci.yml?branch=main&style=flat&label=frontend%20CI)](https://github.com/nhtua/OpenShip/actions/workflows/frontend-ci.yml)
[![License](https://img.shields.io/github/license/nhtua/OpenShip?style=flat)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/nhtua/OpenShip?style=flat)](https://github.com/nhtua/OpenShip/stargazers)
[![Last commit](https://img.shields.io/github/last-commit/nhtua/OpenShip/main?style=flat)](https://github.com/nhtua/OpenShip/commits/main/)

**A self-hosted DevOps co-pilot in your browser.** Chat with a configured model,
search for information, inspect AWS resources, and work with files in one
project workspace. Follow progress as work runs and return to conversations
without losing their history or results.

OpenShip is under active development. You host the application and its state;
model providers and integrations receive the context needed for their requests.

## Features available today

- **Project-based chat:** authenticated workspaces, streaming responses,
  conversation history, and project/conversation archiving.
- **Durable runs:** live progress, cancellation, and recovery of recorded state
  after reconnects or restarts, backed by PostgreSQL and NATS JetStream.
- **Model profiles:** choose explicitly configured providers and models. See the
  [provider support matrix](docs/provider-support.md) for capabilities and tested combinations.
- **Search and Connectors:** managed Brave/Tavily credentials, project defaults,
  and operator-controlled tool availability.
- **Read-only AWS tools:** role-based discovery and bounded resource reads.
- **Conversation files:** upload, preview, download, and delete retained artifacts.
- **Python execution (opt-in):** chat-directed Python in OpenSandbox with retained
  outputs, including charts when a plotting image is configured. This is an
  experimental lane; see [setup and limitations](docs/operations/opensandbox.md).

See the [roadmap](docs/roadmap.md) for delivery status and future features,
including reusable workflows, approvals, event-driven automation, and
infrastructure changes. Those are not all available today.

## Quick start

Use the Compose development stack for your first checkout. It runs the app and
its dependencies with source hot reload; you do not need to install PostgreSQL,
Node.js, or the Python application dependencies on your host.

### 1. Prepare your machine

On **Arch Linux and derivatives**, use the Ansible bootstrap to install and
configure Docker, Compose, and gVisor sandbox dependencies, as your normal user:

```bash
./scripts/bootstrap/bootstrap.sh arch-docker --verify
```

Review the [bootstrap guide](scripts/bootstrap/README.md) first: this uses sudo,
may upgrade host packages, and requires an existing AUR helper for gVisor.
For other Linux distributions, install Python 3, Docker with Compose **2.24+**,
and the sandbox runtime dependencies yourself; other Ansible targets are not yet
provided. See [host setup and engine alternatives](docs/development.md#host-setup).

### 2. Configure your installation

From the repository root:

```bash
cp .env.example .env
```

**Edit `.env` before continuing.** The stack does not choose or generate these values:

| Settings | What you need to supply |
| --- | --- |
| `JWT_SECRET` | A long random signing secret; replace the example. |
| `ARTIFACT_STORAGE_ID` | A new UUID for this installation. Keep it unchanged across restarts and restores. |
| `MODEL_PROFILES_JSON`, `MODEL_DEFAULT_PROFILE_ID` | A model/endpoint you can access and its matching default profile ID. Review the supplied examples using the [provider guide](docs/provider-support.md). |
| `OPENAI_API_KEY` (or your profile's credential binding) | Your model provider key. The supplied profiles use `OPENAI_API_KEY`. |
| `OPENSHIP_SANDBOX_SERVER_IMAGE`, `OPENSHIP_SANDBOX_GATEWAY_IMAGE` | The pinned provider images from [`deploy/sandbox/images.json`](deploy/sandbox/images.json). |
| `SANDBOX_ENABLED`, `SANDBOX_IMAGE` | Set `true` and the workload image you build with the [sandbox image setup](docs/development.md#sandbox-images-for-dev). Leave the standard `python311` / `stdlib` identity for that image. |

For global tool management, set `CONNECTOR_OPERATOR_USERNAME` to the account you
will register. Search/AWS Connectors also need a stable `CONNECTOR_INSTALLATION_ID`
UUID and [one-time keyring setup](docs/development.md#optional-connector-setup).
These are optional for plain chat; the stack does not create Connector keyrings.

Set `DEV_BIND_ADDRESS=127.0.0.1` for workstation-only access; the example otherwise
exposes the development UI and API to your LAN. Keep `.env` and generated secrets
out of Git.

### 3. Initialize once, then start

Complete the [sandbox image setup](docs/development.md#sandbox-images-for-dev)
before the first start, then run:

```bash
./scripts/openship-stack -e dev init-messaging
./scripts/openship-stack -e dev init-sandbox
./scripts/openship-stack -e dev init-artifacts
./scripts/openship-stack -e dev --sandbox start
```

On first startup, the command may stop and print a **Cutover ID**. Review the
preview, ensure no other API/worker/relay processes use this database, then run
these commands with that ID:

```bash
./scripts/openship-stack -e dev --sandbox start --cutover <printed-cutover-id>
./scripts/openship-stack -e dev --sandbox start
```

Open **[http://localhost:5173](http://localhost:5173)**, sign up, and start a chat.
For tools, configure Connectors and enable installed tools in **Global Settings**.
API documentation is at [http://localhost:8000/docs](http://localhost:8000/docs).

For later sessions:

```bash
./scripts/openship-stack -e dev --sandbox start
./scripts/openship-stack -e dev --sandbox status
./scripts/openship-stack -e dev --sandbox stop
```

This is a development setup, not a production deployment or security
qualification. For startup failures and migration/recovery procedures, use the
[messaging runbook](docs/operations/messaging.md) and
[sandbox troubleshooting guide](docs/operations/opensandbox.md#failure-signatures-on-the-dev-stack).

## Documentation

- [Development guide](docs/development.md) — custom/native setup, engine alternatives, tests, and pre-commit hooks.
- [Architecture](docs/architects.md) — how the web client, API, workers, and durable state fit together.
- [Roadmap](docs/roadmap.md) — delivered milestones and planned capabilities.
- [Provider support](docs/provider-support.md) — model configuration and qualification evidence.
- [Connector operations](docs/operations/managed-connectors.md) and [tool availability](docs/operations/tool-availability.md) — credentials and tool controls.
- [Artifact storage](docs/operations/artifact-storage.md) — retention, backup, and restore.
- [Messaging operations](docs/operations/messaging.md) and [OpenSandbox](docs/operations/opensandbox.md) — detailed deployment and recovery.

## Contributing

Issues, feedback, documentation improvements, and pull requests are welcome.
Start with the [development guide](docs/development.md); discuss new capabilities
against the [roadmap](docs/roadmap.md) and [ideas collection](docs/ideas.md).

Application code lives in [`apps/api`](apps/api) (FastAPI/LangGraph) and
[`apps/web`](apps/web) (Vue/TypeScript). The experiments under [`poc/`](poc/README.md)
are historical prototypes, not the supported application entry point.

## License

MIT — see [LICENSE](LICENSE).
