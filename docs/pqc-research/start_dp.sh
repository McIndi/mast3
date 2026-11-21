#!/bin/bash
# Start a DataPower container for the PQC research.
# usage: start_dp.sh TAG NAME "KEM1 KEM2 ..."   (empty string = no kem-alg)
# web-mgmt on 9090 uses SSL server profile pqc-server; xml-mgmt on 5550 is left as default.
set -euo pipefail
TAG=$1; NAME=$2; KEMS=${3:-}
DIR=$(cd "$(dirname "$0")" && pwd)
IMG=icr.io/cpopen/datapower/datapower-limited:$TAG
CFG=$(mktemp -d)
{
  echo "top; configure terminal;"
  echo "crypto"
  echo "  key pqc-key cert:///pqc.key"
  echo "  certificate pqc-cert cert:///pqc.crt"
  echo "  idcred pqc-idcred pqc-key pqc-cert"
  echo "  ssl-server pqc-server"
  echo "    idcred pqc-idcred"
  for k in $KEMS; do echo "    kem-alg $k"; done
  echo "  exit"
  echo "exit"
  echo "web-mgmt"
  echo "  admin-state enabled"
  echo "  local-address 0.0.0.0 9090"
  echo "  ssl-config-type server"
  echo "  ssl-server pqc-server"
  echo "exit"
  echo "xml-mgmt"
  echo "  admin-state enabled"
  echo "  local-address 0.0.0.0 5550"
  echo "exit"
  echo "write memory"
} > "$CFG/auto-startup.cfg"
podman rm -f "$NAME" >/dev/null 2>&1 || true
podman create --name "$NAME" \
  --env DATAPOWER_ACCEPT_LICENSE=true --env DATAPOWER_LOG_LEVEL=3 \
  --publish 127.0.0.1::5550 --publish 127.0.0.1::9090 "$IMG" >/dev/null
podman cp "$CFG/auto-startup.cfg" "$NAME:/opt/ibm/datapower/drouter/config/auto-startup.cfg"
podman cp "$DIR/pqc.key" "$NAME:/opt/ibm/datapower/root/secure/usrcerts/pqc.key"
podman cp "$DIR/pqc.crt" "$NAME:/opt/ibm/datapower/root/secure/usrcerts/pqc.crt"
podman start "$NAME" >/dev/null
SOMA=$(podman port "$NAME" 5550 | head -1 | cut -d: -f2)
WEB=$(podman port "$NAME" 9090 | head -1 | cut -d: -f2)
echo "$NAME soma=$SOMA web=$WEB"
for i in $(seq 1 120); do
  if curl -sk -u admin:admin -o /dev/null -w '%{http_code}' "https://127.0.0.1:$SOMA/service/mgmt/current" -d '<x/>' 2>/dev/null | grep -q '^[245]'; then
    echo "ready after ~$((i*5))s"; exit 0
  fi
  sleep 5
done
echo "not ready"; exit 1
