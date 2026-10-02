# DiNQL 褥瘡ケア 4/5/8/9/10 集計ブック 再構成スクリプト（関数のみ・マクロなし）
from uno_common import *
import uno
from com.sun.star.table import CellRangeAddress

N_OBS=int(os.environ.get("N_OBS","6000"))   # 処理_観察 行数
N_ADM=3000   # 処理_入院 行数（貼付 2..3001 行）
N_PLN=3000   # 処理_計画書 行数（貼付 8..3007 行）
OBS_LAST=N_OBS+1; ADM_LAST=N_ADM+1; PLN_LAST=N_PLN+1

P_OBS='褥瘡状況貼付'; P_PLN='褥瘡予防対策診療計画書'; P_ADM='入院一覧'
S_OBS='処理_観察'; S_ADM='処理_入院'; S_PLN='処理_計画書'; S_SET='設定'; S_RES='集計結果'
M1=f'${S_SET}.$C$5'   # 集計月初
ME=f'${S_SET}.$C$6'   # 集計月末

def src(sheet,col,last,off):
    return f'INDEX(${sheet}.${col}$1:${col}${last};ROW()+{off})'
def DN(x):  # 日付正規化（日付値・"2026/07/01"文字列どちらも可）
    return f'IF(ISNUMBER({x});INT({x});IF(TRIM({x})="";"";IFERROR(DATEVALUE(SUBSTITUTE(TRIM({x});"/";"-"));IFERROR(DATEVALUE(TRIM({x}));""))))'
def NW(x):  # 病棟名正規化 ３階東病棟/3階東/3東 → W3東
    return f'"W"&SUBSTITUTE(SUBSTITUTE(SUBSTITUTE(ASC(TRIM({x}));" ";"");"病棟";"");"階";"")'
def IDN(x):
    return f'IF(TRIM({x})="";"";IFERROR(VALUE(TRIM({x}));TRIM({x})))'
def DEP(x):
    a=f'ASC(TRIM({x}))'
    return f'IF(OR({a}="DU";{a}="U");"U";IF(OR({a}="DDTI";{a}="DTI");"DTI";{a}))'

def rng(col,last,first=2): return f'${col}${first}:${col}${last}'

# ---------------- 処理_観察 ----------------
OBS_COLS=[
 ('A','患者ID'),('B','記録日'),('C','病棟コード'),('D','褥瘡有無(元)'),('E','褥瘡有無(処理)\n連続する治癒は2日目以降を無'),
 ('F','深さ'),('G','合計点'),('H','発見日\n(治癒行は直前行を継承)'),('I','発生場所コード'),('J','入院日\n(入院一覧から)'),
 ('K','当月\n1=集計月の記録'),('L','区分\n持込/先月以前/新規'),('M','褥瘡キー\nID_病棟_発見日'),('N','最終行候補'),
 ('O','基準日\n(当月最新記録)'),('P','代表行\n1=基準日の行'),('Q','7日前の評価\n1=あり'),('R','7日前の合計点'),
 ('S','治癒\n1=基準日が治癒'),('T','評価日までの日数\n(病棟の最終記録日−\n病棟入室日と発見日の遅い方)'),('U','対象\n治癒は7日以上で1'),
 ('V','9/10分母'),('W','9/10分子\n(改善)'),('X','5B/5C\n患者初出'),('Y','9/10分母\n患者初出'),('Z','9/10分子\n患者初出'),
 ('AA','新規の初回記録'),('AB','8 新規エピソード初出\n(同じ褥瘡キーは1回)'),('AC','病棟内患者初出\n(当月)'),('AD','5A\n危険因子あり\n(褥瘡なし)'),
 ('AE','点検:並び順異常'),('AF','点検:同日重複'),('AG','点検:有で空欄'),('AH','点検:日付読取不可'),
 ('AI','点検:期間外記録'),('AJ','点検:入院日不明'),('AK','点検:未登録病棟'),
 ('AL','氏名'),('AM','8 発見日の深さ順位'),('AN','一覧番号'),('AO','一覧累計'),
 ('AP','深さ順位(行)\nd1=1…D5=5,DTI=6,U=7'),('AQ','発見時の深さ順位\n(新規キー)'),('AR','未使用'),('AS','未使用'),('AT','比較した\n1週間前の日付'),('AU','未使用')]

