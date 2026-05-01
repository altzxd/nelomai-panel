set -e

cat >/tmp/off.json <<'JSON'
{
  "action": "toggle_interface",
  "component": "tic-agent",
  "contract_version": "1.0",
  "requested_capabilities": ["interface.state.v1"],
  "tic_server": {"id": 1, "name": "live-tic-1", "host": "144.31.109.224"},
  "interface": {"id": 1001, "name": "live-check-interface"},
  "target_state": {"is_enabled": false}
}
JSON

NELOMAI_AGENT_EXEC_MODE=system /usr/bin/node /opt/nelomai/current/agents/node-tic-agent/src/index.js </tmp/off.json

if ip link show dev wg-1-00001 >/dev/null 2>&1; then
  echo DISABLE_CHECK_FAILED_IP
  exit 1
else
  echo DISABLE_CHECK_OK_IP
fi

if wg show wg-1-00001 >/dev/null 2>&1; then
  echo DISABLE_CHECK_FAILED_WG
  exit 1
else
  echo DISABLE_CHECK_OK_WG
fi

cat >/tmp/on.json <<'JSON'
{
  "action": "toggle_interface",
  "component": "tic-agent",
  "contract_version": "1.0",
  "requested_capabilities": ["interface.state.v1"],
  "tic_server": {"id": 1, "name": "live-tic-1", "host": "144.31.109.224"},
  "interface": {"id": 1001, "name": "live-check-interface"},
  "target_state": {"is_enabled": true}
}
JSON

NELOMAI_AGENT_EXEC_MODE=system /usr/bin/node /opt/nelomai/current/agents/node-tic-agent/src/index.js </tmp/on.json
echo "---"
wg show wg-1-00001
