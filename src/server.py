"""Flask 서버 — 결과 JSON 캐시 + HTML 서빙 + APScheduler.

엔드포인트:
  GET /              → frontend/index.html
  GET /api/results   → 최근 분석 결과 (JSON)
  GET /api/status    → 분석 진행 상태 (running/idle)
  POST /api/refresh  → 수동 재분석 트리거 (백그라운드)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from src.pipeline import run_pipeline, save_results
from src.tracking import build_tracking

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "results.json"
FRONTEND_DIR = ROOT / "frontend"
LOCK_PATH = ROOT / ".analysis.lock"

app = Flask(__name__)


def _pid_alive(pid: int) -> bool:
    """PID이 살아있는지 체크 (signal 0 = 권한·존재 확인)."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def _is_running() -> dict:
    """파일 기반 lock + PID liveness 체크.

    좀비 락 방지: gunicorn worker가 OOM 등으로 죽었을 때 lock 파일이 남는
    경우, PID 체크로 즉시 탐지하고 정리한다.
    """
    if not LOCK_PATH.exists():
        return {"running": False}
    try:
        info = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        # 1) PID 죽음 → stale 락
        pid = info.get("pid")
        if pid and not _pid_alive(pid):
            logger.warning(f"[lock] stale (pid {pid} dead) — 정리")
            LOCK_PATH.unlink(missing_ok=True)
            return {"running": False}
        # 2) 1시간 이상 = 좀비 (PID 살아있어도 hang)
        if time.time() - info.get("started_at", 0) > 3600:
            logger.warning(f"[lock] stale (>1h) — 정리")
            LOCK_PATH.unlink(missing_ok=True)
            return {"running": False}
        return {"running": True, **info}
    except Exception:
        LOCK_PATH.unlink(missing_ok=True)
        return {"running": False}


def _acquire_lock() -> bool:
    """O_EXCL 원자 생성으로 worker 간 안전 lock."""
    try:
        # Python: open with 'x' = exclusive
        with LOCK_PATH.open("x", encoding="utf-8") as f:
            f.write(json.dumps({
                "started_at": time.time(),
                "started_iso": datetime.now().isoformat(),
                "pid": os.getpid(),
            }))
        return True
    except FileExistsError:
        return False


def _release_lock():
    LOCK_PATH.unlink(missing_ok=True)


def _refresh_in_background(limit: int | None = None,
                           archive: bool = False):
    if not _acquire_lock():
        logger.info("이미 분석 실행 중 — skip")
        return
    try:
        logger.info(f"백그라운드 분석 시작 (archive={archive})")
        payload = run_pipeline(limit=limit)
        save_results(payload, str(RESULTS_PATH), archive=archive)
        logger.info(f"백그라운드 분석 완료: {payload['passed_count']}종목")
    except Exception as e:
        logger.exception(f"백그라운드 분석 오류: {e}")
    finally:
        _release_lock()


@app.route("/")
def index():
    return send_from_directory(str(FRONTEND_DIR), "index.html")


@app.route("/api/results")
def api_results():
    if not RESULTS_PATH.exists():
        return jsonify({
            "generated_at": None,
            "passed_count": 0,
            "results": [],
            "message": "결과 없음 — POST /api/refresh 호출",
        })
    data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    return jsonify(data)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    # 이미 분석 중이면 새 분석 시작하지 않음 (worker 간 file lock)
    state = _is_running()
    if state.get("running"):
        return jsonify({
            "status": "already_running",
            "started_iso": state.get("started_iso"),
            "at": datetime.now().isoformat(),
        }), 409
    limit_env = os.environ.get("ANALYZE_LIMIT")
    limit = int(limit_env) if limit_env else None
    threading.Thread(
        target=_refresh_in_background, args=(limit,), daemon=True
    ).start()
    return jsonify({"status": "started", "at": datetime.now().isoformat()})


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "at": datetime.now().isoformat()})


@app.route("/api/status")
def api_status():
    """현재 분석 진행 상태 (running/idle)."""
    return jsonify(_is_running())


# 추적 데이터 캐시 (1시간)
_tracking_cache = {"at": 0, "data": None}
_TRACKING_TTL = 3600


@app.route("/api/tracking")
def api_tracking():
    import time
    now = time.time()
    if _tracking_cache["data"] and now - _tracking_cache["at"] < _TRACKING_TTL:
        return jsonify(_tracking_cache["data"])
    try:
        data = build_tracking()
        _tracking_cache["data"] = data
        _tracking_cache["at"] = now
        return jsonify(data)
    except Exception as e:
        logger.exception(f"[tracking] err: {e}")
        return jsonify({"error": str(e)}), 500


def init_scheduler():
    """장 마감 후 자동 분석 — 16:00 (장 직후), 21:00 (KRX 정정 반영 후)."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from pytz import timezone
        sched = BackgroundScheduler(timezone=timezone("Asia/Seoul"))
        # 16:00: 장 마감(15:30) + 30분 — 가장 빠른 1차 결과 (archive X)
        sched.add_job(_refresh_in_background, "cron",
                      day_of_week="mon-fri",
                      hour=16, minute=0, id="afternoon_refresh",
                      kwargs={"archive": False})
        # 21:00: KRX 외국인/기관 정정 반영 + 추적용 archive 저장
        sched.add_job(_refresh_in_background, "cron",
                      day_of_week="mon-fri",
                      hour=21, minute=0, id="evening_refresh",
                      kwargs={"archive": True})
        sched.start()
        logger.info("스케줄러 시작 (KST 평일 16:00 + 21:00)")
    except Exception as e:
        logger.warning(f"스케줄러 비활성: {e}")


init_scheduler()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=False)