def obs_row(r):
    p,n=r-1,r+1
    sB=src(P_OBS,'B',N_OBS+7,6); sD=src(P_OBS,'D',N_OBS+7,6); sJ=src(P_OBS,'J',N_OBS+7,6)
    sL=src(P_OBS,'L',N_OBS+7,6); sN=src(P_OBS,'N',N_OBS+7,6); sU=src(P_OBS,'U',N_OBS+7,6)
    sX=src(P_OBS,'X',N_OBS+7,6); sY=src(P_OBS,'Y',N_OBS+7,6)
    L=OBS_LAST
    AR=rng('A',L); BR=rng('B',L); CR=rng('C',L); GR=rng('G',L); KR=rng('K',L); LR=rng('L',L); MR=rng('M',L)
    f={}
    f['A']=f'={IDN(sB)}'
    f['B']=f'=IF(A{r}="";"";{DN(sD)})'
    f['C']=f'=IF(A{r}="";"";{NW(sJ)})'
    f['D']=f'=IF(A{r}="";"";TRIM({sL}))'
    f['E']=f'=IF(A{r}="";"";IF(AND(D{r}="治癒";A{r}=A{p};D{p}="治癒");"無";D{r}))'
    f['F']=f'=IF(E{r}="有";{DEP(sN)};"")'
    f['G']=f'=IF(A{r}="";"";IF(E{r}="有";IFERROR(VALUE(TRIM({sU}));"");0))'
    f['H']=f'=IF(A{r}="";"";IF(E{r}="有";{DN(sX)};IF(AND(E{r}="治癒";A{r}=A{p});H{p};"")))'
    f['I']=f'=IF(A{r}="";"";IF(E{r}="有";IF(TRIM({sY})="";"";{NW(sY)});IF(AND(E{r}="治癒";A{r}=A{p});I{p};"")))'
    f['J']=f'=IF(B{r}="";"";MAXIFS(${S_ADM}.$B$2:$B${ADM_LAST};${S_ADM}.$A$2:$A${ADM_LAST};A{r};${S_ADM}.$B$2:$B${ADM_LAST};"<="&B{r}))'
    f['K']=f'=IF(N(B{r})=0;0;IF(AND(B{r}>={M1};B{r}<={ME});1;0))'
    f['L']=f'=IF(OR(H{r}="";I{r}="");"";IF(OR(I{r}<>C{r};AND(N(J{r})>0;H{r}<N(J{r})));"持込";IF(H{r}<{M1};"先月以前";"新規")))'
    f['M']=f'=IF(L{r}="";"";A{r}&"_"&C{r}&"_"&H{r})'
    f['N']=f'=IF(AND(K{r}=1;M{r}<>"");IF(M{n}<>M{r};1;0);0)'
    f['O']=f'=IF(N{r}=1;MAXIFS({BR};{MR};M{r};{KR};1);"")'
    f['P']=f'=IF(N{r}=1;IF(B{r}=O{r};1;0);0)'
    f['Q']=f'=IF(P{r}=1;IF(N(AT{r})>0;1;0);"")'
    f['R']=f'=IF(Q{r}=1;MAXIFS({GR};{MR};M{r};{BR};AT{r});"")'
    f['S']=f'=IF(P{r}=1;IF(E{r}="治癒";1;0);"")'
    f['T']=f'=IF(P{r}=1;MAXIFS({BR};{AR};A{r};{CR};C{r};{KR};1)-MAX(MINIFS({BR};{AR};A{r};{CR};C{r};$J$2:$J${L};J{r});H{r});"")'
    f['U']=f'=IF(P{r}=1;IF(S{r}=1;IF(T{r}>=7;1;0);1);"")'
    f['V']=f'=IF(P{r}=1;IF(AND(U{r}=1;OR(Q{r}=1;S{r}=1));1;0);0)'
    f['W']=f'=IF(V{r}=1;IF(OR(S{r}=1;AND(Q{r}=1;ISNUMBER(G{r});ISNUMBER(R{r});G{r}<R{r}));1;0);0)'
    f['X']=f'=IF(P{r}=1;IF(COUNTIFS($A$2:A{r};A{r};$C$2:C{r};C{r};$L$2:L{r};L{r};$P$2:P{r};1)=1;1;0);0)'
    f['Y']=f'=IF(V{r}=1;IF(COUNTIFS($A$2:A{r};A{r};$C$2:C{r};C{r};$L$2:L{r};L{r};$V$2:V{r};1)=1;1;0);0)'
    f['Z']=f'=IF(W{r}=1;IF(COUNTIFS($A$2:A{r};A{r};$C$2:C{r};C{r};$L$2:L{r};L{r};$W$2:W{r};1)=1;1;0);0)'
    f['AA']=f'=IF(AND(L{r}="新規";K{r}=1);IF(M{r}<>M{p};1;0);0)'
    f['AB']=f'=IF(AA{r}=1;IF(COUNTIFS($M$2:M{r};M{r};$AA$2:AA{r};1)=1;1;0);0)'
    f['AC']=f'=IF(K{r}=1;IF(AND(A{r}=A{p};C{r}=C{p};K{p}=1);0;IF(COUNTIFS($A$2:A{r};A{r};$C$2:C{r};C{r};$K$2:K{r};1)=1;1;0));0)'
    f['AD']=(f'=IF(AC{r}=1;IF(COUNTIFS({AR};A{r};{CR};C{r};{KR};1;{LR};"持込")'
             f'+COUNTIFS({AR};A{r};{CR};C{r};{KR};1;{LR};"先月以前")=0;1;0);0)')
    f['AE']=(f'=IF(A{r}="";0;IF(AND(A{r}=A{p};N(J{r})=N(J{p}));IF(N(B{r})<N(B{p});1;0);'
             f'IF(COUNTIFS($A$1:A{p};A{r};$J$1:J{p};J{r})>0;1;0)))')
    f['AF']=f'=IF(A{r}="";0;IF(AND(A{r}=A{p};B{r}=B{p});1;0))'
    f['AG']=f'=IF(E{r}="有";IF(OR(H{r}="";I{r}="";G{r}="");1;0);0)'
    f['AH']=f'=IF(AND(A{r}<>"";B{r}="");1;0)'
    f['AI']=f'=IF(N(B{r})=0;0;IF(OR(B{r}<{M1}-7;B{r}>{ME});1;0))'
    f['AJ']=f'=IF(AND(A{r}<>"";N(B{r})>0;N(J{r})=0);1;0)'
    f['AK']=f'=IF(A{r}="";0;IF(COUNTIF(${S_SET}.$C$16:$C$23;C{r})=0;1;0))'
    sC=src(P_OBS,'C',N_OBS+7,6)
    f['AL']=f'=IF(A{r}="";"";TRIM({sC}))'
    f['AM']=f'=IF(AB{r}=1;AP{r};0)'
    PR=rng('P',L); APR=rng('AP',L); ARR=rng('AR',L)
    f['AP']=f'=IF(E{r}="有";IF(F{r}="d1";1;IF(F{r}="d2";2;IF(F{r}="D3";3;IF(F{r}="D4";4;IF(F{r}="D5";5;IF(F{r}="DTI";6;IF(F{r}="U";7;0)))))));0)'
    f['AQ']=f'=IF(AND(P{r}=1;L{r}="新規");MAXIFS({APR};{MR};M{r};{BR};MINIFS({BR};{MR};M{r}));0)'
    fd=f'MINIFS({BR};{MR};M{r};{APR};">=1";{APR};"<=5";{KR};1)'
    f['AR']=f'=IF(AQ{r}>=6;IF({fd}>0;MAXIFS({APR};{MR};M{r};{BR};{fd});AQ{r});AQ{r})'
    f['AS']=f'=IF(AND(P{r}=1;L{r}="新規");MAXIFS({ARR};{AR};A{r};{CR};C{r};{LR};"新規";{PR};1);0)'
    tol=f'${S_SET}.$C$8="はい"'
    f['AT']=(f'=IF(P{r}=1;IF(COUNTIFS({MR};M{r};{BR};B{r}-7)>0;B{r}-7;IF({tol};IF(COUNTIFS({MR};M{r};{BR};B{r}-6)>0;B{r}-6;'
             f'IF(COUNTIFS({MR};M{r};{BR};B{r}-8)>0;B{r}-8;""));""));"")')
    f['AU']=f'=IF(AB{r}=1;MAXIFS({ARR};{AR};A{r};{CR};C{r};{LR};"新規";{PR};1);0)'
    f['AO']=f'=N(AO{p})+AC{r}'
    f['AN']=f'=IF(AC{r}=1;AO{r};"")'
    return [f[c] for c,_ in OBS_COLS]

