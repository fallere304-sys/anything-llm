import json,sys
E=[('A：1ヶ月間に危険因子の評価',[4,2,4]),('参考：当月入院患者数',[4,3,4]),('計画書が見つからない',[0,1,0]),
 ('A：危険因子を有する',[2,3,3]),('（B＋C）',[3,1,3]),('B：入院時・転入時に既に褥瘡を有していた患者数',[1,1,2]),
 ('C：先月以前に自病棟',[2,0,1]),('計画書でリスク有り',[3,2,4]),('観察記録がない',[1,0,0]),
 ('d1',[0,0,1]),('d2',[0,1,1]),('D3',[2,0,0]),('D4',[0,1,0]),('D5',[0,0,0]),('DTI',[0,0,0]),('U（DU）',[0,0,0]),
 ('合計（褥瘡推定',[2,2,2]),('うち d2以上',[2,2,1]),
 ('病棟で新たに褥瘡が生じた患者のうち',[2,2,1]),('1週間前の評価から改善した患者数',[1,1,0]),
 ('既に褥瘡を有していた患者のうち、1週間前の評価がある',[3,1,1]),('#B10',[1,1,1]),('#D10',[2,0,0]),
 ('既に褥瘡を有していた患者のうち、1週間前の評価から改善',[2,1,1]),('#A10',[1,1,1]),('#C10',[1,0,0])]
d=json.load(open(sys.argv[1])); tol=len(sys.argv)>2
items=list(d['table'].items())
def find(key):
    if key.startswith('#'):
        idx={'#B10':0,'#D10':1,'#A10':3,'#C10':4}[key]
        base=[i for i,(k,v) in enumerate(items) if '既に褥瘡を有していた患者のうち、1週間前の評価がある' in k][0]
        order=[base+1,base+2,None,base+4,base+5]
        return items[order[idx]]
    for k,v in items:
        lab=k.split(':',1)[1].strip()
        if (lab==key) or (len(key)>4 and key in lab): return (k,v)
ok=True
for key,exp in E:
    exp=list(exp)
    if tol and key=='#D10': exp=[2,0,1]
    if tol and key=='既に褥瘡を有していた患者のうち、1週間前の評価がある': exp=[3,1,2]
    k,v=find(key); got=[int(x) for x in v]
    flag='OK' if got==exp else 'NG'
    if flag=='NG': ok=False
    print(flag,k.split(':',1)[1][:38].ljust(38),'got',got,'exp',exp)
print('ALL OK' if ok else 'MISMATCH', '| checks NG:',[k for k,v in d['checks'].items() if k and not v.startswith('OK')],'| err',d['obs_err_cells'],'| calc',d['calc_sec'])
