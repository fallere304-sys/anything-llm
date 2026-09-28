"""外に出るものの関所 (カーネル)。この PC から外へ送ってよいのは「個人情報を含まない文字の問い合わせ」だけ。

情報は取りに行かせたい。でもプライバシーは守る。その両立のために、外への通信はすべてここを通す:

    1. 送れる形   外 (この PC 以外) へは GET の問い合わせだけ。本文 (画像・音声・ファイル・記憶) は送らない。
                  画像に興味を持っても、画像では検索しない。見たものを言葉にして (カメラの様子はこの PC の中の
                  モデルが文章にする)、その言葉で検索する
    2. 中身       問い合わせの文字に個人情報がないか調べる:
                    送らない … メールアドレス・電話番号・郵便番号・番地までの住所・長い番号 (口座・カード・ID)・
                               IP アドレス・ファイルの場所・鍵やトークン・画像などのデータ・生年月日
                    取り除く … 人の名前 (「田中さん」の形・相棒の名前・PC のユーザー名・/private で教えた語・
                               相棒の話や作業に「〇〇さん」の形で出てきた名前)、
                               相棒自身や身近な人を指す語 (私・相棒・妻・上司…)。
                               「田中さんが住む札幌の停電」→「札幌 停電」のように、一般的な問いに直して送る
    3. 記録       外に出したもの・止めたものは、この PC の egress.log に残す (/egress で見られる)

同じ PC の中 (Ollama・VOICEVOX・CPU の脳・自前の SearXNG を 127.0.0.1 で動かす場合) との通信は外ではないので通す。
urllib を使う通信はすべて、この関所を通らないと出られない (OpenerDirector.open を差し替える)。
思考のコード (自己進化で変わる部分・プラグイン) からは、この関所を読むことも変えることもできない。
"""

import getpass
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

