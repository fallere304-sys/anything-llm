import json,sys
E=[('A：1ヶ月間に危険因子の評価',[4,2,5]),('参考：当月入院患者数',[4,3,5]),('計画書が見つからない',[0,1,0]),
 ('A：危険因子を有する',[2,4,4]),('（B＋C）',[3,1,3]),('B：入院時・転入時に既に褥瘡を有していた患者数',[1,1,2]),
 ('C：先月以前に自病棟',[2,0,1]),('計画書でリスク有り',[3,2,5]),('観察記録がない',[1,0,0]),
 ('d1',[1,0,1]),('d2',[0,1,2]),('D3',[2,0,0]),('D4',[0,0,0]),('D5',[0,0,0]),('DTI',[0,1,0]),('U（DU）',[0,1,0]),
 ('合計（褥瘡推定',[3,3,3]),('うち d2以上',[2,3,2]),
 ('病棟で新たに褥瘡が生じた患者のうち',[2,3,2]),('1週間前の評価から改善した患者数',[1,1,0]),
 ('既に褥瘡を有していた患者のうち、1週間前の評価がある',[3,1,1]),('#B10',[1,1,1]),('#D10',[2,0,0]),
 ('既に褥瘡を有していた患者のうち、1週間前の評価から改善',[2,1,1]),('#A10',[1,1,1]),('#C10',[1,0,0])]
d=json.load(open(sys.argv[1])); tol=len(sys.argv)>2
items=list(d['table'].items())
def find(key):
    if key.startswith('#'):
        idx={'#B10':0,'#D10':1,'#A10':3,'#C10':4}[key]
        base=[i for i,(k,v) in enumerate(items) if '既に褥瘡を有していた患者のうち、1週間前の評価がある' in k][0]
        return items[[base+1,base+2,None,base+4,base+5][idx]]
    for k,v in items:
        lab=k.split(':',1)[1].strip()
        if lab==key or (len(key)>4 and key in lab): return (k,v)
ok=True; ng=[]
for key,exp in E:
    exp=list(exp)
    if tol and key=='#D10': exp=[2,0,1]
    if tol and key=='既に褥瘡を有していた患者のうち、1週間前の評価がある': exp=[3,1,2]
    k,v=find(key); got=[int(x) for x in v]
    if got!=exp: ok=False; ng.append((k.split(':',1)[1][:30],got,exp))
print('ALL OK (26項目×3病棟)' if ok else 'MISMATCH '+str(len(ng)))
for x in ng: print('  NG',x)
print('点検NG:',[ (k,v) for k,v in d['checks'].items() if k and not v.startswith('OK')],'| エラーセル',d['obs_err_cells'],'| 計算',d['calc_sec'],'秒')
print('一覧:'); [print('  ',x) for x in d['list'] if '200017' in x or '200018' in x or '200019' in x or '200015' in x or 'No' in x]
