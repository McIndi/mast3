#!/bin/bash
# Outbound test: DataPower as TLS client (SSLClientProfile) to server.py.
# usage: outbound.sh TAG     (the container pqc-<tag> must already run, from matrix.sh)
S=$(cd "$(dirname "$0")" && pwd)
PY=${PY:-$S/../../.dev/mast_home/python/3.15.2/bin/python3}
TAG=$1; NAME=pqc-${TAG//./}
SOMA=$(podman port "$NAME" 5550 | head -1 | cut -d: -f2)
OUT=$S/outbound-$TAG.txt
exec > >(tee "$OUT") 2>&1
echo "##### outbound DataPower $TAG (soma $SOMA)"
HOSTNAME_FOR_DP=host.containers.internal

req() { $S/somareq.sh $SOMA "$1" | grep -o -e '>OK<' -e '<faultstring>[^<]*' -e 'log-event[^<]*<[^>]*' -e '<dp:result>[^<]*' | head -3; }

client() {  # client "PROTOS" "KEM ..."
  local K=""; for k in $2; do K="$K<KEMAlgs>$k</KEMAlgs>"; done
  req "<man:set-config><SSLClientProfile name=\"pqc-client\"><Protocols>$1</Protocols><Ciphers>AES_256_GCM_SHA384</Ciphers><Ciphers>ECDHE_RSA_WITH_AES_256_GCM_SHA384</Ciphers><ValidateServerCert>off</ValidateServerCert>$K</SSLClientProfile></man:set-config>"
  echo -n "  stored: "; $S/somareq.sh $SOMA '<man:get-config class="SSLClientProfile" name="pqc-client"/>' | grep KEMAlgs | tr -d '\n ' | cut -c1-160; echo
}

echo "--- user agent and XML manager"
client "<TLSv1d2>on</TLSv1d2><TLSv1d3>on</TLSv1d3>" ""
req '<man:set-config><HTTPUserAgent name="pqc-ua"><SSLPolicies><RegExp>https://*</RegExp><SSLProxyProfile class="SSLProxyProfile"></SSLProxyProfile><SSLClientConfigType>client</SSLClientConfigType><SSLClient class="SSLClientProfile">pqc-client</SSLClient></SSLPolicies></HTTPUserAgent></man:set-config>'
req '<man:set-config><XMLManager name="pqc-xm"><UserAgent class="HTTPUserAgent">pqc-ua</UserAgent></XMLManager></man:set-config>'

PORT=18443; N=0
for SG in "X25519MLKEM768" "X25519" "default"; do
  LOG=$S/server-$TAG-$SG.log; rm -f "$LOG"
  $PY -I $S/server.py $PORT "$SG" "$LOG" & SRV=$!; trap "kill $SRV 2>/dev/null" EXIT
  sleep 1
  for P in "<TLSv1d2>on</TLSv1d2><TLSv1d3>on</TLSv1d3>" "<TLSv1d2>off</TLSv1d2><TLSv1d3>on</TLSv1d3>"; do
    for v in "" "X25519MLKEM768" "X25519MLKEM768 classical" "classical"; do
      N=$((N+1))
      echo "=== server groups [$SG], client protocols [$( [[ $P == *'<TLSv1d2>on'* ]] && echo 1.2+1.3 || echo 1.3)], client KEMAlgs [$v]"
      client "$P" "$v"
      sleep 3
      echo -n "  status: "; $S/somareq.sh $SOMA '<man:get-status class="ObjectStatus"/>' | grep -B3 -A2 '<Name>pqc-client</Name>' | grep -o '<OpState>[^<]*\|<ErrorCode>[^<]*' | tr '\n' ' '; echo
      before=$(wc -l < "$LOG")
      echo -n "  fetch: "; req "<man:do-action><FetchFile><URL>https://$HOSTNAME_FOR_DP:$PORT/f$N</URL><File>temporary:///pqc-f$N.xml</File><Overwrite>on</Overwrite><XMLManager class=\"XMLManager\">pqc-xm</XMLManager></FetchFile></man:do-action>" | tr '\n' ' '; echo
      sleep 2
      echo "  server: $(tail -n +$((before+1)) "$LOG" | tr '\n' ' ')"
    done
  done
  kill $SRV; wait $SRV 2>/dev/null
done
echo "##### end outbound $TAG"
pkill -f "[m]ast3-pqc-research/server.py" 2>/dev/null; true
