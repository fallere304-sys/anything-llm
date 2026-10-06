"""録音後の処理パイプライン（ワーカースレッドで実行）。

UI とはキューでのみ通信する（Tk はメインスレッド以外から触れないため）。
イベント:
  ("phase", index)                フェーズ開始
  ("progress", 0..1|None, msg)    進捗
  ("done", Result)                完了
  ("cancelled", None)             停止
  ("error", message)              失敗
"""
from __future__ import annotations

import csv
import gc
import json
import logging
import queue
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .audio import load_wav_16k
from .diarize import align_tracks, diarize_by_volume
from .errors import Cancelled
from .merge import format_utterances, label_transcript, rule_based_merge
from .models import ModelManager
from .recorder import RecordingResult

log = logging.getLogger(__name__)

PHASES = [
    "録音データの読み込み・時刻合わせ",
    "音量比較で話者識別中",
    "Whisper モデル準備中（初回はダウンロード）",
    "1つ目（マイク1）の文字起こし中",
    "2つ目（マイク2）の文字起こし中",
    "文字起こしに話者識別を付与中",
    "ローカルAIモデル準備中（初回はダウンロード）",
    "完成版（AI補正版）生成中",
    "結果を保存中",
]


@dataclass
class Result:
    session_dir: Path
    final_text: str
    draft_text: str
    mic1_text: str
    mic2_text: str
    timeline_text: str
    warnings: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, rec: RecordingResult, session_dir: Path, cfg: dict, models: ModelManager):
        self.rec = rec
        self.session_dir = session_dir
        self.cfg = cfg
        self.models = models
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self.cancel.set()

    # ------------------------------------------------------------------
    def _phase(self, i: int):
        if self.cancel.is_set():
            raise Cancelled()
        log.info("phase %d: %s", i, PHASES[i])
        self.events.put(("phase", i))

    def _report(self, p, msg):
        self.events.put(("progress", p, msg))

    def _run(self):
        try:
            self.events.put(("done", self._process()))
        except Cancelled:
            log.info("cancelled by user")
            self.events.put(("cancelled", None))
        except MemoryError:
            log.exception("out of memory")
            self.events.put(("error", "メモリ不足で処理できませんでした。config.json で小さいモデルを指定してください。"))
        except Exception as e:
            log.exception("pipeline failed")
            self.events.put(("error", f"{e}\n\n{traceback.format_exc(limit=3)}"))

    def _process(self) -> Result:
        cfg = self.cfg
        out = self.session_dir
        warnings = list(self.rec.warnings)
        threads = config.thread_count(cfg)

        # ① 録音データ → 16kHz mono、時間軸を揃える
        self._phase(0)
        t1, t2 = self.rec.tracks
        a1 = load_wav_16k(t1.path)
        a2 = load_wav_16k(t2.path)
        a1, a2, skew = align_tracks(a1, t1.first_sample_time, a2, t2.first_sample_time)
        wall = self.rec.wall_stop - self.rec.wall_start
        d1, d2 = t1.frames / t1.samplerate, t2.frames / t2.samplerate
        log.info("align: start skew=%.3fs wall=%.2fs mic1=%.2fs mic2=%.2fs", skew, wall, d1, d2)
        # 開始時刻を揃えた後の終端のずれ ≒ 2デバイス間のクロックのずれ
        drift = abs((t1.first_sample_time + d1) - (t2.first_sample_time + d2))
        log.info("align: estimated end drift=%.3fs", drift)
        if drift > 1.0:
            warnings.append(
                f"2本の録音の終端が {drift:.2f} 秒ずれています（デバイスのクロック差）。後半ほど話者識別の精度が下がる可能性があります。"
            )
        if len(a1) < 16000:
            raise RuntimeError("録音が短すぎます（1秒未満）。")

        # ②③ 音量比較による話者識別 → 録音開始からの時刻で記録
        self._phase(1)
        timeline = diarize_by_volume(a1, a2, cfg)
        segs = timeline.segments()
        (out / "speaker_timeline.json").write_text(
            json.dumps(
                {
                    "note": "録音開始（2本の時刻合わせ後）からの秒数",
                    "frame_sec": timeline.frame_sec,
                    "noise_floor_db": {"mic1": timeline.floor1_db, "mic2": timeline.floor2_db},
                    "balance_offset_db": timeline.balance_offset_db,
                    "balance_note": timeline.balance_note,
                    "segments": segs,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        with open(out / "speaker_timeline.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["start_sec", "end_sec", "speaker"])
            for s in segs:
                w.writerow([s["start"], s["end"], s["speaker"]])
        timeline_text = f"[入力差の補正] {timeline.balance_note}\n" + "\n".join(f"{s['start']:9.2f} - {s['end']:9.2f}  話者{s['speaker']}" for s in segs)

        # ④ Whisper で2本とも文字起こし
        self._phase(2)
        wdir = self.models.ensure_whisper(self.cancel, self._report)
        self._report(None, "Whisper モデルを読み込み中")
        model = load_whisper_safe(wdir, cfg, threads)
        from .transcribe import transcribe

        raw = {}
        for mic, audio, phase in ((1, a1, 3), (2, a2, 4)):
            self._phase(phase)
            raw[mic] = transcribe(model, audio, cfg, self.cancel, self._report, f"マイク{mic}を文字起こし中")
            (out / f"transcript_mic{mic}_raw.json").write_text(
                json.dumps(raw[mic], ensure_ascii=False, indent=2), encoding="utf-8"
            )
        del model
        gc.collect()  # LLM 読み込み前に Whisper のメモリを解放する
        del a1, a2

        # ⑤ 2本の文字起こしそれぞれに話者識別を付与
        self._phase(5)
        gap = float(cfg.get("utterance_gap_s", 1.0))
        u1 = label_transcript(raw[1], timeline, mic=1, gap_s=gap)
        u2 = label_transcript(raw[2], timeline, mic=2, gap_s=gap)
        draft = rule_based_merge(u1, u2)
        mic1_text = format_utterances(u1)
        mic2_text = format_utterances(u2)
        draft_text = format_utterances(draft, show_mic=True)
        (out / "transcript_mic1_labeled.txt").write_text(mic1_text, encoding="utf-8")
        (out / "transcript_mic2_labeled.txt").write_text(mic2_text, encoding="utf-8")
        (out / "transcript_rule_merged.txt").write_text(draft_text, encoding="utf-8")
        (out / "utterances.json").write_text(
            json.dumps(
                {"mic1": [u.to_dict() for u in u1], "mic2": [u.to_dict() for u in u2], "merged": [u.to_dict() for u in draft]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # ⑥ ローカル LLM で補正版を作成
        final_text = ""
        if not draft:
            warnings.append("発話が検出されなかったため、AI 補正は行いませんでした。")
        else:
            self._phase(6)
            try:
                lpath = self.models.ensure_llm(self.cancel, self._report)
                self._report(None, "ローカルAIモデルを読み込み中（数十秒かかることがあります）")
                from .llm import load_llm, refine

                llm = load_llm(lpath, cfg, threads)
                self._phase(7)
                final_text, w = refine(llm, draft, u1, u2, cfg, self.cancel, self._report)
                warnings.extend(w)
                del llm
                gc.collect()
            except Cancelled:
                raise
            except Exception as e:
                log.exception("LLM step failed")
                warnings.append(f"ローカルAIによる補正に失敗したため、規則ベース統合版を表示しています: {e}")
        if not final_text.strip():
            final_text = format_utterances(draft)

        self._phase(8)
        (out / "transcript_final.txt").write_text(final_text, encoding="utf-8")
        if warnings:
            (out / "warnings.txt").write_text("\n".join(warnings), encoding="utf-8")
        return Result(out, final_text, draft_text, mic1_text, mic2_text, timeline_text, warnings)


def load_whisper_safe(wdir: Path, cfg: dict, threads: int):
    from .transcribe import load_whisper

    try:
        return load_whisper(wdir, cfg, threads)
    except Exception as e:
        # int8 非対応 CPU 等のフォールバック
        log.exception("Whisper load failed with %s; retry float32", cfg.get("whisper_compute_type"))
        if cfg.get("whisper_compute_type") == "float32":
            raise RuntimeError(f"Whisper モデルを読み込めませんでした: {e}") from e
        return load_whisper(wdir, {**cfg, "whisper_compute_type": "float32"}, threads)
