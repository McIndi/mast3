#!/bin/bash
# usage: soma.sh PORT CLASS   -> get-config of CLASS in default domain
curl -sk -u admin:admin "https://127.0.0.1:$1/service/mgmt/current" -d "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:man=\"http://www.datapower.com/schemas/management\"><soapenv:Body><man:request domain=\"default\"><man:get-config class=\"$2\"/></man:request></soapenv:Body></soapenv:Envelope>"
