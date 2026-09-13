"""历史记录：~/.apitest/history.jsonl"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

HISTORY_PATH = Path(os.environ.get("APITEST_HISTORY", str(Path.home() / ".apitest" / "history.jsonl")))


def _slim(r: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(r)
    d.pop("chunk_times", None)
    d["text"] = (d.get("text") or "")[:400]
    return d


def save_run(meta: Dict[str, Any], summaries: List[Dict[str, Any]], results: Optional[List[Dict[str, Any]]] = None) -> str:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    rec = {"id": run_id, "meta": meta, "summaries": summaries}
    if results:
        rec["results"] = [_slim(r) for r in results if not r.get("warmup")]
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return run_id


def load_runs(last: Optional[int] = None) -> List[Dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    runs = []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return runs[-last:] if last else runs


def find_run(run_id: str) -> Optional[Dict[str, Any]]:
    for r in load_runs():
        if r.get("id") == run_id or r.get("id", "").startswith(run_id):
            return r
    return None


def clear() -> None:
    if HISTORY_PATH.exists():
        HISTORY_PATH.unlink()


# ---------------------------------------------------------------------------
# 连通诊断历史：~/.apitest/ping_history.jsonl
# ---------------------------------------------------------------------------

PING_HISTORY_PATH = Path(os.environ.get("APITEST_PING_HISTORY", str(HISTORY_PATH.parent / "ping_history.jsonl")))


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _write_jsonl(path: Path, recs: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def make_ping_record(endpoint: str, url: str, rows: List[Dict[str, Any]], source: str = "cli", proxy: Optional[str] = None) -> Dict[str, Any]:
    oks = [r for r in rows if not r.get("error")]

    def avg(k: str) -> float:
        vals = [r[k] for r in oks if r.get(k, 0) > 0]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    return {
        "id": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "endpoint": endpoint,
        "url": url,
        "host": rows[0].get("host", "") if rows else "",
        "ip": next((r.get("ip") for r in rows if r.get("ip")), ""),
        "source": source,
        "proxy": proxy or None,
        "n": len(rows),
        "ok": len(oks),
        "avg": {k: avg(k) for k in ("dns", "tcp", "tls", "http")},
        "http_max": round(max([r.get("http", 0) for r in oks] or [0]), 4),
        "status": next((r.get("status") for r in oks if r.get("status")), 0),
        "server": next((r.get("server") for r in rows if r.get("server")), ""),
        "error": next((r.get("error") for r in rows if r.get("error")), ""),
        "rows": [{k: r.get(k) for k in ("ip", "dns", "tcp", "tls", "http", "status", "error")} for r in rows],
    }


def save_ping(rec: Dict[str, Any]) -> str:
    PING_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PING_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec["id"]


def load_pings(endpoint: Optional[str] = None, last: Optional[int] = None) -> List[Dict[str, Any]]:
    recs = _load_jsonl(PING_HISTORY_PATH)
    if endpoint:
        recs = [r for r in recs if r.get("endpoint") == endpoint]
    return recs[-last:] if last else recs


def delete_ping(ping_id: str) -> bool:
    recs = _load_jsonl(PING_HISTORY_PATH)
    keep = [r for r in recs if r.get("id") != ping_id]
    if len(keep) == len(recs):
        return False
    _write_jsonl(PING_HISTORY_PATH, keep)
    return True


def clear_pings(endpoint: Optional[str] = None) -> int:
    recs = _load_jsonl(PING_HISTORY_PATH)
    keep = [r for r in recs if endpoint and r.get("endpoint") != endpoint]
    _write_jsonl(PING_HISTORY_PATH, keep)
    return len(recs) - len(keep)


def delete_run(run_id: str) -> bool:
    runs = load_runs()
    keep = [r for r in runs if r.get("id") != run_id]
    if len(keep) == len(runs):
        return False
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return True
