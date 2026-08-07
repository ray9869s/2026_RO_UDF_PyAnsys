import re, sys, statistics as st
rows=[]
for line in open(sys.argv[1], errors='ignore'):
    t=line.split()
    if len(t)>=7 and re.fullmatch(r'\d+', t[0]):
        try: rows.append((int(t[0]), float(t[5])))
        except ValueError: pass
print("file:", sys.argv[1])
print("  %-16s %12s %12s %12s %10s"%("block","min","max","median","ptp/med"))
B=250
for s in range(0, len(rows)-B+1, B):
    b=rows[s:s+B]; v=[x for _,x in b]
    print("  %-16s %12.4e %12.4e %12.4e %9.2f %%"
          %("%d-%d"%(b[0][0],b[-1][0]), min(v), max(v), st.median(v),
            (max(v)-min(v))/st.median(v)*100))
