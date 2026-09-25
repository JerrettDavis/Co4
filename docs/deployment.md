# Deployment and operations

## Local installation

`python -m pip install .` installs the control plane and worker entry point. A built wheel is also included under `dist/`. Runtime dependency versions are pinned in `pyproject.toml`; installation requires access to an appropriate package index unless dependencies are already present. No hosted model or JavaScript build is needed to start the web application.

Use `co4 demo` only on loopback. It intentionally makes fixture identities available without GitHub authentication and uses a known demo key. A demo is not a shortcut to production configuration.

## Production process

Copy `.env.example` to a private configuration location and fill every required setting. `co4 serve` reads the process environment, not a `.env` file by itself. For a trusted, locally authored shell-compatible file:

```bash
set -a
. ./.env
set +a
co4 serve --host 127.0.0.1 --port 8080
```

Do not source files from untrusted repositories. A service manager or host secret/environment facility is preferable. Reverse proxy your configured HTTPS origin to the process. `deploy/Caddyfile` is an example. Preserve the incoming origin and arrange TLS termination correctly. Callback and webhook origins must exactly match `CO4_PUBLIC_URL`.

A long-running process with persistent storage is required. Ephemeral function hosting or a free service that sleeps unpredictably is not a suitable default for durable leases, webhook processing and background delivery. The core has no provider-specific API dependency beyond the integrations you configure; a small VM, container host, or local virtualized environment can run it. No provider price or free-tier availability is promised.

## Docker

The Dockerfile and Compose definitions are included but were **not built or launched** in the release environment because Docker was unavailable. Review them before deployment.

```bash
cp .env.example .env
mkdir -p .secrets
# Place the App PEM at .secrets/github-app.pem and fill .env.
docker compose up --build -d
```

Compose mounts a persistent SQLite volume at `/data`, uses a read-only root filesystem with a temporary `/tmp`, runs the service as UID 10001, and mounts the App key as a secret. The default port is published only on host loopback for a host-side TLS proxy. Ensure the container user can read the mounted key; do not put key material into image layers. Keep the data volume writable by the configured service user. Health checks use the local `/healthz` endpoint.

The optional PostgreSQL overlay is a deployment example, not a verified production configuration:

```bash
# Set POSTGRES_PASSWORD first.
docker compose -f compose.yaml -f compose.postgres.yaml up --build -d
```

The image includes the optional Psycopg driver. Bootstrap with one replica. This version uses `create_all` rather than migrations and serializes writers through a mutex row. Use one application process initially. Move to reviewed migrations and narrower database locks before scale-out. Do not put a SQLite WAL file on shared network storage and assume it is a distributed database.

## Worker deployment

Install Co4 and the chosen provider CLI inside a dedicated contributor environment. Authenticate the CLI there. Copy `examples/worker.toml`, configure a narrow repository allowlist, exact push destination, test command, and test globs. Set acknowledgments only after reviewing the execution and early-publication risks. Provide the device and Git tokens through the process environment or a service secret store. Never reuse the server's GitHub App private key.

Run one worker process per device configuration. Multiple device records can belong to one person, but the server allows only one active attempt per person. For the alpha, a terminal or an appropriately restricted service manager is simpler than an unattended auto-restarting fleet. A blocked lease requires explicit recovery; a service restart alone must not silently authorize a retry.

The worker stores state below its configured root. Back up or preserve `runs/<lease>/state.json`, the checkout, and `transcript.key` if recovery matters. A replacement contributor receives pushed checkpoints, not the original person's authentication or complete native session. Full local traces may contain sensitive text and are deliberately encrypted.

## Operations and failure recovery

Check `/healthz` for process/database health. Inspect the Governance page for audit events and outbox failures. A failed external operation retries with backoff; the UI exposes retry after repeated failure. The server logs dispatcher errors without intentionally echoing access tokens. Add your host's log aggregation and alerting for repeated provider failures, growing outbox backlog, heartbeat gaps, disk usage, and unavailable encryption keys. There is no built-in Prometheus exporter or pager integration yet.

Back up a stopped/quiesced SQLite database or use SQLite's supported backup facilities; do not copy only a live main file and forget its WAL state. Back up the encryption key and App configuration under a separate, secure retention policy. For PostgreSQL, use its database backup/restore tooling. Exercise restore in a nonproduction environment before depending on any backup.

Never downgrade or upgrade an existing database schema by simply swapping source versions without a tested migration. This release targets fresh installations. Domain name and TLS setup, GitHub registration, provider entitlement, initial secrets, repository policy, backup configuration, and isolation are operator setup, not actions performed by the ZIP.
