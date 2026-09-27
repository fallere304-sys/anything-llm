from uno_common import *
import datetime as dt, json, time
D=lambda m,d: dt.date(2026,m,d)
def days(a,b):
    x=a
    while x<=b:
        yield x; x+=dt.timedelta(1)
def s(d): return d.strftime('%Y/%m/%d')
WARD={'3東':('3階東','３階東病棟'),'3南':('3階南','３階南病棟'),'4':('4階','４階病棟')}
obs=[]  # (id, date, ward_key, 有無, depth, score, 発見日, 発生場所)
def add(pid,a,b,wk,st,dep='',sc='',fd=None,place='',skip=()):
    for d in days(a,b):
        if d in skip: continue
        obs.append((pid,d,wk,st,dep,sc,fd,place))
# P1 dummy (user's)
add(123456,D(6,25),D(7,7),'3東','有','DU','12',D(6,17),'3東')
add(123456,D(7,8),D(7,11),'3東','治癒')
add(123456,D(7,12),D(7,14),'3東','無')
add(123456,D(7,15),D(7,21),'3東','有','D3','12',D(7,15),'3東')
add(123456,D(7,22),D(7,23),'3東','有','DU','12',D(7,15),'3東')
# P2 3南 無のみ
add(200001,D(7,3),D(7,31),'3南','無')
# P3 3南 持込 院外 改善
add(200002,D(6,24),D(7,24),'3南','有','d2','8',D(6,20),'院外')
add(200002,D(7,25),D(7,31),'3南','有','d2','6',D(6,20),'院外')
# P4 4階 7/26入院 持込 7/30治癒
add(200003,D(7,26),D(7,29),'4','有','d2','5',D(7,26),'自宅')
add(200003,D(7,30),D(7,31),'4','治癒')
# P5 4階 新規 d1
add(200004,D(7,1),D(7,19),'4','無')
add(200004,D(7,20),D(7,31),'4','有','d1','3',D(7,20),'4階')
# P6 転棟 3東→4階
add(200005,D(6,24),D(7,9),'3東','有','D3','15',D(6,10),'3東')
add(200005,D(7,10),D(7,24),'4','有','D3','15',D(6,10),'3東')
add(200005,D(7,25),D(7,31),'4','有','D3','13',D(6,10),'3東')
# P7 3南 新規 早期治癒
add(200006,D(7,1),D(7,14),'3南','無')
add(200006,D(7,15),D(7,17),'3南','有','d2','5',D(7,15),'3南')
add(200006,D(7,18),D(7,31),'3南','治癒')
# P9 3東 先月治癒・治癒コピー続く
add(200008,D(6,24),D(6,24),'3東','有','d2','4',D(6,1),'3東')
add(200008,D(6,25),D(7,31),'3東','治癒')
# P10 4階 先月以前 7/24欠損
add(200009,D(6,24),D(7,31),'4','有','D4','20',D(6,20),'4階',skip=(D(7,24),))
# P11 再入院 3東
add(200010,D(6,24),D(6,30),'3東','有','d2','10',D(6,28),'3東')
add(200010,D(7,5),D(7,30),'3東','有','d2','10',D(6,28),'3東')
add(200010,D(7,31),D(7,31),'3東','有','d2','9',D(6,28),'3東')
# P15 4階 予定入院 無のみ
add(200014,D(7,15),D(7,31),'4','無')

adm=[(123456,D(5,29),'3東'),(200001,D(7,3),'3南'),(200002,D(6,20),'3南'),(200003,D(7,26),'4'),(200004,D(7,1),'4'),
     (200005,D(6,1),'3東'),(200006,D(7,1),'3南'),(200008,D(5,1),'3東'),(200009,D(6,15),'4'),(200010,D(4,10),'3東'),
     (200010,D(7,5),'3東'),(200011,D(7,10),'3東'),(200012,D(7,12),'3東'),(200013,D(7,20),'3南'),(200014,D(7,15),'4')]
