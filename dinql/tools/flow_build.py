from uno_common import *
import uno
from com.sun.star.awt import Point, Size
proc,ctx,desk=start(); doc=load(desk,sys.argv[1])
pages=doc.DrawPages; p1=pages.getByIndex(0)
T={'rect':p1.getByIndex(1),'round':p1.getByIndex(69),'down':p1.getByIndex(21),'right':p1.getByIndex(19),'left':p1.getByIndex(50)}
PROPS=['Style','FillStyle','FillColor','FillTransparence','LineStyle','LineColor','LineWidth','LineTransparence',
 'TextHorizontalAdjust','TextVerticalAdjust','TextWordWrap','TextAutoGrowHeight','TextLeftDistance','TextRightDistance',
 'TextUpperDistance','TextLowerDistance']
CHAR=['CharFontName','CharFontNameAsian','CharFontNameComplex','CharFontFamily','CharFontFamilyAsian','CharFontPitch','CharFontPitchAsian','CharColor','CharFontStyleName','CharFontCharSet','CharFontCharSetAsian']
snap={}
for k,t in T.items():
    d={}
    for n in PROPS:
        try: d[n]=t.getPropertyValue(n)
        except Exception: pass
    d['_geom']=t.CustomShapeGeometry
    cur=t.getText().createTextCursor()
    d['_char']={}
    for n in CHAR:
        try: d['_char'][n]=cur.getPropertyValue(n)
        except Exception: pass
    snap[k]=d
def shape(page,kind,x,y,w,h,text='',pt=18,bold=False,left=False):
    s=doc.createInstance('com.sun.star.drawing.CustomShape')
    page.add(s)
    s.Position=Point(x,y); s.Size=Size(w,h)
    d=snap[kind]
    s.CustomShapeGeometry=d['_geom']
    for n,v in d.items():
        if n.startswith('_'): continue
        try: s.setPropertyValue(n,v)
        except Exception: pass
    s.Position=Point(x,y); s.Size=Size(w,h)
    if text:
        s.setString(text)
        c=s.getText().createTextCursor(); c.gotoStart(False); c.gotoEnd(True)
        for n,v in d['_char'].items():
            try: c.setPropertyValue(n,v)
            except Exception: pass
        c.CharHeight=pt; c.CharHeightAsian=pt; c.CharHeightComplex=pt
        c.ParaAdjust=uno.Enum('com.sun.star.style.ParagraphAdjust','LEFT' if left else 'CENTER')
        s.TextVerticalAdjust=uno.Enum('com.sun.star.drawing.TextVerticalAdjust','CENTER')
        s.TextHorizontalAdjust=uno.Enum('com.sun.star.drawing.TextHorizontalAdjust','BLOCK')
        if bold: c.CharWeight=150; c.CharWeightAsian=150
    return s
