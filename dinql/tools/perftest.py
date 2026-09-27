from uno_common import *
import datetime as dt, random, time
random.seed(1)
def s(d): return d.strftime('%Y/%m/%d')
W=[('3階東','３階東病棟','3東'),('3階南','３階南病棟','3南'),('4階','４階病棟','4階')]
proc,ctx,desk=start(); doc=load(desk,sys.argv[1])
so=doc.Sheets.getByName('褥瘡状況貼付'); sa=doc.Sheets.getByName('入院一覧'); sp=doc.Sheets.getByName('褥瘡予防対策診療計画書')
so.getCellRangeByName('A8:Y7000').clearContents(1|2|4|16); sa.getCellRangeByName('A2:M4000').clearContents(1|2|4|16); sp.getCellRangeByName('A8:CM4000').clearContents(1|2|4|16)
rows=[]; adm=[]; pl=[]
start_=dt.date(2026,6,24)
for i in range(160):
    pid=300000+i; w=W[i%3]; a=dt.date(2026,4,1)+dt.timedelta(random.randint(0,120)); adm.append((s(a),w[1],'','',str(pid),'患者%d'%i,'','','','','','',''))
    pl.append(tuple(['91',str(pid),'',s(a)]+['']*3+[w[1],s(a)]+['']*24+['リスク有り']+['']*57))
    ul=random.random()<0.35; fd=start_+dt.timedelta(random.randint(-20,30))
    for k in range(38):
        d=start_+dt.timedelta(k)
        if d<a: continue
        if ul and d>=fd:
            sc=max(0,12-(d-fd).days//7*2)
            st='有' if sc>0 else '治癒'
            rows.append(('25',str(pid),'患者%d'%i,s(d),'','','F',str(pid),'',w[0],'',st,'','D3' if st=='有' else '','','','','','','',str(sc) if st=='有' else '','','',s(fd) if st=='有' else '',w[2] if st=='有' else ''))
        else:
            rows.append(('25',str(pid),'患者%d'%i,s(d),'','','F',str(pid),'',w[0],'','無','','','','','','','','','','','','',''))
so.getCellRangeByName(f'A8:Y{7+len(rows)}').setDataArray(tuple(rows))
sa.getCellRangeByName(f'A2:M{1+len(adm)}').setDataArray(tuple(adm))
sp.getCellRangeByName(f'A8:CM{7+len(pl)}').setDataArray(tuple(pl))
t=time.time(); doc.calculateAll(); el=time.time()-t
rs=doc.Sheets.getByName('集計結果')
print('rows',len(rows),'calcAll sec',round(el,1))
T=[r for r in range(5,40) if rs.getCellRangeByName(f'A{r}').getString()=='項目'][0]
for r in range(5,T-1): print(rs.getCellRangeByName(f'B{r}').getString(),rs.getCellRangeByName(f'D{r}').getString())
doc.close(True); proc.terminate()
