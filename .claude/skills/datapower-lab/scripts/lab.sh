#!/usr/bin/env bash
# This file is part of McIndi's Automated Solutions Tool (MAST).
#
# MAST is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3
# as published by the Free Software Foundation.
#
# MAST is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MAST.  If not, see <https://www.gnu.org/licenses/>.
#
# Copyright 2015-2026, McIndi Solutions, All rights reserved.
#
# A MAST 4.0 lab: a development home of this checkout (editable, so it
# runs whatever is checked out) and two DataPower containers.
#
#   lab.sh build     make the home, start the appliances, register them
#   lab.sh up        start the appliances and mastd (web, worker, cron)
#   lab.sh down      stop mastd and the appliances (nothing is lost)
#   lab.sh status    show what runs
#   lab.sh destroy   remove the containers and the home
#
# MAST_REPO is the checkout (default: the one holding this script).
# MAST_LAB_HOME is the lab's MAST home (default: ~/mast-lab).
#
# Starting the containers accepts the IBM DataPower license
# (DATAPOWER_ACCEPT_LICENSE=true): the limited images are for
# non-production use only. The passwords below are lab-only: the
# images' published admin/admin, and a reader user that exists only
# inside these loopback-only containers.
set -euo pipefail

SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
REPO="${MAST_REPO:-$(cd "$SCRIPTS/../../../.." && pwd)}"
HOME_DIR="${MAST_LAB_HOME:-$HOME/mast-lab}"
MAST="$HOME_DIR/mast"
WEB_PORT=5088

# name image soma web ssh
APPLIANCES=(
  "dp1 icr.io/cpopen/datapower/datapower-limited:11.0.0.3 15550 19090 12222"
  "dp2 icr.io/cpopen/datapower/datapower-limited:10.6.4.0 25550 29090 22222"
)

STARTUP_CONFIG='top; configure terminal;

web-mgmt
  admin-state enabled
  local-address 0.0.0.0 9090
exit

xml-mgmt
  admin-state enabled
  local-address 0.0.0.0 5550
exit

ssh 0.0.0.0 2222
'

container() { echo "mast-lab-$1"; }

create_appliance() {
  local name=$1 image=$2 soma=$3 web=$4 ssh=$5 cname
  cname=$(container "$name")
  if podman container exists "$cname"; then return; fi
  podman create --name "$cname" \
    --env DATAPOWER_ACCEPT_LICENSE=true --env DATAPOWER_LOG_LEVEL=3 \
    --publish "127.0.0.1:$soma:5550" --publish "127.0.0.1:$web:9090" \
    --publish "127.0.0.1:$ssh:2222" "$image" >/dev/null
  local tmp
  tmp=$(mktemp -d)
  printf '%s' "$STARTUP_CONFIG" > "$tmp/auto-startup.cfg"
  podman cp "$tmp/auto-startup.cfg" \
    "$cname:/opt/ibm/datapower/drouter/config/auto-startup.cfg"
  rm -rf "$tmp"
}

wait_for() {
  local name=$1 soma=$2
  printf 'Waiting for %s' "$name"
  for _ in $(seq 150); do
    if curl -sk -o /dev/null "https://127.0.0.1:$soma/service/mgmt/current"; then
      echo " ready"; return
    fi
    printf '.'; sleep 2
  done
  echo; echo "$name did not answer; see: podman logs $(container "$name")" >&2
  exit 1
}

appliances_up() {
  for line in "${APPLIANCES[@]}"; do
    read -r name image soma web ssh <<<"$line"
    create_appliance "$name" "$image" "$soma" "$web" "$ssh"
    podman start "$(container "$name")" >/dev/null
  done
  for line in "${APPLIANCES[@]}"; do
    read -r name image soma web ssh <<<"$line"
    wait_for "$name" "$soma"
  done
}

daemon() { (cd "$HOME_DIR" && "$MAST" daemon "$@"); }

case "${1:-}" in
  build)
    if [ ! -x "$MAST" ]; then
      (cd "$REPO" && uv run --locked python mast-setup --development-mode \
        --home "$HOME_DIR")
      mkdir -p "$HOME_DIR/etc/local"
      printf '[server]\nport = %s\nsecure = false\n' "$WEB_PORT" \
        > "$HOME_DIR/etc/local/server.conf"
    fi
    appliances_up
    for line in "${APPLIANCES[@]}"; do
      read -r name image soma web ssh <<<"$line"
      "$MAST" targets add -n "$name" -k datapower -A 127.0.0.1 \
        -s "soma_port=$soma" -s "web_port=$web" -s "ssh_port=$ssh" \
        --notes "lab: $image"
      # The containers have self-signed certificates: pin every endpoint
      "$MAST" targets pin -n "$name" --yes
    done
    "$MAST" fleets add -n lab -k datapower -m dp1 -m dp2
    # A credential that may change the appliances (the limited images
    # start with admin/admin)
    echo admin | "$MAST" credentials add -n lab-admin -u admin -p change \
      -b lab --password-stdin -d "admin of the lab appliances"
    # A DataPower user that may only read, and a credential for it
    "$MAST" datapower add-group -a lab --credential lab-admin \
      -N readers -A '*/*/*?Access=r' -s
    "$MAST" datapower add-user -a lab --credential lab-admin \
      -u reader -p 'LabReader-2026x' -g readers \
      --supress-force-password-change -s
    echo 'LabReader-2026x' | "$MAST" credentials add -n lab-read \
      -u reader -p collect -b lab --password-stdin \
      -d "a DataPower user that may only read"
    "$MAST" credentials test -n lab-read -a dp1
    echo "Next: lab.sh up, then add a web user (mast users)."
    ;;
  up)
    appliances_up
    daemon start
    echo "Web UI: http://127.0.0.1:$WEB_PORT"
    ;;
  down)
    daemon stop || true
    for line in "${APPLIANCES[@]}"; do
      read -r name _ <<<"$line"
      podman stop "$(container "$name")" >/dev/null 2>&1 || true
    done
    ;;
  status)
    podman ps -a --filter name=mast-lab- \
      --format '{{.Names}}  {{.Status}}  {{.Ports}}'
    daemon status || true
    ;;
  destroy)
    daemon stop 2>/dev/null || true
    for line in "${APPLIANCES[@]}"; do
      read -r name _ <<<"$line"
      podman rm -f "$(container "$name")" >/dev/null 2>&1 || true
    done
    rm -rf "$HOME_DIR"
    ;;
  *)
    sed -n '18,28p' "$0"; exit 2 ;;
esac
