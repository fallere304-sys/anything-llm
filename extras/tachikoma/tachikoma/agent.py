"""思考ループ本体。

    知覚 (毎 tick, LLM なし) → 評価 (新規性が高い出来事だけ LLM)
      → 好奇心 (低確度の仮説を選び、調べ、判定し、信念を更新)
      → 発話 (伝える価値 > 割り込みコスト のときだけ)
      → 睡眠 (長い無操作時に記憶を整理)
      → 学習 (さらに長い無操作時に、裏付けのある標本で LoRA ファインチューン)

音声会話モード (voice=true) では、注意状態 (attention.py) で振る舞いを切り替える:
  conversing  声で話しかけられた → 最優先で短く声で返す。聞き取りに自信がなければ聞き返す
  attending   人がいる/声がする → 観察と好奇心。他人の会話には割り込まない
  alone       誰もいない → 字幕付き動画で耳を鍛え (study.py)、耳と頭を学習する

学習標本は 2 経路で溜まる (dataset.py):
  - ユーザーとのやり取り: /good /bad 訂正、質問への回答、本人の申告
  - 自律調査: 仮説が外部の根拠で確定したとき、知識と「結論と整合した判定」を記録

性格の芯 (persona.py / epistemics.py) はプロンプトだけでなく仕組みとして実装している:
  知ったかぶりしない  返事の前に「答えに必要だが知らないこと」を洗い出し、調べると約束する。
                      調べ終わったら「さっきの、調べたよ！」と自分から報告する
  前提を疑う          発言の前提を点検し、怪しい前提は確かめる対象にする
  疑問を作る          確かめた事柄から「なぜ？」「もし違ったら？」を自分で作る (wonder)
  根拠と因果          論文・政府文書を格付けし、因果の主張は因果を示せる研究デザインでしか確信しない
  自分を疑う          確信の較正 (当たり外れの記録) で初期確信を縮め、確信した推定の反証も探す (challenge)

GPU 推論は 1 tick に最大 1 回。ユーザーの話しかけだけは即時・最優先。
"""

import re
import time

from . import curiosity, expression, prompts
from .attention import ALONE, Attention
from .llm import LLMError
from .dataset import TrainingData
from .epistemics import Calibration, shrink_p
from .memory import FACT, INFERENCE, REFUTED, SPECULATION, Memory, entropy
from .persona import DIARY_SYSTEM, SelfModel, diary_input
from .text import clip, overlap

# 判定 → 対数オッズの変化量 (プローブの信頼度を掛けて使う)
VERDICT_WEIGHT = {"supports": 2.5, "partially_supports": 1.0, "contradicts": -3.0, "irrelevant": 0.0}

# 評価で得た命題の初期値。LLM に確率を言わせず、根拠の種類だけ選ばせる。
BASIS_PRIOR = {"observed": (0.95, "observation"), "inferred": (0.65, "reflection"),
               "guessed": (0.5, "reflection")}

SITUATION_HALF_LIFE = 2 * 3600


