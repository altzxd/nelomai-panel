export DEBIAN_FRONTEND=noninteractive
set -e

ROOT=/opt/nelomai
TYPE="${NELOMAI_SERVER_TYPE:-tic}"
case "${TYPE}" in
  tic|tak) ;;
  *)
    echo "Unsupported NELOMAI_SERVER_TYPE: ${TYPE}" >&2
    exit 1
    ;;
esac
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
apt-get upgrade -y
apt-get install -y bash build-essential ca-certificates curl git iproute2 iptables jq nftables python3 tar unzip wireguard wireguard-tools zip
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs
GO_VERSION="$(curl -fsSL https://go.dev/VERSION?m=text | head -n 1 | tr -d '\r')"
echo "${GO_VERSION}" > /tmp/nelomai-go-version
GO_VERSION="$(cat /tmp/nelomai-go-version)"
curl -fsSL "https://dl.google.com/go/${GO_VERSION}.linux-amd64.tar.gz" -o /tmp/nelomai-go.tar.gz
rm -rf /usr/local/go
tar -C /usr/local -xzf /tmp/nelomai-go.tar.gz
/usr/local/go/bin/go version
install -d -m 755 "${ROOT}/third_party"
if [ ! -d "${ROOT}/third_party/amneziawg-tools/.git" ]; then
  rm -rf "${ROOT}/third_party/amneziawg-tools"
  git clone "https://github.com/amnezia-vpn/amneziawg-tools.git" "${ROOT}/third_party/amneziawg-tools"
else
  git -C "${ROOT}/third_party/amneziawg-tools" pull --ff-only
fi
make -C "${ROOT}/third_party/amneziawg-tools/src"
make -C "${ROOT}/third_party/amneziawg-tools/src" install PREFIX=/usr WITH_WGQUICK=yes WITH_SYSTEMDUNITS=yes
if [ ! -d "${ROOT}/third_party/amneziawg-go/.git" ]; then
  rm -rf "${ROOT}/third_party/amneziawg-go"
  git clone "https://github.com/amnezia-vpn/amneziawg-go.git" "${ROOT}/third_party/amneziawg-go"
else
  git -C "${ROOT}/third_party/amneziawg-go" pull --ff-only
fi
cd "${ROOT}/third_party/amneziawg-go"
PATH=/usr/local/go/bin:$PATH make
install -m 755 "${ROOT}/third_party/amneziawg-go/amneziawg-go" /usr/local/bin/amneziawg-go
command -v python3
command -v node
command -v npm
command -v wg
command -v awg
command -v awg-quick
command -v amneziawg-go

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

cat > "/etc/default/nelomai-${TYPE}-agent" <<ENV
NELOMAI_AGENT_COMPONENT=${TYPE}-agent
NELOMAI_AGENT_STATE_FILE=${ROOT}/state/${TYPE}-agent-state.json
NELOMAI_AGENT_DAEMON_STATUS_FILE=${ROOT}/state/${TYPE}-agent-daemon-status.json
NELOMAI_AGENT_RUNTIME_ROOT=${ROOT}/runtime/${TYPE}
NELOMAI_AGENT_EXEC_MODE=system
$(if [ "${TYPE}" = "tak" ]; then echo "NELOMAI_AMNEZIAWG_TOOL_CMD=/usr/bin/python3 ${ROOT}/current/scripts/official_amnezia_tool_bridge.py"; fi)
$(if [ "${TYPE}" = "tic" ]; then echo "NELOMAI_AGENT_TUNNEL_QUICK_CMD=/usr/bin/awg-quick"; fi)
$(if [ "${TYPE}" = "tic" ]; then echo "NELOMAI_AGENT_TUNNEL_USERSPACE_IMPLEMENTATION=/usr/local/bin/amneziawg-go"; fi)
ENV

cat > "${SERVICE_PATH}" <<UNIT
[Unit]
Description=Nelomai ${TYPE^^} Node Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT}/current/agents/node-tic-agent
ExecStart=/usr/bin/node ${ROOT}/current/agents/node-tic-agent/src/daemon.js
Restart=always
RestartSec=3
EnvironmentFile=-/etc/default/nelomai-${TYPE}-agent
User=root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}"
