set -e

BACKUP_DIR=/opt/nelomai/state/hotfix-backup
REPO_DIR=/opt/nelomai/current

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
systemctl restart nelomai-tic-agent.service
sleep 2
systemctl is-active nelomai-tic-agent.service
echo "---"
wg show wg-1-00001
echo "---"
sed -n '1,200p' /etc/wireguard/wg-1-00001.conf
