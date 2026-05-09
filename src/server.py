"""Flask 서버 — 결과 JSON 캐시 + HTML 서빙 + APScheduler.

엔드포인트:
  GET /         → frontend/index.html
  GET /api/results → 최근 분석 결과 (JSON)
  POST /api/refresh → 수동 재분석 트리거 (백그라운드)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from src.pipeline import run_pipeline, save_results

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = ROOT / "results.json"
FRONTEND_DIR = ROOT / "frontend"

app = Flask(__name__)
_run_lock = threading.Lock()


def _refresh_in_background(limit: int | None = None):
    if not _run_lock.acquire(blocking=False):
        logger.info("이미 분석 실행 중 — skip")
        return
    try:
        logger.info("백그라운드 분석 시작")
        payload = run_pipeline(limit=limit)
        save_results(payload, str(RESULTS_PATH))
        logger.info(f"백그라운드 분석 완료: {payload['passed_count']}종목")
    except Exception as e:
        logger.exception(f"백그라운드 분석 오류: {e}")
    finally:
        _run_lock.release()


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
    limit_env = os.environ.get("ANALYZE_LIMIT")
    limit = int(limit_env) if limit_env else None
    threading.Thread(
        target=_refresh_in_background, args=(limit,), daemon=True
    ).start()
    return jsonify({"status": "started", "at": datetime.now().isoformat()})


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "at": datetime.now().isoformat()})


def init_scheduler():
    """장 마감 후 자동 분석 — 16:00 (장 직후), 21:00 (KRX 정정 반영 후)."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from pytz import timezone
        sched = BackgroundScheduler(timezone=timezone("Asia/Seoul"))
        # 16:00: 장 마감(15:30) + 30분 — 가장 빠른 1차 결과
        sched.add_job(_refresh_in_background, "cron",
                      day_of_week="mon-fri",
                      hour=16, minute=0, id="afternoon_refresh")
        # 21:00: KRX 외국인/기관 매매 정정 데이터까지 반영된 2차 결과
        sched.add_job(_refresh_in_background, "cron",
                      day_of_week="mon-fri",
                      hour=21, minute=0, id="evening_refresh")
        sched.start()
        logger.info("스케줄러 시작 (KST 평일 16:00 + 21:00)")
    except Exception as e:
        logger.warning(f"스케줄러 비활성: {e}")


init_scheduler()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=False)
