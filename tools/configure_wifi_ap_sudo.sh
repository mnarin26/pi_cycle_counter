#!/bin/bash
# Allow pi user to manage fabrika Wi-Fi AP (hostapd) from admin panel 8080.
set -euo pipefail
SUDOERS_FILE="/etc/sudoers.d/injection-monitor-hostapd"
WLAN_IFACE="${WIFI_AP_IFACE:-wlan0}"
cat <<EOF | sudo tee "$SUDOERS_FILE" >/dev/null
pi ALL=(ALL) NOPASSWD: /usr/bin/cat /etc/hostapd/hostapd.conf
pi ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/hostapd/hostapd.conf
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart hostapd
pi ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active hostapd
pi ALL=(ALL) NOPASSWD: /usr/sbin/iw dev ${WLAN_IFACE} info
pi ALL=(ALL) NOPASSWD: /usr/bin/iw dev ${WLAN_IFACE} info
EOF
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE"
echo "OK: passwordless hostapd AP management for pi"

# NetworkManager profili senkron icin (AP kapali olsa bile)
NM_FILE="/etc/sudoers.d/injection-monitor-nmcli"
if [[ ! -f "$NM_FILE" ]]; then
  echo "pi ALL=(ALL) NOPASSWD: /usr/bin/nmcli" | sudo tee "$NM_FILE" >/dev/null
  sudo chmod 440 "$NM_FILE"
  sudo visudo -cf "$NM_FILE"
fi
