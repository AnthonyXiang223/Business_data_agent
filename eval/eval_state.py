"""断点续跑状态存储（从 eval_retrieval.py 拆出）。

状态文件命名：hash = 题集内容 + agent 模式 + judge 模型配置（改了题或
配置旧状态自动失效）。写入用原子替换（uuid 临时文件 + fsync + os.replace，
借鉴 QueryMind resume_store），读入对损坏 JSON 降级为空状态。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv

# hash 输入含 judge 模型名（env 读取），模块需自足：独立 import 也要有 .env
load_dotenv()

STATE_DIR = Path(__file__).resolve().parent.parent / ".eval_state"

_save_lock = threading.Lock()


def state_path(cases: list[dict], mode: str) -> Path:
    judge_model = os.environ.get("EVAL_JUDGE_MODEL", os.environ.get("OPENAI_MODEL_NAME", ""))
    key = json.dumps(cases, ensure_ascii=False, sort_keys=True) + "|" + mode + "|" + judge_model
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return STATE_DIR / f"resume_{digest}.json"


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # 损坏文件降级为"无断点"：自动全量重跑，而不是长任务跑一半崩溃
            print(f"[警告] 状态文件损坏，忽略并重新执行: {path}", file=sys.stderr)
    return {"results": {}}


def save_state(path: Path, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with _save_lock:
        # uuid 临时名：多进程并发写同一文件时互不交错；replace 保证落盘是完整 JSON
        tmp_path = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
