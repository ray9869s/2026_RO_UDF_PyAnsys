import re, sys, statistics as st
rows=[]
for line in open(sys.argv[1], errors='ignore'):
    t=line.split()
    if len(t)>=7 and re.fullmatch(r'\d+', t[0]):
        try: rows.append((int(t[0]), float(t[5])))
        except ValueError: pass
n=int(sys.argv[2]) if len(sys.argv)>2 else 40
w=[v for _,v in rows[-n:]]
print("file:", sys.argv[1])
print("  iterations: %d - %d  (n=%d)"%(rows[0][0], rows[-1][0], len(rows)))
print("  nacl last-%d : min %.4e  max %.4e  median %.4e"%(n,min(w),max(w),st.median(w)))
print("  ptp / median = %.2f %%   <-- run C baseline was 16.5 %%"%((max(w)-min(w))/st.median(w)*100))
c=[v for _,v in rows[-200:]]
print("  continuity last-200: min %.4e max %.4e"%(min(c),max(c)))