plan=[(123456,D(5,29),'3東','リスク有り'),(200001,D(7,3),'3南','リスク有り'),(200003,D(7,26),'4','リスク有り'),
      (200004,D(7,1),'4','リスク有り'),(200006,D(7,1),'3南','リスク有り'),(200010,D(4,10),'3東','リスク無し'),
      (200010,D(7,5),'3東','リスク有り'),(200011,D(7,10),'3東','リスク無し'),(200012,D(7,12),'3東','リスク有り'),
      (200014,D(4,20),'4','リスク有り')]

def main(path,setting,swap=False,label=''):
    proc,ctx,desk=start()
    doc=load(desk,path)
    so=doc.Sheets.getByName('褥瘡状況貼付'); sp=doc.Sheets.getByName('褥瘡予防対策診療計画書'); sa=doc.Sheets.getByName('入院一覧')
    so.getCellRangeByName('A8:Y7000').clearContents(1|2|4|16)
    sp.getCellRangeByName('A8:CM4000').clearContents(1|2|4|16)
    sa.getCellRangeByName('A2:M4000').clearContents(1|2|4|16)
    rows=[]
    o=sorted(obs,key=lambda x:(x[0],x[1]))
    if swap: o[5],o[6]=o[6],o[5]
    for pid,d,wk,st,dep,sc,fd,place in o:
        r=['25',str(pid),'テスト',s(d),'12:00','','F',str(pid),'テスト',WARD[wk][0],'2.6',st,'',dep,'','','','','','',sc,'','',s(fd) if fd else '',place]
        rows.append(tuple(r))
    so.getCellRangeByName(f'A8:Y{7+len(rows)}').setDataArray(tuple(rows))
    prow=[]
    for pid,d,wk,j in plan:
        r=['']*91; r[0]='91'; r[1]=str(pid); r[3]=s(d); r[7]=WARD[wk][1]; r[8]=s(d); r[33]=j
        prow.append(tuple(r))
    sp.getCellRangeByName(f'A8:CM{7+len(prow)}').setDataArray(tuple(prow))
    arow=[]
    for pid,d,wk in adm:
        arow.append((s(d),WARD[wk][1],'301号室','1',str(pid),'テスト','女','1940/01/01','内科','X','病名','',''))
    sa.getCellRangeByName(f'A2:M{1+len(arow)}').setDataArray(tuple(arow))
    doc.Sheets.getByName('設定').getCellRangeByName('C8').setString(setting)
    t=time.time(); doc.calculateAll(); el=time.time()-t
    rs=doc.Sheets.getByName('集計結果')
    out={'calc_sec':round(el,1),'month':rs.getCellRangeByName('C2').getString(),'D2':rs.getCellRangeByName('D2').getString(),'checks':{},'table':{}}
    T=[r for r in range(5,40) if rs.getCellRangeByName(f'A{r}').getString()=='項目'][0]
    for r in range(5,T-1):
        out['checks'][rs.getCellRangeByName(f'B{r}').getString()]=rs.getCellRangeByName(f'D{r}').getString()
    for r in range(T+1,T+33):
        lab=rs.getCellRangeByName(f'B{r}').getString().strip()
        if not lab: continue
        out['table'][f'{r}:{lab}']=[rs.getCellRangeByName(f'{c}{r}').getString() for c in 'CDE']
    # errors anywhere in processing first rows?
    so2=doc.Sheets.getByName('処理_観察')
    errs=0
    arr=so2.getCellRangeByName('A2:AK400').getDataArray()
    for i,row in enumerate(arr):
        for j,v in enumerate(row):
            c=so2.getCellByPosition(j,i+1)
            if c.getError()!=0: errs+=1
    out['obs_err_cells']=errs
    sl=doc.Sheets.getByName('該当患者一覧')
    out['list']=[' | '.join(str(int(x)) if isinstance(x,float) else str(x) for x in row[:14]) for row in sl.getCellRangeByName('A3:N30').getDataArray() if row[0]!='']
    out['list_adm']=[' | '.join(str(int(x)) if isinstance(x,float) else str(x) for x in row[:5]) for row in sl.getCellRangeByName('S3:W10').getDataArray() if row[0]!='']
    doc.close(True); proc.terminate()
    print(json.dumps(out,ensure_ascii=False,indent=1))
if __name__=='__main__':
    main(sys.argv[1],sys.argv[2],len(sys.argv)>3 and sys.argv[3]=='swap')
