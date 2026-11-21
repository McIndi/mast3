#!/bin/bash
# usage: run_all.sh TAG...   -> inbound matrix (if no result file yet) and outbound test for each tag
S=$(cd "$(dirname "$0")" && pwd)
for TAG in "$@"; do
  NAME=pqc-${TAG//./}
  if [ -s "$S/result-$TAG.txt" ]; then
    bash "$S/start_dp.sh" "$TAG" "$NAME" "" >/dev/null 2>&1; sleep 40
  else
    bash "$S/matrix.sh" "$TAG" >/dev/null 2>&1
  fi
  bash "$S/outbound.sh" "$TAG" >/dev/null 2>&1
  podman rm -f "$NAME" >/dev/null 2>&1
  echo "done $TAG"
done
