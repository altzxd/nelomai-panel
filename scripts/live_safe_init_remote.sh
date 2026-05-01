export DEBIAN_FRONTEND=noninteractive
set -e

ROOT=/opt/nelomai
TYPE=tic
REPO_URL="${NELOMAI_REPOSITORY_URL:-https://github.com/altzxd/nelomai-panel.git}"
SERVICE_NAME="nelomai-${TYPE}-agent.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"

uname -a
echo "---"
cat /etc/os-release
echo "---"
id -u
echo "---"
command -v bash
command -v apt-get
command -v ip || true
command -v systemctl || true
command -v wg >/dev/null 2>&1 || echo "wg-not-installed-yet"

apt-get update
apt-get install -y ca-certificates curl git iproute2 iptables jq nftables tar unzip wireguard wireguard-tools zip
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

install -d -m 755 "${ROOT}"
install -d -m 755 "${ROOT}/releases"
install -d -m 755 "${ROOT}/current"
install -d -m 700 "${ROOT}/runtime/${TYPE}"
install -d -m 700 "${ROOT}/state"

if [ ! -d "${ROOT}/current/.git" ]; then
  rm -rf "${ROOT}/current"
  git clone "${REPO_URL}" "${ROOT}/current"
else
  git -C "${ROOT}/current" pull --ff-only
fi

cd "${ROOT}/current/agents/node-tic-agent"
npm install --omit=dev

cat > "${SERVICE_PATH}" <<UNIT
[Unit]
Description=Nelomai TIC Node Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT}/current/agents/node-tic-agent
ExecStart=/usr/bin/node ${ROOT}/current/agents/node-tic-agent/src/daemon.js
Restart=always
RestartSec=3
Environment=NELOMAI_AGENT_COMPONENT=tic-agent
Environment=NELOMAI_AGENT_STATE_FILE=${ROOT}/state/tic-agent-state.json
Environment=NELOMAI_AGENT_DAEMON_STATUS_FILE=${ROOT}/state/tic-agent-daemon-status.json
Environment=NELOMAI_AGENT_RUNTIME_ROOT=${ROOT}/runtime/${TYPE}
Environment=NELOMAI_AGENT_EXEC_MODE=system
User=root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}"
