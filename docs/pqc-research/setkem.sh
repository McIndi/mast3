#!/bin/bash
# usage: setkem.sh SOMAPORT "KEM1 KEM2 ..."  -> set-config pqc-server with those KEMAlgs (empty = none)
S=$(dirname "$0"); K=""
for k in $2; do K="$K<KEMAlgs>$k</KEMAlgs>"; done
$S/somareq.sh $1 "<man:set-config><SSLServerProfile name=\"pqc-server\"><Protocols>${PROTOS:-<TLSv1d2>on</TLSv1d2><TLSv1d3>on</TLSv1d3>}</Protocols><Ciphers>AES_256_GCM_SHA384</Ciphers><Ciphers>ECDHE_RSA_WITH_AES_256_GCM_SHA384</Ciphers><Idcred class=\"CryptoIdentCred\">pqc-idcred</Idcred>$K</SSLServerProfile></man:set-config>" | grep -o -e 'OK' -e 'log-event[^<]*<[^>]*' | head -3
echo -n "stored: "; $S/somareq.sh $1 '<man:get-config class="SSLServerProfile" name="pqc-server"/>' | grep KEMAlgs | tr -d '\n'; echo
