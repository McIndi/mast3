#!/bin/bash
# usage: somareq.sh PORT 'INNER-XML'  (inside man:request domain=default)
curl -sk -u admin:admin "https://127.0.0.1:$1/service/mgmt/current" -d "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:man=\"http://www.datapower.com/schemas/management\"><soapenv:Body><man:request domain=\"default\">$2</man:request></soapenv:Body></soapenv:Envelope>" | python3 -c "import sys,re; t=sys.stdin.read(); print(re.sub(r'>\s*<','>\n<',t))" | grep -v -e '^<?xml' -e Envelope -e 'env:Body' -e timestamp