R=lambda pg,x,y,w,h,t,pt=18,left=False: shape(pg,'rect',x,y,w,h,t,pt,False,left)
def DA(pg,x,y,w,h,t=''): return shape(pg,'down',x,y,w,h,t,16)
def RA(pg,x,y,w,h,t=''): return shape(pg,'right',x,y,w,h,t,16)
# remove old shapes on page1
while p1.Count>0: p1.remove(p1.getByIndex(0))
P=p1
shape(P,'round',3000,250,22000,950,'DiNQL褥瘡集計フローチャート①　患者の振り分け（5・8）',20)
R(P,300,1350,27400,800,'病棟ごとに、当月観察記録がある患者を1人ずつたどり、終了したら次の患者へ進みます',16)
def col(x,w,q,rec,judge):
    R(P,x,2400,w,2400,q,15)
    DA(P,x+w//2-1200,4800,2400,700,'YES')
    R(P,x,5500,w,800,rec,18)
    DA(P,x+w//2-700,6300,1400,600)
    R(P,x,6900,w,1200,judge,15)
    DA(P,x+w//2-700,8100,1400,600)
    R(P,x,8700,w,1400,'今月この病棟で新たな\n褥瘡も発生しましたか？',16)
    DA(P,x+300,10100,2400,700,'YES'); DA(P,x+w-2700,10100,2400,700,'NO')
    R(P,x,10800,4200,1200,'8に記載\n9の判定へ（②）',15)
    R(P,x+4500,10800,w-4500,1200,'終了',18)
    DA(P,x+1400,12000,1400,600)
    R(P,x+600,12600,3000,900,'終了',18)
col(300,7000,'入院時・転入時に既に\n褥瘡がありましたか？\n（持込／手術室発生）','5②に記載','10（持込）の改善判定へ\n→フローチャート②')
RA(P,7300,3100,1200,1000,'NO')
col(8500,7000,'先月以前に自病棟で発生し\n月初にも治っていない\n褥瘡がありますか？','5③に記載','10（先月以前）の改善判定へ\n→フローチャート②')
RA(P,15500,3100,1200,1000,'NO')
X=16700; W=6500
R(P,X,2400,W,2400,'5①に記載\n（危険因子あり・\n褥瘡なし）',16)
DA(P,X+W//2-700,4800,1400,700)
R(P,X,5500,W,1400,'今月この病棟で新たに\n褥瘡が発生しましたか？',15)
RA(P,X+W,5700,1100,1000,'NO')
R(P,X+W+1100,5500,27700-(X+W+1100),1400,'終了',18)
DA(P,X+W//2-1200,6900,2400,700,'YES')
R(P,X,7600,W,800,'8に記載',18)
DA(P,X+W//2-700,8400,1400,600)
R(P,X,9000,W,1200,'9の改善判定へ\n→フローチャート②',16)
DA(P,X+W//2-700,10200,1400,600)
R(P,X+W//2-1500,10800,3000,900,'終了',18)
R(P,X,11900,27700-X,3650,'【8】深さは発見時の評価（DTI・Uは判定後の深さ）\n　複数なら最も重い1つで数える\n　（U＞D5＞D4＞D3＞d2＞d1）\n【5②と5③の両方】重症度の高い方で1人\n【4】今月入院し計画書（入院前の評価を含む）が\n　ある患者を、入院した病棟で数える',12,True)
# ---------- page 2 ----------
pages.insertNewByIndex(0)
P2=pages.getByIndex(1)
P2.MasterPage=p1.MasterPage
try: P2.Layout=p1.Layout
except Exception: pass
shape(P2,'round',3000,250,22000,950,'DiNQL褥瘡集計フローチャート②　改善判定（9・10）',20)
R(P2,300,1350,27400,800,'評価日：当月に退院・転出した患者は退院・転出日、月末まで入院中の患者は月末（最終の記録日）',15)
R(P2,300,2400,7000,1900,'評価日のちょうど7日前に\nこの病棟の評価が\nありますか？',16)
RA(P2,7300,2850,1300,1000,'NO')
DA(P2,2600,4300,2400,700,'YES')
R(P2,300,5000,7000,800,'分母に記載',18)
DA(P2,3100,5800,1400,600)
R(P2,300,6400,7000,1900,'評価日の総得点が\n7日前より下がりましたか？\n（治癒＝0点を含む）',15)
RA(P2,7300,6850,1300,1000,'NO')
R(P2,8600,6750,3000,1200,'終了',18)
DA(P2,2600,8300,2400,700,'YES')
R(P2,300,9000,7000,800,'分子にも記載',18)
DA(P2,3100,9800,1400,600)
R(P2,2300,10400,3000,900,'終了',18)
R(P2,8600,2400,8400,2400,'評価日までに治癒し、評価日が\n発生日（10は病棟に入った日）\nから7日以上後ですか？',15)
RA(P2,17000,3100,1300,1000,'NO')
R(P2,18300,2800,4200,1600,'終了\n（数えない）',16)
DA(P2,11600,4800,2400,700,'YES')
R(P2,8600,5500,8400,800,'分母と分子の両方に記載',18)
RA(P2,17000,5400,1300,1000)
R(P2,18300,5300,3000,1200,'終了',18)
R(P2,300,11600,4200,850,'区分',16); R(P2,4500,11600,4800,850,'分母',16); R(P2,9300,11600,4800,850,'分子',16)
rows=[('9（新規）','9上段','9下段'),('10（持込）','10①上段と中段','10②上段と中段'),('10（先月以前）','10①上段と下段','10②上段と下段')]
for i,(a,b,c) in enumerate(rows):
    y=12450+i*1050
    R(P2,300,y,4200,1050,a,15); R(P2,4500,y,4800,1050,b,15); R(P2,9300,y,4800,1050,c,15)
R(P2,14800,8300,12900,7250,'【ポイント（マニュアルより）】\n・9は8で数えた褥瘡（最重症）で判定する\n・評価日の7日前が先月でも「評価あり」\n　例）8/1退院 → 7/25と比べる\n・4/1発生・4/6治癒・4/20退院\n　→ 分母・分子の両方に記載\n・7/25発生・7/27治癒・7/31月末\n　→ 発生から6日のため数えない\n・点数が同じなら改善に含めない',14,True)
doc.storeToURL('file://'+os.path.abspath(sys.argv[2]),(pv('FilterName','impress8'),))
doc.close(True); proc.terminate()