class Tachikoma:
    def __init__(self, cfg, llm, memory: Memory, sensors, probes, out=print,
                 clock=time.time, idle_fn=None, data=None, learner=None,
                 attention=None, tts=None, study=None, asr_learner=None,
                 eyes=None, eye_learner=None, idle=None, ui=None, power=None):
        self.cfg, self.llm, self.memory = cfg, llm, memory
        self.data = data or TrainingData(memory)
        self.learner = learner
        self.tts, self.study, self.asr_learner = tts, study, asr_learner
        self.eyes, self.eye_learner = eyes, eye_learner
        self.idle, self.ui, self.power = idle, ui, power      # 独りの時間の使い方 / 画面 / 電力計
        self.bits_resolved = 0.0                              # 調べて解消した不確実性の累計 (知識の伸び)
        self._last_activity = None
        self.attention = attention or Attention(cfg, clock, idle_fn,
                                                has_camera=getattr(probes, "camera", None) is not None)
        self.mode = None
        self.want_look = False
        self.selfm = SelfModel(memory, clock)
        self.calib = Calibration(memory)
        self.sensors, self.probes = sensors, probes
        self.out, self.clock = out, clock
        self.idle_fn = idle_fn or (lambda: None)
        now = clock()
        self.last_tick = now
        self.last_activity = now
        self.last_speak = 0.0
        self.situation = ""
        self.question_outstanding = None   # 質問キューにある信念 id
        self.asked = None                   # 実際に尋ねて回答待ちの信念 id
        self.slept = False
        self.backoff_until = 0.0
        self.last_wonder = now
        self.last_diary = now

    # ------------------------------------------------------------------ loop
    def run_forever(self):
        self.say("起動しました。見てます。")
        while True:
            self.step()
            time.sleep(self.cfg["tick_seconds"])

    def learners(self):
        return [x for x in (self.learner, self.asr_learner, self.eye_learner) if x is not None]

    def step(self):
        now = self.clock()
        for s in self.sensors:
            for item in s.poll():
                self.perceive(s.name, item[0], item[1], item[2] if len(item) > 2 else None)
        self.memory.decay_relevance(now - self.last_tick, self.cfg["relevance_half_life_s"])
        self.last_tick = now

        mode = self.attention.mode(now)
        if mode != self.mode:
            self.log(f"注意: {self.mode} → {mode}")
            self.mode = mode
        if mode != ALONE:
            # 誰かいる: 独りの時間の活動 (自習・学習) はすぐやめて GPU を返す
            for st in (self.study, self.eyes):
                if st is not None:
                    st.pause()
            for lr in self.learners():
                if lr.busy:
                    lr.abort()
        for lr in self.learners():
            msg = lr.poll()
            if msg:
                self.say(msg)
        training = any(lr.busy for lr in self.learners())
        scheduled = self.idle is not None and mode == ALONE
        # 学習中は GPU を学習プロセスに明け渡す (知覚と記録だけ続ける)
        if not training and now >= self.backoff_until and self.llm.gate.can_run_background():
            try:
                if self.want_look:
                    self.look_around()
                elif scheduled:
                    self.appraise_next()     # 独りの時間の調べものは、スケジューラが「読書」として選んだときだけ
                else:
                    self.appraise_next() or self.curiosity_step() or self.wonder()
            except LLMError as e:
                self.log(f"推論失敗、30秒待ちます: {e}")
                self.backoff_until = now + 30
        if self.idle is not None:
            try:
                act = self.idle.step(alone=(mode == ALONE))
                if act and act != self._last_activity:
                    d = self.idle.describe()
                    self.log(f"独りの時間: {act} を選んだ ({d['why'] if d else ''})")
                self._last_activity = act
            except Exception as e:  # noqa: BLE001 — 独りの時間の活動の失敗で本体を止めない
                self.log(f"独りの時間の活動でエラー: {e}")
                if self.idle.current:
                    self.idle._end(interrupted=True)
        elif mode == ALONE and not training and self.study is not None:
            try:
                self.study.step(self.cfg["study_step_budget_s"])
            except Exception as e:  # noqa: BLE001 — 自習の失敗で本体を止めない
                self.log(f"自習エラー: {e}")
                self.study.state = "idle"
        self.maybe_speak()
        self.maybe_sleep()
        if self.idle is None:
            self.maybe_learn()
        self.push_state()

    # ------------------------------------------------------------- perceive
    def perceive(self, source, kind, content, meta=None):
        now = self.clock()
        meta = meta or {}
        voice = False
        if kind == "user_message":
            self.attention.on_addressed()
        elif kind in ("person_appeared", "person_left"):
            self.attention.on_presence(kind == "person_appeared")
            if kind == "person_appeared" and self.cfg["camera_describe"]:
                self.want_look = True
        elif kind == "speech":
            self.attention.on_speech()
            if self.attention.is_addressed(content):
                self.attention.on_addressed()
                text = self.attention.strip_wake_word(content)
                if meta.get("avg_logprob", 0.0) < self.cfg["asr_min_logprob"]:
                    # 聞き取りの低確度推定 → いちばん安い情報収集は「聞き返す」こと
                    eid = self.memory.add_event(source, "unclear_speech", content, 0.0)
                    self.memory.mark_appraised(eid, 2)   # 聞き取れていない文から推論はしない
                    self.say("ごめん、よく聞き取れなかった。もう一回言って？")
                    return
                if not text:
                    self.say("はい、なに？")
                    return
                fb = voice_feedback(text)
                if fb is not None:
                    content = "/good" if fb[0] == "good" else f"/bad {fb[1]}"
                kind, content, voice = "user_message", content if fb else text, True
            else:
                kind = "overheard_speech"     # 他の人の会話・テレビ等。記録と評価はするが返事はしない
        if kind == "user_message" and content.startswith("/"):
            self.last_activity = now
            self.on_command(content)
            return
        novelty = self.memory.novelty(source, content)
        eid = self.memory.add_event(source, kind, content, novelty)
        self.memory.touch_relevance(content)
        self.last_activity = now
        self.slept = False
        if kind == "user_message":
            try:
                self.on_user_message(content, voice=voice)
            except LLMError as e:
                self.say(f"(推論に失敗しました: {e})")
        elif novelty < self.cfg["novelty_threshold"]:
            self.memory.mark_appraised(eid, 2)   # 既視感: 考えるまでもない

    def on_user_message(self, text, voice=False):
        if self.ui is not None:
            self.ui.push({"type": "user", "text": text})
        for lr in self.learners():
            if lr.busy:
                lr.abort()     # ユーザー最優先: 学習を止めて GPU を返してもらう
                msg = lr.poll()
                self.say(msg or "学習を中断しました。")
        if self.study is not None:
            self.study.pause()
        if self.asked is not None:
            b = self.memory.get_belief(self.asked)
            if b is not None:
                verdict = self.judge(b, f"仮説: {b.statement}\n根拠 (ユーザーの回答): {text}", "user")
                if verdict.get("verdict") != "irrelevant":
                    self.apply_verdict(b, verdict, "user", 1.0, "ユーザー回答: " + clip(text, 200))
                    self.asked = None
        user = self.context_text() + "\n\n# ユーザーの発言\n" + text
        check = self.inquire(text) if self.cfg["inquiry_on_chat"] else ""
        # 即効層: 学習前でも、過去に裏付けの取れた例をプロンプトに添える
        shots = "\n".join(x for x in (self.data.examples("knowledge", text),
                                       self.data.examples("chat", text)) if x)
        system = self.selfm.system_prompt(self.cfg, voice=voice, calibration=self.calib.stats())
        try:
            reply = self.llm.chat(system, (shots + "\n\n" if shots else "") + user + check,
                                  max_tokens=160 if voice else 400,
                                  temperature=self.cfg.get("chat_temperature", 0.5))
        except LLMError as e:
            self.say(f"(推論に失敗しました: {e})")
            return
        if voice:
            reply = shorten(reply, self.cfg["voice_max_reply_chars"])
        self.say(reply)
        self.attention.on_self_spoke()      # 続けて呼びかけ語なしで返事できるようにする
        self.memory.add_event("self", "reply", reply, 0.0)
        self.data.remember_chat(system, user, reply)

    def inquire(self, text):
        """答える前の点検: 怪しい前提と、知らないことを洗い出して「調べる対象」にする。

        返り値は返事のプロンプトに足す点検結果。知らないことは知らないと言い、調べると約束させる。"""
        try:
            res = self.llm.chat(prompts.INQUIRY_SYSTEM, self.context_text() + "\n\n# 発言\n" + text,
                                schema=prompts.INQUIRY_SCHEMA, max_tokens=300)
        except LLMError:
            return ""
        doubtful, unknown = [], []
        for pr in (res.get("premises") or [])[:3]:
            st = (pr.get("statement") or "").strip()
            if st and pr.get("doubtful"):
                self._hypothesis(st, 0.5, relevance=1.0, basis="premise")
                doubtful.append(st)
        for u in (res.get("unknowns") or [])[:3]:
            u = (u or "").strip()
            if u:
                bid = self._hypothesis(u, 0.5, relevance=1.0, basis="unknown")
                self.memory.set_flag(bid, "promised")
                unknown.append(u)
        if not doubtful and not unknown:
            return ""
        lines = ["\n\n# 返事の前の点検"]
        if doubtful:
            lines.append("- 怪しい前提 (まずここを確かめたいと伝える): " + " / ".join(doubtful))
        if unknown:
            lines.append("- ボクがまだ知らないこと (知ったかぶりせず、調べると約束する): " + " / ".join(unknown))
        return "\n".join(lines)

    def _hypothesis(self, statement, p, relevance, basis):
        """新しい仮説を記憶し、予測として記録する (後で当たり外れを較正に使う)。"""
        p = shrink_p(p, self.calib.shrink(self.cfg.get("persona_skepticism", 0.6)))
        bid = self.memory.add_belief(statement, p, "reflection", relevance=relevance,
                                     half_life=SITUATION_HALF_LIFE * 12)
        self.calib.predict(bid, self.memory.get_belief(bid).p, basis)
        return bid

    def wonder(self):
        """疑問を作る: 確かめた事柄から「なぜ？」「もし違ったら？」を自分で立てる。"""
        now = self.clock()
        if now - self.last_wonder < self.cfg["wonder_interval_s"] or self.attention.conversing(now):
            return False
        self.last_wonder = now
        known = [b for b in self.memory.beliefs() if b.label() in (FACT, INFERENCE)]
        known.sort(key=lambda b: -b.relevance)
        if not known:
            return False
        res = self.llm.chat(prompts.WONDER_SYSTEM, "\n".join(f"- [{b.label()}] {b.statement}" for b in known[:3]),
                            schema=prompts.WONDER_SCHEMA, max_tokens=200, temperature=0.8)
        made = 0
        for h in (res.get("hypotheses") or [])[:2]:
            if (h or "").strip():
                self._hypothesis(h.strip(), 0.5, relevance=0.6, basis="wonder")
                made += 1
        if made:
            self.selfm.bump("questions", made)
            self.log(f"疑問を {made} 個つくった")
        return made > 0

    def on_command(self, text):
        cmd, _, arg = text.partition(" ")
        arg = arg.strip()
        if cmd == "/good":
            last = self.data.last_chat
            ok = self.data.feedback(True)
            if ok and last:
                self.selfm.remember("褒められた: " + clip(last[2], 80), "praise")
            self.say("覚えておきます。" if ok else "評価できる直前の応答がありません。")
        elif cmd == "/bad":
            if arg:
                self.selfm.remember("訂正された: " + clip(arg, 80), "correction")
            ok = self.data.feedback(False, arg)
            if not ok:
                self.say("評価できる直前の応答がありません。")
            else:
                self.say("訂正を覚えておきます。" if arg else
                         "記録しました。正解を `/bad 正しい答え` の形で教えてもらえると学習に使えます。")
        elif cmd == "/learn":
            if self.learner is None:
                self.say("学習機能は無効です。")
            else:
                self.say(self.learner.start("手動") or "学習できる標本がないか、既に学習中です。")
        elif cmd == "/rollback":
            if self.learner is None:
                self.say("学習機能は無効です。")
            else:
                self.say(f"{self.learner.rollback()} に戻しました。")
        elif cmd == "/ear_learn":
            if self.asr_learner is None:
                self.say("耳の学習は無効です。")
            else:
                self.say(self.asr_learner.start("手動") or "学習できる標本 (字幕の検証分を含む) が足りないか、学習中です。")
        elif cmd == "/eye_learn":
            if self.eye_learner is None:
                self.say("目の学習は無効です。")
            else:
                self.say(self.eye_learner.start("手動") or "学習できる行 (検証分を含む) が足りないか、学習中です。")
        elif cmd == "/eye_rollback":
            if self.eye_learner is None:
                self.say("目の学習は無効です。")
            else:
                self.say(f"目を {self.eye_learner.rollback()} に戻しました。")
        elif cmd == "/idle":
            if self.idle is None:
                self.say("独りの時間のスケジューラは無効です。")
            else:
                st = self.idle.stats()
                self.say("独りの時間の見込み (1Wh あたりの伸び / 試行回数): " + ", ".join(
                    f"{a} {v['rate']:.2f}/{v['n']}" for a, v in st.items()))
        elif cmd == "/ear_rollback":
            if self.asr_learner is None:
                self.say("耳の学習は無効です。")
            else:
                self.say(f"耳を {self.asr_learner.rollback()} に戻しました。")
        elif cmd == "/diary":
            self.say(self.selfm.get("last_diary") or "まだ日記を書いてないんです。今夜書きますね！")
        elif cmd == "/self":
            c = self.calib.stats()
            self.say(f"ボクは生まれて {self.selfm.days_alive()} 日目！ 興味: {'、'.join(self.selfm.top_interests()) or 'これから'}"
                     f" / これまで: {self.selfm.get('counters', {})}"
                     + (f" / 確信の当たり具合 Brier {c['brier']:.3f} (確信過剰度 {c['overconfidence']:+.2f}, n={c['n']})"
                        if c["n"] else ""))
        elif cmd == "/status":
            model = self.learner.active_model() if self.learner else self.llm.model
            busy = [f"{lr.stage}" for lr in self.learners() if lr.busy]
            lines = [f"注意: {self.attention.mode()} / モデル: {model}" + (f" / 学習中: {busy[0]}" if busy else ""),
                     f"標本: {self.data.stats()} / 未学習: {self.data.count_new()} 件"]
            if self.study is not None:
                rc = self.study.recent_cer()
                ear = self.asr_learner.active_model() if self.asr_learner else "-"
                lines.append(f"耳: {ear} / 自習: {self.study.state} / 最近の CER: "
                             f"{'-' if rc is None else f'{rc:.3f}'} / 未学習の聞き間違い: {self.study.count_new_hard()} 件")
            if self.eyes is not None:
                rc = self.eyes.recent_cer()
                eye = self.eye_learner.active_model() if self.eye_learner else "-"
                lines.append(f"目: {eye} / 自習: {self.eyes.state} / 最近の CER: {'-' if rc is None else f'{rc:.3f}'}"
                             f" / 未学習の読み間違い: {self.eyes.count_new_hard()} 行")
            if self.idle is not None and self.idle.describe():
                d = self.idle.describe()
                lines.append(f"独りの時間: {d['activity']} ({d['why']})")
            self.say(" | ".join(lines))
        else:
            self.say("コマンド: /good, /bad [正しい答え], /learn, /rollback, /ear_learn, /ear_rollback, "
                     "/eye_learn, /eye_rollback, /idle, /status, /self, /diary")

    # ------------------------------------------------------------- appraise
    def appraise_next(self):
        now = self.clock()
        for ev in self.memory.pending_events():
            if now - ev["ts"] > 600:      # 推論が追いつかず古くなった出来事は評価しない
                self.memory.mark_appraised(ev["id"], 3)
                continue
            break
        else:
            return False
        self.memory.mark_appraised(ev["id"])
        res = self.llm.chat(
            prompts.APPRAISE_SYSTEM,
            self.context_text() + f"\n\n# 新しい出来事 ({ev['source']}/{ev['kind']})\n{ev['content']}",
            schema=prompts.APPRAISE_SCHEMA, max_tokens=500,
            think=self.cfg.get("think_for_hypotheses", False))
        if res.get("situation"):
            self.situation = res["situation"]
        for c in res.get("claims", [])[:4]:
            st = (c.get("statement") or "").strip()
            if not st:
                continue
            p, source = BASIS_PRIOR.get(c.get("basis"), BASIS_PRIOR["guessed"])
            if source == "reflection":
                # 確信過剰が続いていたら、推論だけの初期確信を 0.5 側に縮める (自分を疑う)
                p = shrink_p(p, self.calib.shrink(self.cfg.get("persona_skepticism", 0.6)))
            # 「本文に書いてある」と言うなら本文と重なっているはず。重ならなければ格下げ
            if source == "observation" and overlap(st, ev["content"]) < 0.3:
                p, source = BASIS_PRIOR["inferred"]
            elif source == "observation" and ev["source"] == "user":
                source = "user"
            bid = self.memory.add_belief(st, p, source, relevance=1.0, half_life=SITUATION_HALF_LIFE,
                                         evidence=f"{ev['kind']}#{ev['id']}")
            if source == "reflection":
                self.calib.predict(bid, self.memory.get_belief(bid).p, c.get("basis", "guessed"))
            if source == "user":
                # 本人の申告は、それ自体が学習できる知識
                b = self.memory.get_belief(bid)
                if b.label() == FACT:
                    self.data.on_resolved(b)
        remark, imp = (res.get("remark") or "").strip(), res.get("remark_importance")
        if remark and imp in ("low", "high"):
            self.memory.add_utterance("remark", remark, 0.85 if imp == "high" else 0.45)
        return True

    # ------------------------------------------------------------ curiosity
    def allowed_probes(self):
        allowed = ["search_memory", "wait_observe"]
        if self.cfg["watch_dirs"]:
            allowed += ["grep_workspace", "read_file"]
        if getattr(self.probes, "web", None) is not None:
            allowed.append("web_search")
        if getattr(self.probes, "scholar", None) is not None or getattr(self.probes, "web", None) is not None:
            allowed.append("research")
        if getattr(self.probes, "camera", None) is not None:
            allowed.append("look")
        if self.cfg["allow_ask_user"] and self.question_outstanding is None and self.asked is None:
            allowed.append("ask_user")
        return allowed

    def curiosity_step(self):
        max_att = self.cfg["max_probe_attempts"]
        waiting = (self.asked, self.question_outstanding)   # 回答待ちの仮説は重ねて調べない
        beliefs = [b for b in self.memory.beliefs(include_irreducible=False) if b.id not in waiting]
        can_challenge = "research" in self.allowed_probes()
        if curiosity.drive(beliefs, max_att) < self.cfg["curiosity_threshold"]:
            # 気になる仮説が無い = 自分の「わかったつもり」を疑う番
            return self.challenge_step(beliefs) if can_challenge else False
        if can_challenge and self._rand() < self.cfg["challenge_share"] and self.challenge_step(beliefs):
            return True
        target, u = curiosity.pick_target(beliefs, max_att)
        if target is None:
            return False
        # 期待情報利得/コストの順に並べて提示し、選択肢は enum で縛る (小型モデルの迷いを減らす)
        ranked = [s.name for s in curiosity.rank_probes(target, self.allowed_probes())]
        plan = self.llm.chat(
            prompts.PLAN_SYSTEM,
            f"# 現在の状況\n{self.situation or '(不明)'}\n\n# 確かめたい仮説\n{target.statement}"
            f"\n\n# 使える調べ方\n" + "\n".join(f"- {n}: {curiosity.PROBES[n].description}" for n in ranked),
            schema=prompts.plan_schema(ranked), max_tokens=120)
        name, query = plan.get("probe"), (plan.get("query") or "").strip()
        if name not in ranked:
            name = ranked[0]
        spec = curiosity.PROBES[name]
        self.log(f"好奇心 {u:.2f}: 「{target.statement}」を {name}({query}) で調べる")

        if name == "ask_user":
            q = query or f"「{target.statement}」で合ってる?"
            self.memory.add_utterance("question", q, min(1.0, 0.65 + 0.3 * u), target.id)
            self.question_outstanding = target.id
            self.memory.update_belief(target.id, 0.0, target.source, count_attempt=True)
            return True

        evidence = self.probes.run(name, query or target.statement)
        meta = {}
        if isinstance(evidence, tuple):
            evidence, meta = evidence
        ev_key = f"{name}: {clip(evidence, 200)}" if evidence else None
        if ev_key and any(ev_key[len(name) + 2:] == e.split(": ", 1)[-1] for e in target.evidence):
            evidence = None     # 既に見た根拠: 同じ証拠を二重に数えない (独立な根拠ではない)
        if not evidence:
            nb = self.memory.update_belief(target.id, 0.0, target.source, count_attempt=True)
            self._maybe_give_up(nb)
            return True
        verdict = self.judge(target, f"仮説: {target.statement}\n\n根拠 ({name}):\n{clip(evidence, 2500)}",
                             spec.source)
        self.apply_verdict(target, verdict, spec.source, meta.get("reliability", spec.reliability), ev_key,
                           causal_evidence=meta.get("causal_design", False))
        return True

    def _rand(self):
        import random
        return random.random()

    def challenge_step(self, beliefs):
        """反証探し: 確信している推定のうち、まだ反証を探していないものを 1 つ疑ってみる。"""
        cands = [b for b in beliefs if b.label() == INFERENCE and not b.challenged and b.relevance > 0.2]
        if not cands:
            return False
        b = max(cands, key=lambda x: x.relevance)
        self.memory.set_flag(b.id, "challenged")
        plan = self.llm.chat(prompts.PLAN_SYSTEM, f"# 反証を探したい推定\n{b.statement}\n\n# 使える調べ方\n"
                             f"- challenge: {curiosity.PROBES['challenge'].description}",
                             schema=prompts.plan_schema(["challenge"]), max_tokens=120)
        res = self.probes.run("challenge", (plan.get("query") or b.statement).strip())
        self.log(f"反証探し: 「{b.statement}」")
        if not res:
            return True
        evidence, meta = res if isinstance(res, tuple) else (res, {})
        verdict = self.judge(b, f"仮説: {b.statement}\n\n根拠 (反証探し):\n{clip(evidence, 2500)}", "research")
        self.apply_verdict(b, verdict, "research", meta.get("reliability", 0.7), f"challenge: {clip(evidence, 200)}",
                           causal_evidence=meta.get("causal_design", False))
        return True

    def apply_verdict(self, b, verdict, source, reliability, evidence, causal_evidence=False):
        v = verdict.get("verdict", "irrelevant")
        before, p_before = b.label(), b.p_now()
        if v == "irrelevant":
            nb = self.memory.update_belief(b.id, 0.0, b.source, count_attempt=True)
            self._maybe_give_up(nb)
            return nb
        nb = self.memory.update_belief(b.id, VERDICT_WEIGHT.get(v, 0.0) * reliability, source,
                                       evidence=evidence, count_attempt=True,
                                       causal_evidence=causal_evidence and v != "contradicts")
        after = nb.label()
        self.log(f"更新: {nb!r} ({verdict.get('reason', '')})")
        gain = entropy(p_before) - entropy(nb.p_now())
        if gain > 0:
            self.bits_resolved += gain
        if after != before and gain > 0:
            # わかった! = 不確実性が減った分だけ、その話題への興味が育つ
            self.selfm.learned(nb.statement, gain)
        if after != before and after in (FACT, REFUTED):
            added, dropped = self.data.on_resolved(nb)
            self.log(f"学習標本 +{added} (結論と矛盾した判定 {dropped} 件は不採用)")
            self.calib.resolve(nb.id, 1 if after == FACT else 0)
            pred = self.db_prediction(nb.id)
            if after == REFUTED and pred is not None and pred > 0.6:
                self.selfm.remember(f"確信していたのに外れた: {clip(nb.statement, 60)}", "mistake")
        if nb.promised and after in (FACT, INFERENCE, REFUTED) and after != before:
            # 知らないと言ったことを、知にした → 自分から報告する
            self.memory.set_flag(nb.id, "promised", 0)
            self.memory.add_utterance(
                "finding", f"ねえねえ、さっきわからなかった「{clip(nb.statement, 60)}」、調べたよ！ → [{after}] "
                           f"{verdict.get('reason', '')}".strip(), 0.9, nb.id)
            return nb
        if before == SPECULATION and after in (FACT, INFERENCE, REFUTED):
            self.memory.add_utterance(
                "finding", f"確かめた: {nb.statement} → [{after}] {verdict.get('reason', '')}".strip(),
                0.35 + 0.5 * nb.relevance, nb.id)
        else:
            self._maybe_give_up(nb)
        return nb

    def db_prediction(self, bid):
        row = self.memory.db.execute("SELECT p FROM predictions WHERE belief_id=?", (bid,)).fetchone()
        return row["p"] if row else None

    def judge(self, b, user, source):
        """判定し、後で結論が出たときの事後ラベル付けのために記録しておく。"""
        shots = self.data.examples("judge", user)
        verdict = self.llm.chat(prompts.JUDGE_SYSTEM, (shots + "\n\n" if shots else "") + user,
                                schema=prompts.JUDGE_SCHEMA, max_tokens=160)
        self.data.record_judgment(b.id, user, verdict, source)
        return verdict

    def _maybe_give_up(self, b):
        """何度調べても減らない不確実性は『今は解けない』として好奇心の対象から外す。"""
        if b and b.attempts >= self.cfg["max_probe_attempts"] and b.label() == SPECULATION:
            self.memory.mark_irreducible(b.id)
            self.log(f"保留: 「{b.statement}」は今は確かめられない")

    # ---------------------------------------------------------------- speak
    def maybe_speak(self):
        now = self.clock()
        if now - self.last_speak < self.cfg["min_speak_interval_s"]:
            return
        if self.cfg["voice"]:
            if self.attention.mode(now) == ALONE:
                return          # 誰もいない部屋に話しかけない (15分以内に人が来たら話す)
            busy = self.attention.someone_talking(now) and not self.attention.conversing(now)
        else:
            idle = self.idle_fn()
            if idle is None:
                idle = now - self.last_activity
            busy = idle < self.cfg["busy_idle_s"]
        for u in self.memory.pending_utterances():
            if now - u["ts"] > 900:     # 15分前の話はもう「目の前」ではない
                self.memory.mark_delivered(u["id"], -1)
                if u["kind"] == "question" and u["belief_id"] == self.question_outstanding:
                    self.question_outstanding = None
                continue
            threshold = max(0.9, self.cfg["speak_threshold"]) if busy else self.cfg["speak_threshold"]
            if u["value"] < threshold:
                continue
            self.say(("❓ " if u["kind"] == "question" else "") + u["text"])
            self.memory.mark_delivered(u["id"])
            self.memory.add_event("self", u["kind"], u["text"], 0.0)
            if u["kind"] == "question":
                self.asked, self.question_outstanding = u["belief_id"], None
                self.attention.on_self_spoke()   # 返事を呼びかけ語なしで受けられるように
            self.last_speak = now
            return

    # ---------------------------------------------------------------- sleep
    def maybe_sleep(self):
        now = self.clock()
        if self.slept or now - self.last_activity < self.cfg["sleep_after_idle_s"]:
            return
        if not self.llm.gate.can_run_background():
            return
        self.slept = True
        removed = self.memory.prune(self.cfg["event_retention_days"])
        events = [e for e in self.memory.recent_events(60) if e["kind"] != "digest"]
        if events:
            log = "\n".join(f"- {e['source']}/{e['kind']}: {clip(e['content'], 160)}" for e in events)
            try:
                digest = self.llm.chat(prompts.DIGEST_SYSTEM, log, max_tokens=300)
                self.memory.add_event("self", "digest", digest, 0.0)
            except LLMError as e:
                self.log(f"要約失敗: {e}")
        self.write_diary()
        self.log(f"睡眠: 信念 {removed} 件を整理")

    def write_diary(self):
        """1 日 1 回、知ったこと・間違えたこと・まだわからないことを自分の言葉で書く。"""
        now = self.clock()
        if now - self.last_diary < 20 * 3600 and self.selfm.get("last_diary"):
            return
        since = now - 86400
        rows = self.memory.db.execute(
            "SELECT b.statement, p.p, p.outcome FROM predictions p JOIN beliefs b ON b.id=p.belief_id"
            " WHERE p.resolved_ts>=?", (since,)).fetchall()
        learned = [r["statement"] for r in rows if r["outcome"] == 1]
        wrong = [r["statement"] for r in rows if r["outcome"] == 0 and r["p"] > 0.6]
        open_q = [b.statement for b in sorted(self.memory.beliefs(), key=lambda b: -b.relevance)
                  if b.label() == SPECULATION][:5]
        if not (learned or wrong or open_q):
            return
        try:
            diary = self.llm.chat(DIARY_SYSTEM, diary_input(learned, wrong, open_q, self.calib.stats()),
                                  max_tokens=400, temperature=0.7)
        except LLMError:
            return
        self.last_diary = now
        self.selfm.set("last_diary", diary)
        self.memory.add_event("self", "diary", diary, 0.0)

    # ---------------------------------------------------------------- learn
    def maybe_learn(self):
        if not self.learners() or any(lr.busy for lr in self.learners()):
            return
        now = self.clock()
        if self.cfg["voice"] or self.attention.has_camera:
            idle = self.attention.alone_for(now)
        else:
            idle = self.idle_fn()
            if idle is None:
                idle = now - self.last_activity
        # 耳の学習を優先 (独りの時間の自習の成果をすぐ反映させる)。同時には走らせない
        for lr, reason in ((self.asr_learner, "独りの時間に耳の学習"), (self.learner, "睡眠中の自動学習")):
            if lr is not None and lr.should_train(idle, now):
                if self.study is not None:
                    self.study.pause()
                msg = lr.start(reason)
                if msg:
                    self.log(msg)
                    return

    def look_around(self):
        """人が現れた: カメラの様子を Gemma に説明させ、出来事として記録する。"""
        self.want_look = False
        desc = self.probes.run("look", "誰がいて何をしているか")
        if desc:
            self.memory.add_event("camera", "scene", desc, 1.0)

    # --------------------------------------------------------------- helpers
    def context_text(self, k=8):
        top = sorted(self.memory.beliefs(), key=lambda b: -b.relevance)[:k]
        lines = [f"# いま把握していること\n状況: {self.situation or '(まだ不明)'}"]
        for b in top:
            if b.label() == REFUTED:
                continue
            note = ""
            if b.source == "research" and b.evidence:
                note = f" (根拠: {clip(b.evidence[-1].split(': ', 1)[-1], 80)})"
            if b.claim_type == "causal" and not b.causal_ok:
                note += " (因果の根拠は相関どまり)"
            lines.append(f"- [{b.label()}] {b.statement}{note}")
        recent = [e for e in self.memory.recent_events(6) if e["kind"] != "digest"]
        if recent:
            lines.append("# 直近の出来事")
            lines += [f"- {e['source']}/{e['kind']}: {clip(e['content'], 200)}" for e in recent]
        return "\n".join(lines)

    def push_state(self):
        if self.ui is None:
            return
        d = self.idle.describe() if self.idle is not None else None
        thinking = bool(getattr(self.llm.gate, "busy", False))
        listening = self.attention.conversing() and self.cfg["voice"]
        self.ui.push({
            "type": "state", "mode": self.mode, "activity": d and {"activity": d["activity"], "why": d["why"]},
            "expression": expression.from_state(self.mode, d and d["activity"], thinking, listening),
            "speaking": bool(self.tts is not None and self.tts.speaking),
            "ear_cer": self.study.recent_cer() if self.study is not None else None,
            "eye_cer": self.eyes.recent_cer() if self.eyes is not None else None,
            "watts": round(self.power.watts(), 0) if self.power is not None else None,
            "model": getattr(self.llm, "model", None),
        })

    def say(self, text):
        self.out(f"[タチコマ {time.strftime('%H:%M')}] {text}")
        if self.ui is not None:
            self.ui.push({"type": "say", "text": text, "expression": expression.from_text(text)})
        # 声に出すのは誰かいるときだけ
        if self.cfg["voice"] and self.tts is not None and self.attention.mode() != ALONE:
            self.tts.speak(text)

    def log(self, text):
        if self.cfg.get("verbose"):
            self.out(f"  · {text}")


_GOOD = re.compile(r"^(それ)?(正解|合ってる|あってる|その通り|覚えといて|覚えておいて)[。!！]*$")
_BAD = re.compile(r"^(違う|ちがう)[よね、,。 ]*(正しくは|本当は|ほんとは)[、,: ]*(.+)$")


def voice_feedback(text):
    """声でのフィードバック。誤爆しないよう、はっきりした言い方だけを拾う。
    「正解」「覚えといて」→ good /「違う、正しくは○○」→ ○○ を正解として bad"""
    t = text.strip()
    if _GOOD.match(t):
        return ("good", None)
    m = _BAD.match(t)
    if m:
        return ("bad", m.group(3).strip())
    return None


def shorten(text, limit):
    """声の返事は短く。文の切れ目で切る。"""
    if len(text) <= limit:
        return text
    cut = max(text.rfind(c, 0, limit) for c in "。！？!?")
    return text[: cut + 1] if cut > 0 else text[:limit]
