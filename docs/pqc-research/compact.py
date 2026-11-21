"""One line per firmware with the key answers. usage: python3 compact.py"""
import glob, re
from summarize import inbound, outbound, key
names = ["X25519MLKEM768", "SecP256r1MLKEM768", "SecP384r1MLKEM1024", "MLKEM768", "classical", "mlkem768", "x448_mlkem768", "p384_mlkem1024"]
print("| fw | default→hybrid | KEMAlgs names accepted | KEM+TLS1.2 (in) | hyb+classical (1.3): hyb / classical | out default→PQC srv | out [hyb classical]→PQC / classical srv | out KEM+TLS1.2 |")
print("|---|---|---|---|---|---|---|---|")
for path in sorted(glob.glob("result-*.txt"), key=key):
    tag = path[7:-4]; r = inbound(path)
    d = r.get(("1.2+1.3", ""), {}).get("offers", {}).get("X25519MLKEM768 only")
    acc = []
    for n in names:
        for (p, k), v in r.items():
            if p == "1.3" and k == n:
                toks = re.findall(r"<KEMAlgs>(.*?)</KEMAlgs>", v["stored"] or "")
                if n in toks or (n == "classical" and ("p256" in toks or "classical" in toks)):
                    acc.append(n)
    t12 = r.get(("1.2+1.3", "X25519MLKEM768 classical"), {})
    t12s = ("down: " + t12["error"][:40]) if t12.get("error") else ("ok" if t12.get("stored") else "n/a")
    hc = r.get(("1.3", "X25519MLKEM768 classical"), {}).get("offers", {})
    hcs = "{} / {}".format(hc.get("X25519MLKEM768 only"), hc.get("X25519 only (TLS1.3)")) if r.get(("1.3", "X25519MLKEM768 classical"), {}).get("stored") else "n/a"
    try:
        o = outbound("outbound-%s.txt" % tag)
    except FileNotFoundError:
        o = []
    def f(srv, proto, kem):
        for row in o:
            if row["srv"] == srv and row["proto"] == proto and row["kem"] == kem:
                return row.get("result", "?"), row.get("status", "")
        return "?", ""
    od = f("X25519MLKEM768", "1.2+1.3", "")[0]
    oh = "{} / {}".format(f("X25519MLKEM768", "1.3", "X25519MLKEM768 classical")[0], f("X25519", "1.3", "X25519MLKEM768 classical")[0])
    o12 = f("default", "1.2+1.3", "X25519MLKEM768 classical")
    o12s = o12[0] + (" " + re.sub("<[^>]*>", " ", o12[1])[:50] if "down" in o12[1] else "")
    print("| {} | {} | {} | {} | {} | {} | {} | {} |".format(tag, d, ", ".join(acc) or "none", t12s, hcs, od, oh, o12s))