# ---------------- 処理_入院 ----------------
ADM_COLS=[('A','患者ID'),('B','入院日'),('C','病棟コード'),('D','当月入院'),('E','当月入院\n病棟内患者初出'),
 ('F','照合した計画書の登録日'),('G','計画書あり'),('H','計画書でリスク有り'),('I','当月の観察記録あり\n(同じ病棟)'),
 ('J','点検:計画書なし'),('K','点検:リスク有りで\n観察記録なし'),('L','氏名'),('M','一覧累計'),('N','一覧番号')]
def adm_row(r):
    sA=src(P_ADM,'A',N_ADM+1,0); sB=src(P_ADM,'B',N_ADM+1,0); sE=src(P_ADM,'E',N_ADM+1,0)
    PA=f'${S_PLN}.$A$2:$A${PLN_LAST}'; PB=f'${S_PLN}.$B$2:$B${PLN_LAST}'; PD=f'${S_PLN}.$D$2:$D${PLN_LAST}'
    f={}
    f['A']=f'={IDN(sE)}'
    f['B']=f'=IF(A{r}="";"";{DN(sA)})'
    f['C']=f'=IF(A{r}="";"";{NW(sB)})'
    f['D']=f'=IF(N(B{r})=0;0;IF(AND(B{r}>={M1};B{r}<={ME});1;0))'
    f['E']=f'=IF(D{r}=1;IF(COUNTIFS($A$2:A{r};A{r};$C$2:C{r};C{r};$D$2:D{r};1)=1;1;0);0)'
    f['F']=f'=IF(E{r}=1;MAXIFS({PB};{PA};A{r};{PB};">="&(B{r}-${S_SET}.$C$11);{PB};"<="&(B{r}+${S_SET}.$C$12));"")'
    f['G']=f'=IF(E{r}=1;IF(N(F{r})>0;1;0);0)'
    f['H']=f'=IF(G{r}=1;IF(COUNTIFS({PA};A{r};{PB};F{r};{PD};1)>0;1;0);0)'
    f['I']=f'=IF(H{r}=1;IF(COUNTIFS(${S_OBS}.$A$2:$A${OBS_LAST};A{r};${S_OBS}.$C$2:$C${OBS_LAST};C{r};${S_OBS}.$K$2:$K${OBS_LAST};1)>0;1;0);"")'
    f['J']=f'=IF(AND(E{r}=1;G{r}=0);1;0)'
    f['K']=f'=IF(AND(H{r}=1;I{r}=0);1;0)'
    sF=src(P_ADM,'F',N_ADM+1,0)
    f['L']=f'=IF(A{r}="";"";TRIM({sF}))'
    f['M']=f'=N(M{r-1})+IF(OR(J{r}=1;K{r}=1);1;0)'
    f['N']=f'=IF(OR(J{r}=1;K{r}=1);M{r};"")'
    return [f[c] for c,_ in ADM_COLS]

# ---------------- 処理_計画書 ----------------
PLN_COLS=[('A','患者ID'),('B','登録日'),('C','病棟コード'),('D','リスク有り\n1=判定がリスク有り'),('E','判定(元)')]
def pln_row(r):
    sB=src(P_PLN,'B',N_PLN+7,6); sD=src(P_PLN,'D',N_PLN+7,6); sH=src(P_PLN,'H',N_PLN+7,6); sAH=src(P_PLN,'AH',N_PLN+7,6)
    f={}
    f['A']=f'={IDN(sB)}'
    f['B']=f'=IF(A{r}="";"";{DN(sD)})'
    f['C']=f'=IF(A{r}="";"";{NW(sH)})'
    f['D']=f'=IF(A{r}="";"";IF(ISNUMBER(FIND("リスク有";{sAH}));1;0))'
    f['E']=f'=IF(A{r}="";"";TRIM({sAH}))'
    return [f[c] for c,_ in PLN_COLS]

