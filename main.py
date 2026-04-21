"""
MARTS — Marketing Automation Real-Time System
메인 서버 엔트리포인트

실행: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from fastapi import FastAPI
from contextlib import asynccontextmanager
import logging
import os

from gsheet_handler import update_status
from wiki.store import WikiStore, WikiQueryResult
from wiki.interceptor import WikiModelRouter

# Bridge 모듈 임포트 (GHL-Meta 라우트 + APScheduler 자동 시작)
import scripts.ghl_meta_bridge as _bridge

log = logging.getLogger(__name__)

# ── Wiki store singleton (shared across requests) ─────────────────────────────
_wiki_store = WikiStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 서버 시작 ──────────────────────────────
    print("🚀 MARTS 메인 서버 시작 중...")
    print("📊 구글 시트 연결 테스트 진행 중...")

    update_status("Ivan", "🟢 Online (System Initialized)")

    # ── Wiki store — inject WikiModelRouter for LLM summaries ───────────────
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        try:
            _wiki_store.set_router(WikiModelRouter(api_key=openrouter_key))
            log.info("[Wiki] WikiModelRouter injected — claude-3.5-sonnet summaries enabled")
        except Exception as _wiki_err:
            log.warning("[Wiki] WikiModelRouter init failed — rule-based fallback active: %s", _wiki_err)
    else:
        log.info("[Wiki] OPENROUTER_API_KEY not set — rule-based summaries active")

    yield  # 서버 실행 중

    # ── 서버 종료 ──────────────────────────────
    print("🛑 서버가 종료되었습니다.")
    update_status("Ivan", "🔴 Offline")


# ─────────────────────────────────────────
# MARTS 앱 생성
# ─────────────────────────────────────────
app = FastAPI(
    title="MARTS — Marketing Automation Real-Time System",
    description="MedSpa Lead-Gen AI 자동화 에이전시 통합 서버",
    version="1.0.0",
    lifespan=lifespan,
)

# Bridge 라우트 통합 (GHL Webhook, Meta CAPI, Dashboard)
_BRIDGE_SKIP = {"/", "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}
for _route in _bridge.app.routes:
    path = getattr(_route, "path", "")
    if path not in _BRIDGE_SKIP:
        app.routes.append(_route)

log.info(f"Bridge 라우트 {len([r for r in app.routes if getattr(r,'path','') not in _BRIDGE_SKIP])}개 통합됨")


# ─────────────────────────────────────────
# MARTS 전용 엔드포인트
# ─────────────────────────────────────────
@app.get("/", tags=["MARTS"])
def home():
    return {"status": "MARTS System is active and running."}


@app.get("/status", tags=["MARTS"])
def live_status():
    """Google Sheets Live Status 전체 조회"""
    from gsheet_handler import get_all_statuses
    statuses = get_all_statuses()
    return {"sheet": statuses, "total": len(statuses)}


@app.post("/status/{name}", tags=["MARTS"])
def set_status(name: str, status: str):
    """특정 팀원 상태 업데이트"""
    ok = update_status(name, status)
    return {"name": name, "status": status, "success": ok}


@app.get("/wiki/{client_name}", tags=["Wiki"])
def wiki_query(client_name: str):
    """
    Query Client_Wiki (verified records) AND Chat_Archive (raw conversations)
    for a given client and return a combined executive summary.
      GET /wiki/Luna         → matches "Luna Medspa"
      GET /wiki/Luna%20Medspa → exact match
    """
    result = _wiki_store.query_client(client_name)
    return result.to_dict()


@app.get("/wiki/archive/search", tags=["Wiki"])
def wiki_archive_search(q: str, limit: int = 50):
    """
    Full-text search of the Chat_Archive tab.
    Matches against Message, Space, and User columns.
      GET /wiki/archive/search?q=Luna
      GET /wiki/archive/search?q=budget&limit=20
    """
    hits = _wiki_store.search_archive(q, limit=limit)
    return {"keyword": q, "count": len(hits), "results": hits}
