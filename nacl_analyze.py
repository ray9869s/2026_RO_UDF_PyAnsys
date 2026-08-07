import re, sys, statistics as st
rows=[]
for line in open(sys.argv[1], errors='ignore'):
    t=line.split()
    if len(t)>=7 and re.fullmatch(r'\d+', t[0]):
        try: rows.append((int(t[0]), float(t[1]), float(t[5])))  # iter, continuity, nacl
        except ValueError: pass
print("file:", sys.argv[1])
print("  iterations %d - %d  (n=%d)"%(rows[0][0], rows[-1][0], len(rows)))
print()
# nacl period from local minima, last 600 iterations
tail=rows[-600:]
W=8
mins=[i for i in range(W,len(tail)-W)
      if tail[i][2]==min(r[2] for r in tail[i-W:i+W+1])]
its=[tail[i][0] for i in mins]
sp=[b-a for a,b in zip(its,its[1:])]
print("  nacl minima :", its[-12:])
print("  spacings    :", sp[-11:])
if sp: print("  median period = %.0f iterations"%st.median(sp))
print()
print("  %-10s %12s %12s %10s"%("window","min","max","ptp/median"))
for n in (20,40,60,100,150,200,300,400,600):
    if n>len(rows): break
    w=[v for _,_,v in rows[-n:]]
    print("  %-10d %12.4e %12.4e %9.2f %%"%(n,min(w),max(w),(max(w)-min(w))/st.median(w)*100))
print()
c=[v for _,v,_ in rows[-200:]]
print("  continuity last-200 : min %.4e  max %.4e  ptp/median %.2f %%"
      %(min(c),max(c),(max(c)-min(c))/st.median(c)*100))
