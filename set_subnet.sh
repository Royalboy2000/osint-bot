#!/usr/bin/env bash
#
# interactive_set_subnet_ip.sh
#
# Usage:
#   sudo ./interactive_set_subnet_ip.sh [-s SUFFIX]
#
# Options:
#   -s SUFFIX    Host suffix 1–254 (optional; default: random)
#
# Example:
#   sudo ./interactive_set_subnet_ip.sh
#     → prompts for iface, picks random suffix, uses existing gateway
#
#   sudo ./interactive_set_subnet_ip.sh -s 42
#     → prompts for iface, forces .42, uses existing gateway

set -euo pipefail

SUFFIX=""

# parse optional suffix
while getopts "s:h" opt; do
  case $opt in
    s) SUFFIX="$OPTARG" ;;
    *) 
       echo "Usage: sudo $0 [-s SUFFIX]" >&2
       exit 1
       ;;
  esac
done

# 1) List interfaces
echo "Available network interfaces:"
mapfile -t IFACES < <(ip -o link show | awk -F': ' '{print $2}' | grep -v lo)
for i in "${!IFACES[@]}"; do
  printf "  %2d) %s\n" "$((i+1))" "${IFACES[i]}"
done

# prompt choice
read -rp "Select interface [1-${#IFACES[@]}]: " choice
if ! [[ "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#IFACES[@]} )); then
  echo "Invalid selection." >&2
  exit 2
fi
IFACE="${IFACES[choice-1]}"

# 2) Auto-detect gateway
GATEWAY=$(ip route | awk '/^default/ {print $3; exit}')
if [[ -z "$GATEWAY" ]]; then
  echo "No default gateway found. Please ensure routing is set up." >&2
  exit 3
fi

# 3) Pick or validate suffix
if [[ -z "$SUFFIX" ]]; then
  SUFFIX=$(( (RANDOM % 254) + 1 ))
  echo "No suffix given → randomly chose .${SUFFIX}"
fi

if (( SUFFIX < 1 || SUFFIX > 254 )); then
  echo "ERROR: suffix must be 1–254 (got $SUFFIX)" >&2
  exit 4
fi

IP="10.50.10.${SUFFIX}/24"
echo
echo "→ Applying:"
echo "     Interface: $IFACE"
echo "     Address:   ${IP}"
echo "     Gateway:   ${GATEWAY}"
echo

# 4) Apply immediately
ip link set dev "$IFACE" down
ip addr flush dev "$IFACE"
ip addr add "$IP" dev "$IFACE"
ip link set dev "$IFACE" up

# update default route
ip route del default 2>/dev/null || true
ip route add default via "$GATEWAY" dev "$IFACE"

echo
echo "✔ $IFACE is now at ${IP%/*} (gw ${GATEWAY})"
echo "  Run 'ip addr show dev $IFACE' or 'ip route' to verify."
