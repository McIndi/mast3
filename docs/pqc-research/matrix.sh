#!/bin/bash
# usage: matrix.sh TAG  -> start a container, run every KEMAlgs variant, print results
S=$(cd "$(dirname "$0")" && pwd)
PY=${PY:-$S/../../.dev/mast_home/python/3.15.2/bin/python3}
TAG=$1; NAME=pqc-${TAG//./}
OUT=$S/result-$TAG.txt
exec > >(tee "$OUT") 2>&1
echo "##### DataPower $TAG"
LINE=$(bash $S/start_dp.sh "$TAG" "$NAME" "" | tee /dev/stderr | head -1)
SOMA=$(echo "$LINE" | sed -n 's/.*soma=\([0-9]*\).*/\1/p')
WEB=$(echo "$LINE" | sed -n 's/.*web=\([0-9]*\).*/\1/p')
sleep 40
echo "--- firmware"
$S/somareq.sh $SOMA '<man:get-status class="FirmwareVersion3"/>' | grep -i -e '<Version>' -e '<Build>' | head -3
$S/somareq.sh $SOMA '<man:get-status class="FirmwareVersion"/>' | grep -i -e '<Version>' | head -2
echo "--- built-in web-mgmt TLS (startup profile may have failed)"
$PY -I $S/probe.py $WEB | tail -n +2
echo "--- make profile with no KEMAlgs and attach to web-mgmt"
$S/setkem.sh $SOMA ""
$S/somareq.sh $SOMA '<man:modify-config><WebGUI name="WebGUI-Settings"><SSLServerConfigType>server</SSLServerConfigType><SSLServer class="SSLServerProfile">pqc-server</SSLServer></WebGUI></man:modify-config>' | grep -o -e OK -e '<faultstring>[^<]*' -e 'log-event[^<]*<[^>]*'
for P in "<TLSv1d2>on</TLSv1d2><TLSv1d3>on</TLSv1d3>" "<TLSv1d2>off</TLSv1d2><TLSv1d3>on</TLSv1d3>"; do
export PROTOS="$P"
echo "########## protocols: $P"
for v in "" "X25519MLKEM768" "X25519MLKEM768 classical" "X25519MLKEM768 SecP256r1MLKEM768 SecP384r1MLKEM1024 classical" "MLKEM768" "classical" "mlkem768" "x448_mlkem768" "p384_mlkem1024"; do
  echo "=== KEMAlgs: [$v]"
  $S/setkem.sh $SOMA "$v"
  sleep 4
  $S/somareq.sh $SOMA '<man:get-status class="ObjectStatus"/>' | grep -A6 '<Name>pqc-server</Name>' | grep -e ErrorCode | head -1
  $PY -I $S/probe.py $WEB | tail -n +2
done
done
echo "##### end $TAG"
