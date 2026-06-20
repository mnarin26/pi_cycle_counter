#!/bin/bash
# Allow pi user to run timedatectl without password (admin panel clock setup).
set -euo pipefail
SUDOERS_FILE="/etc/sudoers.d/injection-monitor-timedatectl"
echo "pi ALL=(ALL) NOPASSWD: /usr/bin/timedatectl" | sudo tee "$SUDOERS_FILE" >/dev/null
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE"
echo "OK: passwordless timedatectl for pi"
