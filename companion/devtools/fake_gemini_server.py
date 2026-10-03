"""開発用: 疑似 Gemini サーバー(APIキー無しで B案の通し確認をするため)。

- 会話: OpenAI 互換 SSE(/gemini/v1beta/openai/chat/completions)
    初回は delegate_task(Claude Code への依頼)を要求し、ツール結果を受けたら完了報告を返す
- 作成・調査: generateContent(/gemini/v1beta/models/{model}:generateContent)
    tools に googleSearch があれば出典付きの調査結果、なければ文書(Markdown)を返す
- 受けたリクエストの要約を、このファイルと同じ場所の fake_gemini_server.py.log に追記する

使い方(companion フォルダで):
    python -m uvicorn devtools.fake_gemini_server:app --port 11556
    # 別のターミナルで(Windows PowerShell の例)
    $env:LLM_PROVIDER="gemini"; $env:GEMINI_API_KEY="AIza-FAKE"; $env:LLM_MODEL="gemini-fast-fake"
    $env:LLM_BASE_URL="http://127.0.0.1:11556/gemini/v1beta/openai"; $env:GEMINI_BASE_URL="http://127.0.0.1:11556/gemini"
    $env:GEMINI_TEXT_MODEL="gemini-text-fake"; $env:GEMINI_IMAGE_MODEL="gemini-image-fake"
    $env:TOOL_AUTO_APPROVE="research_web,create_document,generate_image"
    python -m buddy
    # ブラウザで「AIエージェントの動向を調べて報告書にまとめて」→ 承認 → 本物の Claude Code が MCP 経由で調査・作成
"""
import json, time
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
app = FastAPI()
LOG = __file__ + ".log"
def rec(**kw):
    with open(LOG, "a") as f: f.write(json.dumps(kw, ensure_ascii=False) + "\n")

def sse(chunks):
    def gen():
        for c in chunks:
            time.sleep(0.1)
            yield "data: " + json.dumps({"choices": [{"delta": c}]}, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")

@app.get("/gemini/v1beta/openai/models")
def models(): return {"data": []}

@app.post("/gemini/v1beta/openai/chat/completions")
async def chat(body: dict, request: Request):
    msgs = body["messages"]; last = msgs[-1]
    rec(svc="gemini-chat", auth=request.headers.get("authorization", "")[:12], last_role=last["role"],
        tools=[t["function"]["name"] for t in body.get("tools", [])])
    if last["role"] == "user":
        args = {"goal": "2026年のAIエージェント動向を調べ、要点をまとめた短い報告書(markdown)を作る",
                "context": "読み手は個人開発者。出典を付けること。", "deliverable": "outputs/ に markdown 1本",
                "purpose": "調査と文書作成の複数ステップが必要なため"}
        a = json.dumps(args, ensure_ascii=False)
        return sse([{"content": "複数の作業が必要なので Claude Code に任せます。"},
                    {"tool_calls": [{"index": 0, "id": "call_d", "type": "function", "function": {"name": "delegate_task", "arguments": a[:40]}}]},
                    {"tool_calls": [{"index": 0, "function": {"arguments": a[40:]}}]}])
    return sse([{"content": "Claude Code の作業が完了しました。報告書は outputs に保存されています。"}])

@app.post("/gemini/v1beta/models/{model}:generateContent")
async def gen(model: str, request: Request):
    body = await request.json()
    text = body["contents"][0]["parts"][0]["text"]
    if body.get("tools") == [{"googleSearch": {}}]:
        rec(svc="gemini-research", model=model, q=text[:60])
        return {"candidates": [{"content": {"parts": [{"text": "2026年はエージェントの実務導入が進み、権限管理と人間の承認が設計の要点になった。"}]},
                "finishReason": "STOP", "groundingMetadata": {"groundingChunks": [
                    {"web": {"uri": "https://example.com/agents-2026", "title": "example.com"}}]}}]}
    rec(svc="gemini-writer", model=model, has_system="systemInstruction" in body, prompt_head=text[:80])
    return {"candidates": [{"content": {"parts": [{"text": "# AIエージェント動向レポート\n\n## 要点\n- 実務導入が進んだ\n- 権限管理と承認が鍵\n\n## 出典\n- https://example.com/agents-2026\n"}]},
            "finishReason": "STOP"}]}
