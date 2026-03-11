# Offensive — Attack Simulations

Attack simulation scripts for demonstrating Falco/Sysdig detection on the RetailBot AI workload.

---

## Attack 1 — Shell Spawn in AI Container

**File:** `attack.sh`

### What it does

Simulates an attacker who has obtained `kubectl exec` access to the RetailBot pod. This is a realistic post-exploitation scenario — the attacker may have stolen a kubeconfig, compromised a CI/CD pipeline, or exploited a vulnerability in the application.

The script:
1. Locates the running RetailBot pod in the `retailbot` namespace
2. Spawns a shell inside the container (`/bin/sh`)
3. Runs reconnaissance commands: identity, process list, network interfaces, environment variables (looking for leaked secrets)

### MITRE ATT&CK

| Tactic | Technique | ID |
|--------|-----------|----|
| Execution | Command and Scripting Interpreter | T1059 |
| Discovery | System Information Discovery | T1082 |
| Discovery | Network Interface Discovery | T1016 |
| Credential Access | Unsecured Credentials in Environment | T1552.007 |

### Expected Falco alerts

| Rule | Priority | Condition |
|------|----------|-----------|
| `Spawned Shell in Container` | WARNING | `proc.name in (shell_binaries)` inside container |
| `Terminal shell in container` | NOTICE | Interactive shell session opened |

Both rules are **built into Falco by default** — no custom rules required to detect this.

### How to run

On the VM (or via SSH):

```bash
chmod +x offensive/attack.sh
./offensive/attack.sh
```

### What to watch in Sysdig / Falco

```bash
# Falco (if running as systemd service)
sudo journalctl -fu falco | grep -i "shell\|retailbot"

# Falco (if running as K8s DaemonSet)
microk8s kubectl logs -n falco ds/falco -f | grep -i "shell\|retailbot"

# Sysdig Secure
# Navigate to Events > Runtime > filter by pod: retailbot
```

### Expected alert output

```
CRITICAL Shell spawned in RetailBot AI container
  user=root pod=retailbot-xxx ns=retailbot
  image=python:3.11-slim cmd=/bin/sh
  parent=kubectl
```

---

## Detection rules

Custom Falco rules with AI-specific context are in `detection/falco_rules.yaml`.

---

## Planned attacks

- [ ] Attack 2 — K8s service account token theft (T1552.007)
- [ ] Attack 3 — Unexpected outbound connection / data exfiltration (T1041)
- [ ] Attack 4 — Crypto miner simulation (GPU abuse)
- [ ] Attack 5 — Prompt injection → RCE chain (if app made intentionally vulnerable)
