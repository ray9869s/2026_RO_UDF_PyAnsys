import re, sys
rows = []
for line in open(sys.argv[1], errors='ignore'):
    t = line.split()
    if len(t) >= 7 and re.fullmatch(r'\d+', t[0]):
        try: rows.append((int(t[0]), float(t[1])))
        except ValueError: pass
W = 20
mins = [i for i in range(W, len(rows)-W)
        if rows[i][1] == min(r[1] for r in rows[i-W:i+W+1])]
its = [rows[i][0] for i in mins]
print("file:", sys.argv[1])
print("  samples:", len(rows), " range:", rows[0][0] if rows else None, "-", rows[-1][0] if rows else None)
print("  minima :", its)
print("  spacing:", [b-a for a, b in zip(its, its[1:])])
