import sys, uno, subprocess, time, os
from com.sun.star.beans import PropertyValue
def pv(n,v):
    p=PropertyValue(); p.Name=n; p.Value=v; return p
def start():
    proc=subprocess.Popen(['soffice','--headless','--invisible','--norestore','--accept=socket,host=localhost,port=2002;urp;'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    local=uno.getComponentContext()
    res=local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver",local)
    for i in range(90):
        try:
            ctx=res.resolve("uno:socket,host=localhost,port=2002;urp;StarOffice.ComponentContext");break
        except Exception: time.sleep(1)
    desk=ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop",ctx)
    return proc,ctx,desk
def load(desk,path):
    return desk.loadComponentFromURL("file://"+os.path.abspath(path),"_blank",0,(pv("Hidden",True),))
def colname(sh,c): return sh.getColumns().getByIndex(c).Name
