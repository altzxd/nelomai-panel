#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_EXAMPLE_PATH="${ROOT_DIR}/.env.example"
ENV_PATH="${ROOT_DIR}/.env"
VENV_PATH="${ROOT_DIR}/.venv"
SERVICE_NAME="${SERVICE_NAME:-nelomai-panel}"
SERVICE_UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_SITE_NAME="${NGINX_SITE_NAME:-nelomai-panel}"
NGINX_SITE_AVAILABLE_PATH="/etc/nginx/sites-available/${NGINX_SITE_NAME}"
NGINX_SITE_ENABLED_PATH="/etc/nginx/sites-enabled/${NGINX_SITE_NAME}"
PANEL_BIND_HOST="${PANEL_BIND_HOST:-127.0.0.1}"
PANEL_BIND_PORT="${PANEL_BIND_PORT:-8000}"
PANEL_PUBLIC_BASE_URL="${PANEL_PUBLIC_BASE_URL:-https://nelomai.ru}"
PANEL_PUBLIC_HOST="${PANEL_PUBLIC_HOST:-}"
PANEL_TLS_EMAIL="${PANEL_TLS_EMAIL:-}"
PANEL_DB_NAME="${PANEL_DB_NAME:-nelomai_panel}"
PANEL_DB_USER="${PANEL_DB_USER:-nelomai}"
PANEL_DB_PASSWORD="${PANEL_DB_PASSWORD:-}"
PANEL_SECRET_KEY="${PANEL_SECRET_KEY:-}"
NELOMAI_GIT_REPO_VALUE="${NELOMAI_GIT_REPO:-https://github.com/altzxd/nelomai-panel.git}"
PEER_AGENT_COMMAND_VALUE="${PEER_AGENT_COMMAND_VALUE:-}"
ACCESS_TOKEN_EXPIRE_MINUTES_VALUE="${ACCESS_TOKEN_EXPIRE_MINUTES_VALUE:-720}"
PANEL_RUN_USER="${PANEL_RUN_USER:-}"
DATABASE_URL_VALUE=""

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    echo "Required file is missing: ${path}" >&2
    exit 1
  fi
}

generate_alnum_secret() {
  local length="$1"
  python3 - <<PY
import secrets
import string

alphabet = string.ascii_letters + string.digits
print("".join(secrets.choice(alphabet) for _ in range(${length})))
PY
}

derive_public_host() {
  if [[ -n "${PANEL_PUBLIC_HOST}" ]]; then
    return
  fi
  PANEL_PUBLIC_HOST="$(
    python3 - <<PY
from urllib.parse import urlparse

url = "${PANEL_PUBLIC_BASE_URL}"
parsed = urlparse(url)
print(parsed.hostname or "")
PY
  )"
  if [[ -z "${PANEL_PUBLIC_HOST}" ]]; then
    echo "Cannot derive PANEL_PUBLIC_HOST from PANEL_PUBLIC_BASE_URL=${PANEL_PUBLIC_BASE_URL}" >&2
    exit 1
  fi
}

derive_run_user() {
  if [[ -n "${PANEL_RUN_USER}" ]]; then
    return
  fi
  PANEL_RUN_USER="$(stat -c '%U' "${ROOT_DIR}")"
  if [[ -z "${PANEL_RUN_USER}" || "${PANEL_RUN_USER}" == "UNKNOWN" ]]; then
    echo "Cannot determine panel run user from ${ROOT_DIR}" >&2
    exit 1
  fi
}

install_base_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y \
    software-properties-common \
    ca-certificates \
    curl \
    git \
    gnupg \
    lsb-release \
    postgresql \
    postgresql-contrib \
    openssh-client \
    sshpass \
    tar \
    unzip \
    zip \
    nginx \
    certbot \
    python3-certbot-nginx
}

ensure_python_311() {
  if command -v python3.11 >/dev/null 2>&1; then
    return
  fi

  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update
  apt-get install -y python3.11 python3.11-venv
}

enable_base_services() {
  systemctl enable --now postgresql
  systemctl enable --now nginx
}

create_database() {
  local db_password="$1"
  local db_name="$2"
  local db_user="$3"

  sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${db_user}') THEN
    CREATE ROLE "${db_user}" LOGIN PASSWORD '${db_password}';
  ELSE
    ALTER ROLE "${db_user}" WITH LOGIN PASSWORD '${db_password}';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE "${db_name}" OWNER "${db_user}"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${db_name}')\gexec
GRANT ALL PRIVILEGES ON DATABASE "${db_name}" TO "${db_user}";
SQL
}

write_env_file() {
  local env_example="$1"
  local env_path="$2"
  local secret_key="$3"
  local database_url="$4"
  local public_base_url="$5"
  local git_repo="$6"
  local peer_agent_command="$7"
  local access_minutes="$8"

  cp "${env_example}" "${env_path}"
  python3 - <<PY
from pathlib import Path

env_path = Path(r"${env_path}")
values = {
    "DEBUG": "false",
    "SECRET_KEY": r"${secret_key}",
    "ACCESS_TOKEN_EXPIRE_MINUTES": r"${access_minutes}",
    "DATABASE_URL": r"${database_url}",
    "PANEL_PUBLIC_BASE_URL": r"${public_base_url}",
    "NELOMAI_GIT_REPO": r"${git_repo}",
    "PEER_AGENT_COMMAND": r"${peer_agent_command}",
}

lines = env_path.read_text(encoding="utf-8").splitlines()
updated = []
seen = set()
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else line
    if key in values:
        updated.append(f"{key}={values[key]}")
        seen.add(key)
    else:
        updated.append(line)
for key, value in values.items():
    if key not in seen:
        updated.append(f"{key}={value}")
env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
}

