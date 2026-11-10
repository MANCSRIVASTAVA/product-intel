"""One-time setup: creates (or resets the password on) a read-only Postgres
role for the frontend to connect with, and appends its connection string to
local .env as DATABASE_URL_READONLY. Safe to re-run.

    python -m pipeline.create_readonly_role
"""
from __future__ import annotations

import secrets
from pathlib import Path

from sqlalchemy import text

from pipeline.load import get_engine

ROLE_NAME = "dashboard_reader"
ENV_PATH = Path(".env")


def main() -> None:
    engine = get_engine()
    password = secrets.token_urlsafe(24)

    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": ROLE_NAME}
        ).scalar()

        if exists:
            conn.execute(text(f"ALTER ROLE {ROLE_NAME} WITH PASSWORD :password"), {"password": password})
        else:
            conn.execute(
                text(f"CREATE ROLE {ROLE_NAME} WITH LOGIN PASSWORD :password"), {"password": password}
            )

        conn.execute(text(f"GRANT CONNECT ON DATABASE {engine.url.database} TO {ROLE_NAME}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {ROLE_NAME}"))
        conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ROLE_NAME}"))
        conn.execute(
            text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {ROLE_NAME}")
        )

    query = "&".join(f"{k}={v}" for k, v in engine.url.query.items())
    readonly_url = f"postgresql://{ROLE_NAME}:{password}@{engine.url.host}/{engine.url.database}"
    if query:
        readonly_url += f"?{query}"

    existing = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    lines = [line for line in existing.splitlines() if not line.startswith("DATABASE_URL_READONLY=")]
    lines.append(f"DATABASE_URL_READONLY={readonly_url}")
    ENV_PATH.write_text("\n".join(lines) + "\n")

    print(f"{ROLE_NAME} role ready, connection string written to {ENV_PATH} (not printed here)")


if __name__ == "__main__":
    main()
