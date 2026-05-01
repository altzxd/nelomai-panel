set -e

BACKUP_DIR=/opt/nelomai/state/hotfix-backup
REPO_DIR=/opt/nelomai/current
TYPE="${NELOMAI_SERVER_TYPE:-tic}"
SERVICE_NAME="nelomai-${TYPE}-agent.service"
INTERFACE_ID="${NELOMAI_LIVE_INTERFACE_ID:-wg-1-00001}"

install -d -m 700 "${BACKUP_DIR}"
cd "${REPO_DIR}"

for file in \
  agents/node-tic-agent/src/render.js \
  agents/node-tic-agent/src/runtime.js \
  agents/node-tic-agent/src/index.js
do
  if ! git diff --quiet -- "$file"; then
    cp "$file" "${BACKUP_DIR}/$(basename "$file").before-git-pull"
    git checkout -- "$file"
  fi
done

git pull --ff-only
systemctl restart "${SERVICE_NAME}"
sleep 2
systemctl is-active "${SERVICE_NAME}"
echo "---"
wg show "${INTERFACE_ID}"
echo "---"
sed -n '1,200p' "/etc/wireguard/${INTERFACE_ID}.conf"