def col_idx(c):
    n=0
    for ch in c: n=n*26+ord(ch)-64
    return n-1

def fill(sheet,cols,rowfn,nrows,chunk=1000):
    last=cols[-1][0]
    sheet.getCellRangeByName(f'A1:{last}1').setDataArray((tuple(h for _,h in cols),))
    for s in range(2,nrows+2,chunk):
        e=min(s+chunk-1,nrows+1)
        data=tuple(tuple(rowfn(r)) for r in range(s,e+1))
        sheet.getCellRangeByName(f'A{s}:{last}{e}').setFormulaArray(data)

def main(src_path,out_path):
    proc,ctx,desk=start()
    doc=load(desk,src_path)
    sheets=doc.Sheets
    for nm in ['Sheet2','入院日','褥瘡状況',S_RES]:
        if sheets.hasByName(nm): sheets.removeByName(nm)
    # 並び: 集計結果, 設定, 貼付3枚, 処理3枚
    sheets.insertNewByName(S_RES,0); sheets.insertNewByName(S_SET,1)
    order=[S_RES,S_SET,P_OBS,P_PLN,P_ADM]
    for i,nm in enumerate(order): sheets.moveByName(nm,i)
    sheets.insertNewByName(S_OBS,5); sheets.insertNewByName(S_ADM,6); sheets.insertNewByName(S_PLN,7)

    nf=doc.NumberFormats; loc=uno.createUnoStruct('com.sun.star.lang.Locale'); loc.Language='ja'; loc.Country='JP'
    def fmt(code):
        k=nf.queryKey(code,loc,False)
        return k if k!=-1 else nf.addNew(code,loc)
    FD=fmt('YYYY/MM/DD;;'); FYM=fmt('YYYY"年"M"月"')

    # ---- 設定 ----
    st=sheets.getByName(S_SET)
    def put(sh,addr,v):
        c=sh.getCellRangeByName(addr)
        if isinstance(v,str) and v.startswith('='): c.setFormula(v)
        elif isinstance(v,(int,float)): c.setValue(v)
        else: c.setString(v)
        return c
    put(st,'A1','設定（黄色のセルだけ変更できます）')
    put(st,'B3','集計月（手入力・任意）'); put(st,'D3','空欄なら観察記録の最新記録日の月を自動で採用。別の月で集計したいときだけ 2026/07/01 のように入力')
    put(st,'B4','自動判定の集計月（観察記録の最新記録日の月）')
    put(st,'C4',f'=IF(COUNT(${S_OBS}.$B$2:$B${OBS_LAST})=0;"";DATE(YEAR(MAX(${S_OBS}.$B$2:$B${OBS_LAST}));MONTH(MAX(${S_OBS}.$B$2:$B${OBS_LAST}));1))')
    put(st,'B5','採用する集計月初'); put(st,'C5','=IF(ISNUMBER(C3);DATE(YEAR(C3);MONTH(C3);1);C4)')
    put(st,'B6','集計月末'); put(st,'C6','=IF(C5="";"";DATE(YEAR(C5);MONTH(C5)+1;0))')
    for a in ['C3','C4','C5','C6']: st.getCellRangeByName(a).NumberFormat=FD
    put(st,'B8','9・10：7日前の記録がないとき6日前・8日前で代用する'); put(st,'C8','いいえ')
    put(st,'D8','いいえ＝7日ちょうど前の記録のみ（初期値）／はい＝7日前→6日前→8日前の順に探す（マニュアルの事例は6～8日間隔の週1評価）')
    put(st,'B9','9・10：治癒の扱い'); put(st,'C9','マニュアル準拠')
    put(st,'D9','評価日（その病棟の当月最終記録日＝退院・転出日または月末）が、病棟入室日と発見日の遅い方から7日以上なら、1週間前の評価がなくても分母・分子に含める')
    put(st,'B11','計画書の照合：入院日の何日前まで遡るか'); put(st,'C11',100)
    put(st,'B12','計画書の照合：入院日の何日後まで認めるか'); put(st,'C12',7)
    put(st,'B15','集計する病棟（表示名）'); put(st,'C15','照合コード（自動）')
    for i,w in enumerate(['3階東','3階南','4階','','','','','']):
        r=16+i
        if w: put(st,f'B{r}',w)
        put(st,f'C{r}',f'=IF(TRIM(B{r})="";"";{NW(f"B{r}")})')
    put(st,'D16','病棟名の「病棟」「階」、全角数字、空白は自動で無視して照合します（３階東病棟＝3階東＝3東）')
    v=st.getCellRangeByName('C8').Validation
    from com.sun.star.sheet.ValidationType import LIST
    v.Type=LIST; v.setFormula1('"はい";"いいえ"'); v.ShowErrorMessage=True
    st.getCellRangeByName('C8').Validation=v
    YEL=0xFFF2CC
    for a in ['C3','C8','C11','C12','B16:B23']: st.getCellRangeByName(a).CellBackColor=YEL
    st.Columns.getByIndex(1).Width=9500; st.Columns.getByIndex(2).Width=3600; st.Columns.getByIndex(3).Width=16000

    # ---- 処理シート ----
    so=sheets.getByName(S_OBS); fill(so,OBS_COLS,obs_row,N_OBS)
    for c in ['B','H','J','O','AT']: so.getCellRangeByName(f'{c}2:{c}{OBS_LAST}').NumberFormat=FD
    sa=sheets.getByName(S_ADM); fill(sa,ADM_COLS,adm_row,N_ADM)
    for c in ['B','F']: sa.getCellRangeByName(f'{c}2:{c}{ADM_LAST}').NumberFormat=FD
    sp=sheets.getByName(S_PLN); fill(sp,PLN_COLS,pln_row,N_PLN)
    sp.getCellRangeByName(f'B2:B{PLN_LAST}').NumberFormat=FD
    for sh,cols,last in [(so,OBS_COLS,OBS_LAST),(sa,ADM_COLS,ADM_LAST),(sp,PLN_COLS,PLN_LAST)]:
        hdr=sh.getCellRangeByName(f'A1:{cols[-1][0]}1')
        hdr.CellBackColor=0xDDEBF7; hdr.IsTextWrapped=True; hdr.CharWeight=150
        sh.Rows.getByIndex(0).Height=1800
        for i in range(len(cols)): sh.Columns.getByIndex(i).Width=2300
    for i in range(30,37): so.Columns.getByIndex(i).Width=2000
    so.Columns.getByIndex(12).Width=4200

    # ---- 集計結果 ----
    rs=sheets.getByName(S_RES)
    put(rs,'A1','DiNQL 褥瘡ケアの取組み 4・5・8・9・10 集計結果')
    put(rs,'H1','Created by Asaki')
    put(rs,'A2','集計月'); c=put(rs,'C2',f'=IF({M1}="";"データなし";{M1})'); c.NumberFormat=FYM
    put(rs,'D2',f'=IF(ISNUMBER(${S_SET}.$C$3);"（設定シートで手入力）";"（自動判定：観察記録の最新記録日 "&TEXT(MAX(${S_OBS}.$B$2:$B${OBS_LAST});"YYYY/MM/DD")&"）")')
    put(rs,'A4','■ 点検（NG があれば数値を転記しないでください。「情報」は結果に影響しません）')
    OB=lambda col: f'${S_OBS}.${col}$2:${col}${OBS_LAST}'
    AD=lambda col: f'${S_ADM}.${col}$2:${col}${ADM_LAST}'
    def hdrchk(sheet,pairs):
        conds=[]
        for addr,txt,partial in pairs:
            ref=f'${sheet}.${addr[0:len(addr.rstrip("0123456789"))]}${addr[len(addr.rstrip("0123456789")):]}'
            conds.append(f'ISNUMBER(FIND("{txt}";{ref}))' if partial else f'TRIM({ref})="{txt}"')
        return f'=IF(AND({";".join(conds)});"OK";"NG：列の並びが想定と違います（CSVの出力設定を確認）")'
    checks=[
     ('列構成：褥瘡状況貼付',hdrchk(P_OBS,[('B7','患者ID',0),('D7','登録日',0),('J7','病棟',0),('L7','褥瘡有無',0),('N7','深さ',1),('U7','合計点',1),('X7','発見日',0),('Y7','発生場所',0)]),'7行目の見出しで確認'),
     ('列構成：褥瘡予防対策診療計画書',hdrchk(P_PLN,[('B7','患者ID',0),('D7','登録日',0),('H7','病棟',0),('AH7','判定',0)]),'7行目の見出しで確認'),
     ('列構成：入院一覧',hdrchk(P_ADM,[('A1','入院日',0),('B1','病棟',0),('E1','患者ID',0)]),'1行目の見出しで確認'),
     ('行数：褥瘡状況貼付',f'=IF(COUNTA(${P_OBS}.$B$8:$B$200000)>{N_OBS};"NG：処理できる上限{N_OBS}行を超えています";"OK（"&COUNTA(${P_OBS}.$B$8:$B$200000)&"行）")',f'上限{N_OBS}行'),
     ('行数：褥瘡予防対策診療計画書',f'=IF(COUNTA(${P_PLN}.$B$8:$B$200000)>{N_PLN};"NG：処理できる上限{N_PLN}行を超えています";"OK（"&COUNTA(${P_PLN}.$B$8:$B$200000)&"行）")',f'上限{N_PLN}行'),
     ('行数：入院一覧',f'=IF(COUNTA(${P_ADM}.$E$2:$E$200000)>{N_ADM};"NG：処理できる上限{N_ADM}行を超えています";"OK（"&COUNTA(${P_ADM}.$E$2:$E$200000)&"行）")',f'上限{N_ADM}行'),
     ('並び順（患者ごと・記録日の古い順）',f'=IF(SUM({OB("AE")})=0;"OK";"NG："&SUM({OB("AE")})&"行。カルテ側の標準の並びで出力し直してください")','処理_観察 AE列'),
     ('日付を読み取れない記録',f'=IF(SUM({OB("AH")})=0;"OK";"NG："&SUM({OB("AH")})&"行")','処理_観察 AH列'),
     ('「有」なのに発見日・発生場所・合計点が空欄',f'=IF(SUM({OB("AG")})=0;"OK";"要確認："&SUM({OB("AG")})&"行（その褥瘡は集計から外れます）")','処理_観察 AG列'),
     ('新規褥瘡で深さの分類が不明',f'=IF(COUNTIFS({OB("AB")};1;{OB("AM")};0)=0;"OK";"要確認："&COUNTIFS({OB("AB")};1;{OB("AM")};0)&"名（処理_観察 F列）")','8の内訳から漏れます'),
     ('集計期間外の記録（月初7日前より前など）',f'=IF(SUM({OB("AI")})=0;"OK";"情報："&SUM({OB("AI")})&"行（集計には使いません）")','処理_観察 AI列'),
     ('入院一覧に入院日が見つからない記録',f'=IF(SUM({OB("AJ")})=0;"OK";"情報："&SUM({OB("AJ")})&"行（在院7日以上として扱います）")','長期入院など'),
     ('設定にない病棟の記録',f'=IF(SUM({OB("AK")})=0;"OK";"情報："&SUM({OB("AK")})&"行（集計表に出ません）")','設定シートの病棟一覧'),
     ('該当患者一覧の行数（上限'+str(int(os.environ.get('N_LIST','1000')))+'）',f'=IF(MAX({OB("AO")})>{int(os.environ.get("N_LIST","1000"))};"NG：一覧に表示しきれません";"OK（"&MAX({OB("AO")})&"名）")','該当患者一覧シート'),
     ('同じ患者・同じ日の重複記録',f'=IF(SUM({OB("AF")})=0;"OK";"情報："&SUM({OB("AF")})&"行")','処理_観察 AF列'),
    ]
    r0=5
    for i,(lab,fml,note) in enumerate(checks):
        r=r0+i; put(rs,f'B{r}',lab); put(rs,f'D{r}',fml); put(rs,f'H{r}',note)
    # 本表
    T=r0+len(checks)+2   # 見出し行
    WCOLS=['C','D','E','F','G','H','I','J']
    put(rs,f'A{T}','項目'); put(rs,f'B{T}','内容（DiNQLの入力欄）')
    for i,wc in enumerate(WCOLS):
        put(rs,f'{wc}{T}',f'=IF(${S_SET}.$B${16+i}="";"";${S_SET}.$B${16+i})')
    rows=[]
    def cnt(*crit):   # crit: (col, criterion) on 処理_観察
        return ';'.join(f'{OB(c)};{v}' for c,v in crit)
    R=[]  # (item,label,kind,payload)
    R.append(('4','A：1ヶ月間に危険因子の評価を実施した患者数（当月入院で計画書あり・入院した病棟）','adm',[('C','CODE'),('G','1')]))
    R.append(('','参考：当月入院患者数（入院一覧・実人数）','adm',[('C','CODE'),('E','1')]))
    R.append(('','点検：うち計画書が見つからない患者数','adm',[('C','CODE'),('J','1')]))
    R.append(('',None,'blank',None))
    R.append(('5','A：危険因子を有する患者数（褥瘡なし・実人数）','obs',[('C','CODE'),('AD','1')]))
    R.append(('','既に褥瘡を有していた患者数（B＋C）','sum',(1,2)))
    R.append(('','B：入院時・転入時に既に褥瘡を有していた患者数','obs',[('C','CODE'),('X','1'),('L','"持込"')]))
    R.append(('','C：先月以前に自病棟で発生した褥瘡を有していた患者数','obs',[('C','CODE'),('X','1'),('L','"先月以前"')]))
    R.append(('','参考：計画書でリスク有り（当月入院・入院した病棟）','adm',[('C','CODE'),('H','1')]))
    R.append(('','点検：うち当月の観察記録がない患者数','adm',[('C','CODE'),('K','1')]))
    R.append(('',None,'blank',None))
    R.append(('8','DESIGN-R®分類別 新たに褥瘡を生じた患者数（新規発生ごとに、発見日の深さで数える）','text',None))
    for lab,cr in [('d1','1'),('d2','2'),('D3','3'),('D4','4'),('D5','5'),('DTI','6'),('U（DU）','7')]:
        R.append(('',f'　{lab}','obs',[('C','CODE'),('AB','1'),('AM',cr)]))
    R.append(('','　合計（褥瘡推定発生率の分子A）','sumrange',7))
    R.append(('','　うち d2以上（d1以外）','sumrange2',6))
    R.append(('',None,'blank',None))
    R.append(('9','病棟で新たに褥瘡が生じた患者のうち、1週間前の評価がある患者数','obs',[('C','CODE'),('L','"新規"'),('Y','1')]))
    R.append(('','1週間前の評価から改善した患者数','obs',[('C','CODE'),('L','"新規"'),('Z','1')]))
    R.append(('',None,'blank',None))
    R.append(('10','既に褥瘡を有していた患者のうち、1週間前の評価がある患者数','sum',(1,2)))
    R.append(('','（内訳）入院時・転入時に既に褥瘡を有していた患者','obs',[('C','CODE'),('L','"持込"'),('Y','1')]))
    R.append(('','（内訳）先月以前に自病棟で発生した褥瘡を有していた患者','obs',[('C','CODE'),('L','"先月以前"'),('Y','1')]))
    R.append(('','既に褥瘡を有していた患者のうち、1週間前の評価から改善した患者数','sum',(1,2)))
    R.append(('','（内訳）入院時・転入時に既に褥瘡を有していた患者','obs',[('C','CODE'),('L','"持込"'),('Z','1')]))
    R.append(('','（内訳）先月以前に自病棟で発生した褥瘡を有していた患者','obs',[('C','CODE'),('L','"先月以前"'),('Z','1')]))
    r=T+1
    for item,lab,kind,pl in R:
        if item: put(rs,f'A{r}',item)
        if lab: put(rs,f'B{r}',lab)
        for i,wc in enumerate(WCOLS):
            code=f'${S_SET}.$C${16+i}'
            if kind in ('obs','adm'):
                ref=OB if kind=='obs' else AD
                crit=';'.join(f'{ref(c)};{code if v=="CODE" else v}' for c,v in pl)
                put(rs,f'{wc}{r}',f'=IF({code}="";"";COUNTIFS({crit}))')
            elif kind=='sum':
                put(rs,f'{wc}{r}',f'=IF({code}="";"";{wc}{r+pl[0]}+{wc}{r+pl[1]})')
            elif kind=='sumrange':
                put(rs,f'{wc}{r}',f'=IF({code}="";"";SUM({wc}{r-pl}:{wc}{r-1}))')
            elif kind=='sumrange2':
                put(rs,f'{wc}{r}',f'=IF({code}="";"";SUM({wc}{r-1-pl}:{wc}{r-2}))')
        if kind=='text' and item=='4':
            pass
        r+=1
    END=r
    notes=[
     '■ 使い方',
     '1. 3枚の貼付シート（褥瘡状況貼付・褥瘡予防対策診療計画書・入院一覧）の中身を全消去（Ctrl+A → Delete）してから、CSVの中身をそれぞれ A1 に貼り付ける。行・列・シートの削除はしない。',
     '2. 上の点検がすべて OK（または情報）であることを確認し、集計表の数値を DiNQL に転記する（率はDiNQL側で自動計算）。',
     '3. 該当患者の確認：「該当患者一覧」シート（ID・氏名・数えられた項目）。点検で数が出た当月入院患者も同シート右側に表示。',
     '■ CSV出力ルール（研究支援から抽出）',
     '計画書：調査月とその3ヶ月前　／　褥瘡状況処置：調査月とその1週間前（7日ちょうど前の評価を参照するため必須）　／　入院一覧：調査月とその3ヶ月前',
     '褥瘡状況は、カルテ側の標準の並び（患者ごと・記録日の古い順）のまま出力する。',
     '■ 判定ルール（要約）',
     '区分：発生場所≠記録病棟、または発見日＜入院日 → 持込（入院時・転入時）／自病棟で発見日が集計月より前 → 先月以前／自病棟で集計月内 → 新規',
     '治癒：同じ患者で前の記録も治癒なら「無」扱い（治癒済み）。最初の治癒の記録日を治癒日とする。',
     '9・10：褥瘡ごとに当月最後の記録日を評価日とし、7日前の同じ病棟の記録と合計点を比較（下がれば改善）。治癒は、評価日（病棟の当月最終記録日）が病棟入室日・発見日の遅い方から7日以上なら、1週間前の評価がなくても分母・分子に含める。9は今月新規発生した褥瘡ごとに判定し、患者単位で1人と数える。',
     '5A：当月その病棟に観察記録がある患者のうち、B（持込）・C（先月以前）に該当しない患者（新規発生の患者を含む）。',
     '8：新規褥瘡ごとに、発見日の記録の深さで数える（DTI・Uも差し替えない）。同じ月に発生→治癒→発生した患者は2回数える。同じ褥瘡の記録が途切れても1回。',
     '4：当月入院した患者のうち、計画書（入院前の入退院支援での評価を含む）がある患者を入院した病棟で数える（マニュアル：対象月に評価した患者のみ）。',
    ]
    for i,t in enumerate(notes): put(rs,f'A{END+1+i}',t)
    # 体裁
    rs.Columns.getByIndex(0).Width=1300; rs.Columns.getByIndex(1).Width=16500
    for i in range(2,10): rs.Columns.getByIndex(i).Width=2100
    rs.getCellRangeByName('A1').CharHeight=14; rs.getCellRangeByName('A1').CharWeight=150
    rs.getCellRangeByName('C2').CharHeight=13; rs.getCellRangeByName('C2').CharWeight=150
    rs.getCellRangeByName(f'A{T}:J{T}').CellBackColor=0xDDEBF7; rs.getCellRangeByName(f'A{T}:J{T}').CharWeight=150
    rs.getCellRangeByName(f'C{T+1}:J{END-1}').HoriJustify=uno.Enum('com.sun.star.table.CellHoriJustify','CENTER')
    for i in range(len(checks)):
        rs.getCellRangeByName(f'D{r0+i}:G{r0+i}').merge(True)
    # ---- 該当患者一覧 ----
    sheets.insertNewByName('該当患者一覧',8)
    sl=sheets.getByName('該当患者一覧')
    NL=int(os.environ.get("N_LIST","1000")); NA=200
    put(sl,'A1','該当患者一覧（集計月にその病棟で観察記録がある患者。○＝その項目に数えられた）')
    put(sl,'A2','病棟で絞り込むには3行目のオートフィルタを使う。「10」の分母・改善は既に有していた褥瘡（持込＝入院時・転入時、先月以前＝自病棟で先月以前に発生）。')
    hdr=['No','病棟','患者ID','氏名','5A 危険因子あり','5B 持込','5C 先月以前','8 新規(深さ)','9 分母','9 改善',
         '10 持込 分母','10 持込 改善','10 先月以前 分母','10 先月以前 改善','','処理行','病棟コード']
    sl.getCellRangeByName('A3:Q3').setDataArray((tuple(hdr),))
    oR=lambda c: f'${S_OBS}.${c}$2:${c}${OBS_LAST}'
    def ex(r,col,lab): return f'=IF($P{r}="";"";IF(COUNTIFS({oR("A")};$C{r};{oR("C")};$Q{r};{oR("L")};"{lab}";{oR(col)};1)>0;"○";""))'
    rowsL=[]
    for r in range(4,4+NL):
        mx=f'MAXIFS({oR("AM")};{oR("A")};$C{r};{oR("C")};$Q{r})'
        rowsL.append((
          f'=IF($P{r}="";"";ROW()-3)',
          f'=IF($P{r}="";"";IFERROR(INDEX(${S_SET}.$B$16:$B$23;MATCH($Q{r};${S_SET}.$C$16:$C$23;0));$Q{r}))',
          f'=IF($P{r}="";"";INDEX({oR("A")};$P{r}))',
          f'=IF($P{r}="";"";INDEX({oR("AL")};$P{r}))',
          f'=IF($P{r}="";"";IF(INDEX({oR("AD")};$P{r})=1;"○";""))',
          ex(r,'X','持込'), ex(r,'X','先月以前'),
          f'=IF($P{r}="";"";IF({mx}=0;"";CHOOSE({mx};"d1";"d2";"D3";"D4";"D5";"DTI";"U")))',
          ex(r,'Y','新規'), ex(r,'Z','新規'), ex(r,'Y','持込'), ex(r,'Z','持込'), ex(r,'Y','先月以前'), ex(r,'Z','先月以前'),
          '',
          f'=IFERROR(MATCH(ROW()-3;{oR("AN")};0);"")',
          f'=IF($P{r}="";"";INDEX({oR("C")};$P{r}))'))
    sl.getCellRangeByName(f'A4:Q{3+NL}').setFormulaArray(tuple(rowsL))
    aR=lambda c: f'${S_ADM}.${c}$2:${c}${ADM_LAST}'
    put(sl,'S1','点検が必要な当月入院患者（計画書・観察記録）')
    sl.getCellRangeByName('S3:X3').setDataArray((('No','病棟','患者ID','氏名','内容','処理行'),))
    rowsA=[]
    for r in range(4,4+NA):
        rowsA.append((
          f'=IF($X{r}="";"";ROW()-3)',
          f'=IF($X{r}="";"";IFERROR(INDEX(${S_SET}.$B$16:$B$23;MATCH(INDEX({aR("C")};$X{r});${S_SET}.$C$16:$C$23;0));INDEX({aR("C")};$X{r})))',
          f'=IF($X{r}="";"";INDEX({aR("A")};$X{r}))',
          f'=IF($X{r}="";"";INDEX({aR("L")};$X{r}))',
          f'=IF($X{r}="";"";IF(INDEX({aR("J")};$X{r})=1;"計画書が見つからない";"計画書でリスク有りだが当月の観察記録なし"))',
          f'=IFERROR(MATCH(ROW()-3;{aR("N")};0);"")'))
    sl.getCellRangeByName(f'S4:X{3+NA}').setFormulaArray(tuple(rowsA))
    for a in ['A3:Q3','S3:X3']:
        h=sl.getCellRangeByName(a); h.CellBackColor=0xDDEBF7; h.CharWeight=150; h.IsTextWrapped=True
    sl.Rows.getByIndex(2).Height=1500
    widths={0:1000,1:1800,2:2200,3:3200,7:2200,14:400,15:1600,16:1800,17:400,18:1000,19:1800,20:2200,21:3200,22:7500,23:1600}
    for i in range(24): sl.Columns.getByIndex(i).Width=widths.get(i,1700)
    sl.getCellRangeByName(f'E4:N{3+NL}').HoriJustify=uno.Enum('com.sun.star.table.CellHoriJustify','CENTER')
    for i in (15,16,23): sl.Columns.getByIndex(i).IsVisible=False
    # 計算設定：正規表現・ワイルドカード無効、セル内容全体一致、表示精度OFF
    doc.RegularExpressions=False; doc.Wildcards=False; doc.MatchWholeCell=True; doc.CalcAsShown=False
    # オートフィルタ
    dbr=doc.DatabaseRanges
    for nm,sh,cols,last,idx,r0_ in [('F_観察',so,OBS_COLS,OBS_LAST,5,0),('F_入院',sa,ADM_COLS,ADM_LAST,6,0),('F_計画書',sp,PLN_COLS,PLN_LAST,7,0),('F_一覧',sl,[('Q','')],3+NL,8,2)]:
        a=CellRangeAddress(); a.Sheet=idx; a.StartColumn=0; a.StartRow=r0_; a.EndColumn=col_idx(cols[-1][0]); a.EndRow=last-1
        if dbr.hasByName(nm): dbr.removeByName(nm)
        dbr.addNewByName(nm,a); dbr.getByName(nm).AutoFilter=True
    sheets.moveByName('該当患者一覧',1)
    doc.calculateAll()
    # ODF 1.2 拡張で保存（LibreOffice 5.4 互換）
    cp=ctx.ServiceManager.createInstanceWithContext("com.sun.star.configuration.ConfigurationProvider",ctx)
    node=cp.createInstanceWithArguments("com.sun.star.configuration.ConfigurationUpdateAccess",(pv("nodepath","/org.openoffice.Office.Common/Save/ODF"),))
    node.setPropertyValue("DefaultVersion",9); node.commitChanges()
    doc.storeToURL("file://"+os.path.abspath(out_path),(pv("FilterName","calc8"),))
    doc.close(True); proc.terminate()

if __name__=='__main__':
    main(sys.argv[1],sys.argv[2])
