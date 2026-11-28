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
#   lab.sh build --accept-datapower-license
#                    make the home, start the appliances, register them
#   lab.sh up --accept-datapower-license
#                    start the appliances and mastd (web, worker, cron)
#   lab.sh down      stop mastd and the appliances (nothing is lost)
#   lab.sh status    show what runs
#
# MAST runs as the service account labops (mast --as labops), the first
# admin of the home, in the groups admin and datapower-operator.
#   lab.sh destroy   remove the containers and the home
#
# Options may come before or after the command. build can run again:
# it skips what is registered already.
#
# MAST_REPO is the checkout (default: the one holding this script).
# MAST_LAB_HOME is the lab's MAST home (default: ~/mast-lab/home).
# build marks the home it makes with a .mast-lab file. It refuses a
# folder that has files but no marker, and destroy refuses to delete a
# folder without the marker.
#
# build and up create or start IBM DataPower containers, which run
# IBM's software under IBM's license for the developers-limited image
# (non-production use only). They refuse to run unless you pass
# --accept-datapower-license, which says that you have read and accept
# that license; only then do the containers get
# DATAPOWER_ACCEPT_LICENSE=true. The passwords below are lab-only: the
# images' published admin/admin, and a reader user that exists only
# inside these loopback-only containers.
set -euo pipefail

COMMAND=""
ACCEPT_LICENSE=false
for arg in "$@"; do
  case "$arg" in
    --accept-datapower-license) ACCEPT_LICENSE=true ;;
    -*) echo "Unknown option: $arg" >&2; exit 2 ;;
    *)
      if [ -n "$COMMAND" ]; then
        echo "One command only, got: $COMMAND $arg" >&2; exit 2
      fi
      COMMAND=$arg ;;
  esac
done

require_license() {
  if [ "$ACCEPT_LICENSE" != true ]; then
    cat >&2 <<'EOF'
This starts IBM DataPower Gateway containers
(icr.io/cpopen/datapower/datapower-limited), which run under IBM's
license for that image: non-production use only. Read the license
terms in IBM's DataPower Gateway documentation. If you accept them,
run this command again with --accept-datapower-license.
EOF
    exit 3
  fi
}

SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
REPO="${MAST_REPO:-$(cd "$SCRIPTS/../../../.." && pwd)}"
HOME_DIR="${MAST_LAB_HOME:-$HOME/mast-lab/home}"
MARKER="$HOME_DIR/.mast-lab"
MAST="$HOME_DIR/mast"
# The command line acts for a user of MAST: the lab's service account
# labops, its first admin, which build makes (its token file is in
# etc/local/tokens of the home)
LAB_USER=labops
lab_mast() { "$MAST" --as "$LAB_USER" "$@"; }
WEB_PORT=5088

# Refuse a folder that is not a lab home, so build cannot write into
# it and destroy cannot delete it.
require_lab_home() {
  if [ -f "$MARKER" ]; then return; fi
  if [ "$COMMAND" = build ] && { [ ! -e "$HOME_DIR" ] ||
      [ -z "$(ls -A "$HOME_DIR")" ]; }; then
    mkdir -p "$HOME_DIR"
    touch "$MARKER"
    return
  fi
  cat >&2 <<EOF
$HOME_DIR is not a lab home made by lab.sh: it has no .mast-lab
marker. Set MAST_LAB_HOME to an empty or new folder. If it is a lab
home from an earlier lab script, and you want lab.sh to manage (and
destroy) it, run: touch "$MARKER"
EOF
  exit 4
}

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
  require_license
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
  require_license
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

case "$COMMAND" in
  build)
    require_license
    require_lab_home
    if [ ! -x "$MAST" ]; then
      (cd "$REPO" && uv run --locked python mast-setup --development-mode \
        --home "$HOME_DIR")
      mkdir -p "$HOME_DIR/etc/local"
      printf '[server]\nport = %s\nsecure = false\n' "$WEB_PORT" \
        > "$HOME_DIR/etc/local/server.conf"
    fi
    # Each step below is skipped when an earlier build did it
    if [ ! -f "$HOME_DIR/etc/local/tokens/$LAB_USER.token" ]; then
      "$MAST" users add-first-admin -u "$LAB_USER" --service
      lab_mast groups add-member -n datapower-operator -u "$LAB_USER"
    fi
    appliances_up
    for line in "${APPLIANCES[@]}"; do
      read -r name image soma web ssh <<<"$line"
      if ! lab_mast targets show -n "$name" >/dev/null 2>&1; then
        lab_mast targets add -n "$name" -k datapower -A 127.0.0.1 \
          -s "soma_port=$soma" -s "web_port=$web" -s "ssh_port=$ssh" \
          --notes "lab: $image"
        # The containers have self-signed certificates: pin every
        # endpoint
        lab_mast targets pin -n "$name" --yes
      fi
    done
    if ! lab_mast fleets list | grep -qE '(^|[[:space:]|])lab([[:space:]|]|$)'
    then
      lab_mast fleets add -n lab -k datapower -m dp1 -m dp2
    fi
    # A credential that may change the appliances (the limited images
    # start with admin/admin)
    if ! lab_mast credentials show -n lab-admin >/dev/null 2>&1; then
      echo admin | lab_mast credentials add -n lab-admin -u admin \
        -p change -b lab --password-stdin -d "admin of the lab appliances"
    fi
    # A DataPower user that may only read, and a credential for it
    if ! lab_mast credentials show -n lab-read >/dev/null 2>&1; then
      lab_mast datapower add-group -a lab --credential lab-admin \
        -N readers -A '*/*/*?Access=r' -s
      lab_mast datapower add-user -a lab --credential lab-admin \
        -u reader -p 'LabReader-2026x' -g readers \
        --supress-force-password-change -s
      echo 'LabReader-2026x' | lab_mast credentials add -n lab-read \
        -u reader -p collect -b lab --password-stdin \
        -d "a DataPower user that may only read"
    fi
    lab_mast credentials test -n lab-read -a dp1
    echo "Next: lab.sh up. Run MAST as $LAB_USER with: $MAST --as $LAB_USER ..."
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
    require_lab_home
    daemon stop 2>/dev/null || true
    for line in "${APPLIANCES[@]}"; do
      read -r name _ <<<"$line"
      podman rm -f "$(container "$name")" >/dev/null 2>&1 || true
    done
    rm -rf "$HOME_DIR"
    ;;
  *)
    sed -n '18,39p' "$0"; exit 2 ;;
esac
