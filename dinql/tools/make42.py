import sys
from uno_common import *
src,dst,variant=sys.argv[1],sys.argv[2],sys.argv[3]
L=6001
proc,ctx,desk=start(); doc=load(desk,src)
so=doc.Sheets.getByName('処理_観察')
so.getCellRangeByName(f'AB2:AB{L}').setFormulaArray(tuple((f'=IF(AA{r}=1;IF(COUNTIFS($M$2:M{r};M{r};$AA$2:AA{r};1)=1;1;0);0)',) for r in range(2,L+1)))
so.getCellRangeByName(f'AM2:AM{L}').setFormulaArray(tuple((f'=IF(AB{r}=1;AP{r};0)',) for r in range(2,L+1)))
so.getCellRangeByName(f'V2:V{L}').setFormulaArray(tuple((f'=IF(P{r}=1;IF(AND(U{r}=1;OR(Q{r}=1;S{r}=1));1;0);0)',) for r in range(2,L+1)))
n=0
if variant=='AM':
    rs=doc.Sheets.getByName('集計結果')
    for r in range(0,80):
        for c in range(0,10):
            cell=rs.getCellByPosition(c,r); f=cell.getFormula()
            if '$AU$' in f: cell.setFormula(f.replace('$AU$','$AM$')); n+=1
print('replaced',n)
doc.calculateAll()
doc.storeToURL('file://'+os.path.abspath(dst),(pv('FilterName','calc8'),))
doc.close(True); proc.terminate()
