import sys, requests, urllib3
urllib3.disable_warnings()
PORT, BASE = int(sys.argv[1]), int(sys.argv[2])
def soma(body):
    xml = ('<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
           '<soapenv:Body><dp:request domain="default" '
           'xmlns:dp="http://www.datapower.com/schemas/management">{}'
           '</dp:request></soapenv:Body></soapenv:Envelope>').format(body)
    t = requests.post("https://127.0.0.1:%d/service/mgmt/current" % PORT, data=xml.encode(), auth=("admin", "admin"), verify=False).text
    return "OK" if ">OK<" in t.replace(" ", "").replace("\n", "") else (t.split("<dp:result>",1)[1][:300] if "<dp:result>" in t else "FAULT")
for h in ("lab-https-modern", "lab-https-legacy", "lab-t"):
    print("del", h, soma('<dp:del-config><HTTPSSourceProtocolHandler name="%s"/></dp:del-config>' % h))
soma('<dp:del-config><MultiProtocolGateway name="lab-gw-t"/></dp:del-config>')
def fw(name, port, profile, client=""):
    return soma('<dp:set-config><XMLFirewallService name="%s"><mAdminState>enabled</mAdminState>'
        '<LocalAddress>0.0.0.0</LocalAddress><LocalPort>%d</LocalPort>'
        '<XMLManager class="XMLManager">default</XMLManager>'
        '<Type>loopback-proxy</Type>'
        '<StylePolicy class="StylePolicy">default</StylePolicy>'
        '<SSLConfigType>server</SSLConfigType>'
        '<SSLServer class="SSLServerProfile">%s</SSLServer>%s'
        '</XMLFirewallService></dp:set-config>' % (name, port, profile, client))
print("modern", fw("lab-partner-api", BASE, "lab-tls-modern"))
print("legacy", fw("lab-legacy-api", BASE + 1, "lab-tls-legacy"))
print("save", soma("<dp:do-action><SaveConfig/></dp:do-action>"))
