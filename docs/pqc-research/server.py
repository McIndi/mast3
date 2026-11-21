"""A TLS server for the outbound (DataPower as client) test. Python 3.15.
usage: server.py PORT GROUPS LOGFILE
GROUPS is an OpenSSL group list ("X25519MLKEM768", "X25519", or
"default" for the OpenSSL default). Each connection logs one line:
the protocol and group that were negotiated, or the handshake error."""
import socket
import ssl
import sys
import threading

PORT, GROUPS, LOG = int(sys.argv[1]), sys.argv[2], sys.argv[3]
HERE = __file__.rsplit("/", 1)[0]

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(HERE + "/pqc.crt", HERE + "/pqc.key")
ctx.minimum_version = ssl.TLSVersion.TLSv1_2
if GROUPS != "default":
    ctx.set_groups(GROUPS)


def log(line):
    with open(LOG, "a") as f:
        f.write(line + "\n")


def handle(conn):
    try:
        with ctx.wrap_socket(conn, server_side=True) as s:
            log("OK {} group={}".format(s.version(), s.group()))
            s.recv(4096)
            body = b"<ok/>"
            s.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/xml\r\n"
                      b"Content-Length: 5\r\nConnection: close\r\n\r\n" + body)
    except Exception as e:
        log("FAIL {}: {}".format(type(e).__name__, str(e)[:100]))


srv = socket.create_server(("0.0.0.0", PORT))
log("listening groups={}".format(GROUPS))
while True:
    c, _ = srv.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