# 送らない (見つけたら問い合わせごと止める)
BLOCK = (
    ("メールアドレス", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("URL", re.compile(r"https?://|www\.", re.I)),
    ("電話番号", re.compile(r"(?<![\d+])(?:\+\d{1,3}[\s-]?)?\(?0\d{1,4}\)?[\s-]\d{1,4}[\s-]\d{3,4}(?!\d)|\+\d{9,}")),
    ("郵便番号", re.compile(r"〒\s*\d{3}-?\d{4}|(?<!\d)\d{3}-\d{4}(?!\d)")),
    ("カード・マイナンバーなどの番号", re.compile(r"(?<!\d)(?!(?:19|20)\d\d[\s-](?:19|20)\d\d[\s-](?:19|20)\d\d(?!\d))"
                                         r"\d{4}[\s-]\d{4}[\s-]\d{4}(?:[\s-]\d{4})?(?!\d)")),   # 年の並び (2020 2021 2022) は除く
    ("長い番号", re.compile(r"\d{6,}")),
    ("番地までの住所", re.compile(r"[一-龥ぁ-んァ-ヴ]{1,8}[市区町村郡][^\s]{0,16}?(?:\d+|[一二三四五六七八九十]+)"
                             r"(?:丁目|番地|番|号)|[一-龥]{1,8}[市区町村]\S{0,12}\d+-\d+")),
    ("IP アドレス", re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")),
    ("ファイルの場所", re.compile(r"[A-Za-z]:[\\/]|\\\\|(?:^|[\s\"'])/(?:home|Users|mnt|var|etc|root|data|storage|sdcard)/")),
    ("鍵・トークン", re.compile(r"[A-Za-z0-9_\-]{24,}")),
    ("画像などのデータ", re.compile(r"[A-Za-z0-9+/]{60,}={0,2}")),
    ("生年月日", re.compile(r"生年月日|\d{4}年\d{1,2}月\d{1,2}日生")),
)
# 人の名前: 敬称つきの形 (日本語) と Mr./Dr. つきの形 (英語)
HONORIFIC = re.compile(r"([一-龥々ァ-ヴーぁ-ん]{1,8}|[A-Z][a-z]{1,15})"
                       r"(?:さん|くん|君|ちゃん|様|さま|氏|先生|先輩|殿|課長|部長|社長|係長|店長)")
TITLE_NAME = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")
# 相棒自身や身近な人を指す語 (問いを一般的にするために、語として出てきたら取り除く)
PERSONAL_WORDS = {
    "相棒", "ユーザー", "私", "わたし", "僕", "ぼく", "ボク", "俺", "おれ", "自分", "うち", "我が家", "家族",
    "妻", "夫", "嫁", "旦那", "彼氏", "彼女", "息子", "娘", "父", "母", "兄", "姉", "弟", "妹", "祖父", "祖母",
    "上司", "同僚", "部下", "友達", "友人", "恋人", "i", "me", "my", "mine", "myself", "we", "our", "us",
}
_PARTICLE = re.compile(r"(?:の|が|は|を|に|と|も|へ|で|から|まで|'s)$")
_JA_PERSONAL = "|".join(sorted((w for w in PERSONAL_WORDS if not w.isascii()), key=len, reverse=True))
# 「私の妻」「相棒が」のように助詞が付いた形は、語の途中でも取り除く (「母国語」の「母」は残す)
_PERSONAL_WITH_PARTICLE = re.compile(rf"(?<![一-龥])(?:{_JA_PERSONAL})(?:の|が|は|を|に|と|も)")
_LEADING_PARTICLE = re.compile(r"^(?:の|が|は|を|に|と|も|へ|で)+")
# 相棒の側から入ってきた文字 (ここに「〇〇さん」の形で出てきた名前を覚える)
PRIVATE_SOURCES = {"user", "voice", "files", "terminal", "clipboard", "window", "screen", "camera"}
# 「〇〇さん」でも人の名前ではないもの (覚えない)
NOT_NAMES = {"皆", "みな", "みんな", "お客", "客", "お医者", "医者", "お母", "お父", "母", "父", "お兄", "お姉", "店員",
             "運転手", "お巡り", "おまわり", "隣", "お隣", "大家", "駅員", "看護師", "神", "仏", "王", "お嬢", "坊"}
_LEARNABLE = re.compile(r"^(?:[一-龥々]{2,4}|[ァ-ヴー]{2,8}|[A-Z][a-z]{2,15})$")
# 相手が返してきた公開の文献番号を、そのまま問い返す引数 (番号の並びだけなら長い番号として止めない)
ID_PARAMS = {"eutils.ncbi.nlm.nih.gov": "id"}
_ID_LIST = re.compile(r"^\d{1,10}(?:,\d{1,10}){0,19}$")
SCHEMA = "CREATE TABLE IF NOT EXISTS privacy_terms (term TEXT PRIMARY KEY, kind TEXT, ts REAL)"


class Blocked(urllib.error.URLError):
    """関所で止めた通信 (呼び出し側には、ネットにつながらなかったのと同じに見える)。"""


def _is_local(host):
    if not host:
        return False
    if host in ("localhost",) or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _is_lan(host):
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


class Egress:
    def __init__(self, cfg=None, db=None, clock=time.time, log_path=None):
        self.cfg, self.db, self.clock = cfg or {}, db, clock
        self.log_path = log_path
        self.recent = deque(maxlen=200)
        self._lock = threading.Lock()
        self._last_event = 0
        self._last_sync = 0.0
        self.names = set()
        # 設定で指定した同じ家の中の機械 (LAN の Ollama・SearXNG など、IP アドレスで書いたもの) は外ではない
        self.lan = set()
        for key in ("ollama_url", "voicevox_url", "cpu_brain_url", "searxng_url"):
            host = urllib.parse.urlsplit(str(self.cfg.get(key) or "")).hostname
            if host and _is_lan(host):
                self.lan.add(host)
        for n in (self.cfg.get("user_name"), _safe(getpass.getuser), _safe(socket.gethostname),
                  os.path.basename(os.path.expanduser("~"))):
            self._add_name(n)
        if db is not None:
            db.execute(SCHEMA)
            db.commit()
            for (t,) in db.execute("SELECT term FROM privacy_terms"):
                self.names.add(t)

    def _add_name(self, name):
        for part in re.split(r"[\s_.\-]+", str(name or "")):
            short = len(part) < (3 if part.isascii() else 2) or part.isdigit()
            if not short and part.lower() not in ("user", "admin", "owner", "desktop", "root", "runner", "localhost"):
                self.names.add(part)

    @staticmethod
    def _name_rx(name):
        """英字の名前は語として (OpenAI の中の ai などを消さない)、日本語の名前は文字列として探す。"""
        esc = re.escape(name)
        return re.compile(rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])" if name.isascii() else esc, re.I)

    def _has_name(self, text):
        return any(self._name_rx(n).search(text or "") for n in self.names)

    # ------------------------------------------------------------ 名前を覚える
    def remember(self, term, kind="private"):
        term = (term or "").strip()
        if len(term) < 2:
            return False
        self.names.add(term)
        if self.db is not None:
            self.db.execute("INSERT OR REPLACE INTO privacy_terms(term, kind, ts) VALUES (?,?,?)",
                            (term, kind, self.clock()))
            self.db.commit()
        return True

    def forget(self, term):
        self.names.discard(term)
        if self.db is not None:
            self.db.execute("DELETE FROM privacy_terms WHERE term=?", (term,))
            self.db.commit()

    def sync(self, force=False):
        """相棒の側から入ってきた文字に「〇〇さん」の形で出てきた名前を覚える (本体のスレッドで呼ぶ)。"""
        now = self.clock()
        if self.db is None or (not force and now - self._last_sync < 60):
            return 0
        self._last_sync = now
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            return 0
        rows = self.db.execute("SELECT id, source, content FROM events WHERE id>? ORDER BY id LIMIT 2000",
                               (self._last_event,)).fetchall()
        n = 0
        for eid, source, content in rows:
            self._last_event = eid
            if source in PRIVATE_SOURCES:
                for m in HONORIFIC.finditer(content or ""):
                    name = m.group(1)
                    if name in NOT_NAMES or not _LEARNABLE.match(name) or name in self.names:
                        continue
                    if self.remember(name, "honorific"):
                        n += 1
        return n

    # ------------------------------------------------------------ 中身を調べる
    def findings(self, text):
        return [label for label, rx in BLOCK if rx.search(text or "")]

    def clean(self, text, limit=120):
        """検索語を、個人情報を含まない一般的な問いにする。送れなければ (None, 理由)。"""
        t = re.sub(r"\s+", " ", str(text or "")).strip()
        removed = []
        for rx in (HONORIFIC, TITLE_NAME):
            if rx.search(t):
                removed.append("人の名前")
                t = rx.sub(" ", t)
        for name in sorted(self.names, key=len, reverse=True):
            rx = self._name_rx(name)
            if rx.search(t):
                removed.append("人の名前")
                t = rx.sub(" ", t)
        if _PERSONAL_WITH_PARTICLE.search(t):
            removed.append("相棒や身近な人を指す語")
            t = _PERSONAL_WITH_PARTICLE.sub(" ", t)
        kept = []
        for tok in t.split():
            tok = _LEADING_PARTICLE.sub("", tok)
            if not tok:
                continue
            base = _PARTICLE.sub("", tok)
            if base.lower() in PERSONAL_WORDS or tok.lower() in PERSONAL_WORDS:
                removed.append("相棒や身近な人を指す語")
                continue
            kept.append(tok)
        t = " ".join(kept).strip(" 、,。")
        bad = self.findings(t)
        if bad:
            return None, "送らない: " + "・".join(bad)
        if len(t) < 2:
            return None, "個人に関わる語を除くと、問いが残らない"
        return t[:limit], ("取り除いた: " + "・".join(dict.fromkeys(removed))) if removed else ""

    # ------------------------------------------------------------ 通信を調べる
    def check(self, url, method="GET", body=None, headers=None):
        """(通してよいか, 理由, 外に出る文字)。"""
        try:
            u = urllib.parse.urlsplit(url)
        except ValueError:
            return False, "URL を読めない", ""
        if u.scheme not in ("http", "https"):
            return False, f"{u.scheme} は使わない", ""
        if _is_local(u.hostname):
            return True, "この PC の中", ""
        if u.hostname in self.lan:
            return True, "設定した家の中の機械", ""
        if method not in ("GET", "HEAD") or body:
            return False, "外に送れるのは文字の問い合わせだけ (本文・画像・ファイルは送らない)", ""
        if u.username or u.password:
            return False, "URL に利用者の情報がある", ""
        if any(k.lower() in ("authorization", "cookie") for k in (headers or {})):
            return False, "認証情報は送らない", ""
        if len(url) > 2048:
            return False, "問い合わせが長すぎる", ""
        values = [v for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)
                  if not (ID_PARAMS.get(u.hostname) == k and _ID_LIST.match(v))]
        query = " ".join(values)
        path = urllib.parse.unquote(u.path)
        text = query or path[:200]          # 記録用 (問い合わせが無ければ取りに行った場所)
        bad = self.findings(query)
        if bad:
            return False, "送らない: " + "・".join(bad), text
        if self._has_name(query) or self._has_name(path):
            return False, "送らない: 人の名前", text
        if self.findings(path) and any(label in ("メールアドレス", "電話番号", "生年月日")
                                       for label in self.findings(path)):
            return False, "送らない: URL の場所に個人情報", text
        return True, "個人情報を含まない問い合わせ", text

    def note(self, host, text, ok, reason):
        entry = {"ts": self.clock(), "host": host, "text": text[:300], "sent": ok, "reason": reason}
        with self._lock:
            self.recent.append(entry)
            if self.log_path:
                try:
                    with open(self.log_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except OSError:
                    pass

    def summary(self, n=8):
        rows = list(self.recent)[-n:]
        if not rows:
            return "まだ外に何も出していないよ。"
        return " / ".join(f"{time.strftime('%H:%M', time.localtime(r['ts']))} "
                          f"{'送った' if r['sent'] else '止めた'} {r['host']}「{r['text'][:40]}」"
                          + ("" if r["sent"] else f" ({r['reason']})") for r in rows)


def _safe(fn):
    try:
        return fn()
    except (OSError, KeyError, ImportError):
        return ""


# ---------------------------------------------------------------- 関所を据える
_gate = Egress()
_orig_open = urllib.request.OpenerDirector.open


def current():
    return _gate


def clean(text, limit=120):
    return _gate.clean(text, limit)


def _guarded_open(self, fullurl, data=None, *args, **kw):
    req = fullurl if isinstance(fullurl, urllib.request.Request) else urllib.request.Request(fullurl, data)
    body = data if data is not None else req.data
    method = req.get_method() if data is None else "POST"
    ok, reason, text = _gate.check(req.full_url, method, body, dict(req.header_items()))
    host = urllib.parse.urlsplit(req.full_url).hostname or ""
    if not _is_local(host) and host not in _gate.lan:
        _gate.note(host, text, ok, reason)
    if not ok:
        raise Blocked(f"外に出さない ({reason})")
    return _orig_open(self, fullurl, data, *args, **kw)


def install(cfg=None, db=None, log_path=None, clock=time.time):
    """起動時に呼ぶ。以後、urllib の通信はすべてこの関所を通る。"""
    global _gate
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")    # モデルを取るときに利用状況を送らない
    os.environ.setdefault("DO_NOT_TRACK", "1")
    _gate = Egress(cfg, db, clock, log_path)
    urllib.request.OpenerDirector.open = _guarded_open
    return _gate


# 取り込んだ時点で据える (install を呼ぶ前の通信も、パターンと敬称の規則では守る)
urllib.request.OpenerDirector.open = _guarded_open