install_python_dependencies() {
  python3.11 -m venv "${VENV_PATH}"
  "${VENV_PATH}/bin/python" -m pip install --upgrade pip
  "${VENV_PATH}/bin/python" -m pip install -e "${ROOT_DIR}"
}

run_migrations() {
  (
    cd "${ROOT_DIR}"
    "${VENV_PATH}/bin/python" -m alembic upgrade head
  )
}

write_systemd_unit() {
  cat > "${SERVICE_UNIT_PATH}" <<EOF
[Unit]
Description=Nelomai Panel
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=${PANEL_RUN_USER}
Group=${PANEL_RUN_USER}
WorkingDirectory=${ROOT_DIR}
ExecStart=${VENV_PATH}/bin/python -m uvicorn app.main:app --host ${PANEL_BIND_HOST} --port ${PANEL_BIND_PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}.service"
}

write_nginx_site() {
  local cert_dir="/etc/letsencrypt/live/${PANEL_PUBLIC_HOST}"
  if [[ -f "${cert_dir}/fullchain.pem" && -f "${cert_dir}/privkey.pem" ]]; then
    cat > "${NGINX_SITE_AVAILABLE_PATH}" <<EOF
server {
    listen 443 ssl http2;
    server_name ${PANEL_PUBLIC_HOST};

    client_max_body_size 20m;
    ssl_certificate ${cert_dir}/fullchain.pem;
    ssl_certificate_key ${cert_dir}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    location / {
        proxy_pass http://${PANEL_BIND_HOST}:${PANEL_BIND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}

server {
    listen 80;
    server_name ${PANEL_PUBLIC_HOST};
    return 301 https://\$host\$request_uri;
}
EOF
  else
    cat > "${NGINX_SITE_AVAILABLE_PATH}" <<EOF
server {
    listen 80;
    server_name ${PANEL_PUBLIC_HOST};

    client_max_body_size 20m;

    location / {
        proxy_pass http://${PANEL_BIND_HOST}:${PANEL_BIND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
  fi

  ln -sf "${NGINX_SITE_AVAILABLE_PATH}" "${NGINX_SITE_ENABLED_PATH}"
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl reload nginx
}

maybe_run_certbot() {
  if [[ -z "${PANEL_TLS_EMAIL}" ]]; then
    return
  fi
  certbot --nginx --non-interactive --agree-tos -m "${PANEL_TLS_EMAIL}" -d "${PANEL_PUBLIC_HOST}"
}

print_summary() {
  cat <<EOF
Panel install bootstrap completed.

Repository root: ${ROOT_DIR}
Environment file: ${ENV_PATH}
Service: ${SERVICE_NAME}.service
Nginx site: ${NGINX_SITE_AVAILABLE_PATH}
Public URL: ${PANEL_PUBLIC_BASE_URL}
Database URL: ${DATABASE_URL_VALUE}
PostgreSQL user: ${PANEL_DB_USER}
PostgreSQL password: ${PANEL_DB_PASSWORD}

Useful commands:
- systemctl status ${SERVICE_NAME}.service
- journalctl -u ${SERVICE_NAME}.service -n 50 --no-pager
- journalctl -u ${SERVICE_NAME}.service -f

If this was the first clean startup, the one-time first-admin link should appear in:
journalctl -u ${SERVICE_NAME}.service -n 50 --no-pager
EOF
}

main() {
  require_file "${ENV_EXAMPLE_PATH}"
  derive_public_host
  derive_run_user

  install_base_packages
  ensure_python_311
  enable_base_services

  if [[ -z "${PANEL_DB_PASSWORD}" ]]; then
    PANEL_DB_PASSWORD="$(generate_alnum_secret 24)"
  fi
  if [[ -z "${PANEL_SECRET_KEY}" ]]; then
    PANEL_SECRET_KEY="$(generate_alnum_secret 64)"
  fi
  if [[ -z "${PEER_AGENT_COMMAND_VALUE}" ]]; then
    PEER_AGENT_COMMAND_VALUE="${VENV_PATH}/bin/python ${ROOT_DIR}/scripts/peer_agent_ssh_bridge.py"
  fi

  DATABASE_URL_VALUE="postgresql+psycopg://${PANEL_DB_USER}:${PANEL_DB_PASSWORD}@127.0.0.1:5432/${PANEL_DB_NAME}"

  create_database "${PANEL_DB_PASSWORD}" "${PANEL_DB_NAME}" "${PANEL_DB_USER}"
  write_env_file \
    "${ENV_EXAMPLE_PATH}" \
    "${ENV_PATH}" \
    "${PANEL_SECRET_KEY}" \
    "${DATABASE_URL_VALUE}" \
    "${PANEL_PUBLIC_BASE_URL}" \
    "${NELOMAI_GIT_REPO_VALUE}" \
    "${PEER_AGENT_COMMAND_VALUE}" \
    "${ACCESS_TOKEN_EXPIRE_MINUTES_VALUE}"
  install_python_dependencies
  run_migrations
  write_systemd_unit
  write_nginx_site
  maybe_run_certbot
  print_summary
}

main "$@"
