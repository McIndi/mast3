"""Summarise result-<tag>.txt (inbound) and outbound-<tag>.txt per firmware.
usage: python3 summarize.py   (in this directory)"""
import glob
import re


def key(tag):
    return [int(x) for x in re.findall(r"\d+", tag)]


def inbound(path):
    """{(protocols, kem): {offer: OK/FAIL ...}, 'stored': {...}}"""
    res, cur, proto = {}, None, "builtin"
    for line in open(path, errors="replace"):
        line = line.rstrip()
        m = re.match(r"#+ protocols: (.*)", line)
        if m:
            proto = "1.2+1.3" if "<TLSv1d2>on" in m.group(1) else "1.3"
        m = re.match(r"=== KEMAlgs: \[(.*)\]", line)
        if m:
            cur = (proto, m.group(1))
            res[cur] = {"stored": None, "offers": {}, "error": ""}
            continue
        if line.startswith("--- built-in"):
            cur = ("builtin", "")
            res[cur] = {"stored": None, "offers": {}, "error": ""}
            continue
        if cur is None:
            continue
        if line.startswith("stored:"):
            res[cur]["stored"] = line[7:].strip()
        if "<ErrorCode>" in line:
            res[cur]["error"] = re.sub(r"</?ErrorCode>", "", line).strip()
        m = re.match(r"(.{30}) (OK|FAIL)\s*(.*)", line)
        if m:
            g = re.search(r"group=(\S+)", m.group(3))
            res[cur]["offers"][m.group(1).strip()] = (
                g.group(1) if m.group(2) == "OK" and g else "FAIL")
    return res


def outbound(path):
    rows = []
    cur = None
    for line in open(path, errors="replace"):
        m = re.match(r"=== server groups \[(.*)\], client protocols \[(.*)\], client KEMAlgs \[(.*)\]", line)
        if m:
            cur = {"srv": m.group(1), "proto": m.group(2), "kem": m.group(3)}
            rows.append(cur)
        elif cur is not None and line.strip().startswith("server:"):
            g = re.search(r"OK \S+ group=(\S+)", line)
            cur["result"] = g.group(1) if g else ("FAIL" if "FAIL" in line else "no connection")
        elif cur is not None and line.strip().startswith("status:"):
            cur["status"] = line.split(":", 1)[1].strip()
    return rows


for path in sorted(glob.glob("result-*.txt"), key=lambda p: key(p)):
    tag = path[7:-4]
    r = inbound(path)
    print("#" * 70)
    print("DataPower", tag)
    b = r.get(("builtin", ""), {}).get("offers", {})
    print("  built-in web-mgmt, hybrid-only client:", b.get("X25519MLKEM768 only"), "| classical client:", b.get("X25519 only (TLS1.3)"))
    for (proto, kem), v in r.items():
        if proto == "builtin":
            continue
        o = v["offers"]
        print("  in  [{:7}] KEMAlgs [{:58}] stored={:3} hyb={:16} p256hyb={:18} p384hyb={:19} mlkem={:8} x25519={:9} tls12={:6} {}".format(
            proto, kem, "yes" if v["stored"] else "-", str(o.get("X25519MLKEM768 only")),
            str(o.get("SecP256r1MLKEM768 only")), str(o.get("SecP384r1MLKEM1024 only")),
            str(o.get("MLKEM768 only")), str(o.get("X25519 only (TLS1.3)")),
            str(o.get("X25519MLKEM768 on TLS1.2")), v["error"][:50]))
    op = "outbound-{}.txt".format(tag)
    try:
        for row in outbound(op):
            print("  out server[{:14}] client[{:7}] KEMAlgs [{:24}] -> {:16} {}".format(
                row["srv"], row["proto"], row["kem"], row.get("result", "?"), row.get("status", "")[:60]))
    except FileNotFoundError:
        print("  (no outbound results)")
