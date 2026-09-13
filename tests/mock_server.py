"""本地模拟 API 服务器，用于无 Key 验证工具：同时模拟 OpenAI / Anthropic / Gemini 三种流式与非流式接口。

用法: python tests/mock_server.py [port] [--ttft 0.15] [--chunk-delay 0.02] [--chunks 40] [--fail-rate 0]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEXT = (
    "人工智能的发展可以追溯到二十世纪五十年代。1956 年的达特茅斯会议首次提出“人工智能”这一概念，图灵、麦卡锡、明斯基等人奠定了符号推理与机器学习的早期基础。"
    "此后几十年里，专家系统、神经网络与统计学习方法交替兴起，也经历过两次“AI 寒冬”。\n\n"
    "进入二十一世纪，算力、数据与深度学习算法的共同进步让语音识别、图像识别取得突破，2012 年 AlexNet 在 ImageNet 上的成绩被视为深度学习时代的开端。"
    "2017 年 Transformer 架构提出后，大规模预训练语言模型迅速发展，GPT、BERT 等模型让自然语言处理能力大幅提升。\n\n"
    "近年来，大语言模型开始具备代码生成、多轮对话与工具调用能力，被广泛应用于编程、写作、搜索与客服等场景。"
    "与此同时，模型的推理延迟、吞吐与稳定性成为工程落地时必须量化的指标，这也是各类测速与评测工具出现的原因。"
)
OPTS = {"ttft": 0.15, "chunk_delay": 0.02, "chunks": 40, "fail_rate": 0.0}


def _chunks(n):
    step = max(1, len(TEXT) // n)
    return [TEXT[i:i + step] for i in range(0, len(TEXT), step)][:n]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 安静
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def _sse(self, data, event=None):
        if event:
            self.wfile.write(f"event: {event}\n".encode())
        self.wfile.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
        self.wfile.flush()

    def do_GET(self):
        if self.path.endswith("/models"):
            if "v1beta" in self.path:
                return self._json(200, {"models": [{"name": "models/gemini-mock", "displayName": "Gemini Mock"}]})
            return self._json(200, {"object": "list", "data": [{"id": "mock-fast", "object": "model", "created": 1700000000, "owned_by": "mock"}, {"id": "mock-slow", "owned_by": "mock"}]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if random.random() < OPTS["fail_rate"]:
            return self._json(502, {"error": {"message": "upstream request timeout", "type": "server_error"}})
        model = body.get("model") or self.path.split("/models/")[-1].split(":")[0]
        delay_mult = 3 if "slow" in model else 1
        chunks = _chunks(OPTS["chunks"])
        if "gemini" in model or "v1beta" in self.path:
            return self._gemini(body, chunks, delay_mult)
        if self.path.endswith("/messages"):
            return self._anthropic(body, chunks, delay_mult)
        return self._openai(body, chunks, delay_mult)

    # ---- openai
    def _openai(self, body, chunks, mult):
        if body.get("model") == "mock-401":
            return self._json(401, {"error": {"message": "Invalid API key", "type": "auth_error"}})
        time.sleep(OPTS["ttft"] * mult)
        if not body.get("stream"):
            time.sleep(OPTS["chunk_delay"] * mult * len(chunks))
            return self._json(200, {"choices": [{"message": {"role": "assistant", "content": "".join(chunks)}}],
                                    "usage": {"prompt_tokens": 30, "completion_tokens": len(chunks) * 4}})
        self._sse_start()
        for i, c in enumerate(chunks):
            self._sse({"id": "x", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}]})
            time.sleep(OPTS["chunk_delay"] * mult)
        self._sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        self._sse({"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": len(chunks) * 4}})
        self.wfile.write(b"data: [DONE]\n\n")

    # ---- anthropic
    def _anthropic(self, body, chunks, mult):
        time.sleep(OPTS["ttft"] * mult)
        if not body.get("stream"):
            time.sleep(OPTS["chunk_delay"] * mult * len(chunks))
            return self._json(200, {"type": "message", "content": [{"type": "text", "text": "".join(chunks)}],
                                    "usage": {"input_tokens": 30, "output_tokens": len(chunks) * 4}})
        self._sse_start()
        self._sse({"type": "message_start", "message": {"usage": {"input_tokens": 30, "output_tokens": 1}}}, "message_start")
        self._sse({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}, "content_block_start")
        for c in chunks:
            self._sse({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": c}}, "content_block_delta")
            time.sleep(OPTS["chunk_delay"] * mult)
        self._sse({"type": "content_block_stop", "index": 0}, "content_block_stop")
        self._sse({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": len(chunks) * 4}}, "message_delta")
        self._sse({"type": "message_stop"}, "message_stop")

    # ---- gemini
    def _gemini(self, body, chunks, mult):
        time.sleep(OPTS["ttft"] * mult)
        if "streamGenerateContent" not in self.path:
            time.sleep(OPTS["chunk_delay"] * mult * len(chunks))
            return self._json(200, {"candidates": [{"content": {"parts": [{"text": "".join(chunks)}], "role": "model"}}],
                                    "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": len(chunks) * 4}})
        self._sse_start()
        for i, c in enumerate(chunks):
            obj = {"candidates": [{"content": {"parts": [{"text": c}], "role": "model"}}]}
            if i == len(chunks) - 1:
                obj["usageMetadata"] = {"promptTokenCount": 30, "candidatesTokenCount": len(chunks) * 4}
            self._sse(obj)
            time.sleep(OPTS["chunk_delay"] * mult)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", type=int, default=8765)
    ap.add_argument("--ttft", type=float, default=0.15)
    ap.add_argument("--chunk-delay", type=float, default=0.02)
    ap.add_argument("--chunks", type=int, default=40)
    ap.add_argument("--fail-rate", type=float, default=0.0)
    a = ap.parse_args()
    OPTS.update(ttft=a.ttft, chunk_delay=a.chunk_delay, chunks=a.chunks, fail_rate=a.fail_rate)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"mock server on http://127.0.0.1:{a.port}  (openai: /v1/chat/completions, anthropic: /v1/messages, gemini: /v1beta/...)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
