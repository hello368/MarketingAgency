# 90일 런치 플레이북 — AI MedSpa 에이전시

> **목표:** 90일 안에 클라이언트 3개, MRR $4,500+, 시스템 완전 자동화
> **원칙:** 완벽한 시스템보다 첫 번째 클라이언트가 먼저다.

---

## PHASE 1 (Day 1-30): 시스템 빌드 + 첫 클라이언트

### Week 1 (Day 1-7): 기술 셋업

**Day 1-2: 환경 설정**
```bash
# 1. .env 파일 생성 (실제 API 키 입력)
cp .env.example .env

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 서버 실행 테스트
uvicorn scripts.ghl_meta_bridge:app --host 0.0.0.0 --port 8000

# 4. 헬스 체크
curl http://localhost:8000/admin/health
```

**필요 API 키 목록:**
- [ ] Meta Long-Lived Access Token (ads_read, ads_management, leads_retrieval)
- [ ] Meta Ad Account ID (act_XXXXXXXXX)
- [ ] Meta Pixel ID
- [ ] Meta Webhook Verify Token (임의 문자열)
- [ ] GoHighLevel Agency API Key
- [ ] GoHighLevel Location ID
- [ ] Telegram Bot Token + Chat ID

**Day 3-4: GHL 설정**
- [ ] GHL 서브어카운트 생성 (테스트용)
- [ ] 커스텀 필드 8개 생성 (meta_lead_id, meta_ad_id 등)
- [ ] fbclid, fbc, fbp 커스텀 필드 추가 (BUG-02 수정)
- [ ] Pipeline 6단계 설정
- [ ] GHL Calendars 설정 (Consultation, Treatment)

**Day 5-7: Webhook 연결**
- [ ] Meta App → Webhooks → leadgen 이벤트 등록
- [ ] Bridge URL 설정 (ngrok 또는 실서버)
- [ ] GHL → Settings → Webhooks → Appointment Created 등록
- [ ] 테스트 리드폼 제출 → GHL 연락처 생성 확인
- [ ] 테스트 예약 → CAPI Schedule 이벤트 확인

### Week 2 (Day 8-14): GHL 자동화 + CAPI 수정

**Day 8-10: 코드 버그 수정**
- [ ] BUG-01: event_id 추가
- [ ] BUG-02: fbclid 캡처 스크립트 + CAPI fbc 포함
- [ ] BUG-03: 시술별 가치 .env 설정
- [ ] SECURITY-01: Bot Token .env 이동

**Day 11-14: GHL 4대 워크플로우 설치**
- [ ] WF-01: Meta Lead 즉시 반응 (T+0, T+1h, T+4h, T+24h)
- [ ] WF-02: 예약 확정 리마인더 (즉시, -24h, -2h)
- [ ] WF-03: 방문 후 리텐션 (+1d, +14d, +30d)
- [ ] WF-04: 노쇼 복구 (+30min, +4h, +3d)
- [ ] 내부 테스트 (팀원 전화로 전체 플로우 검증)

### Week 3 (Day 15-21): GHL Snapshot + 영업 준비

**Day 15-17: Snapshot 패키지**
- [ ] 테스트 서브어카운트에서 전체 설정 완성
- [ ] GHL → Settings → Snapshots → Create Snapshot
- [ ] 스냅샷 이름: "MedSpa Pro Booking System v1.0"
- [ ] 온보딩 체크리스트 문서 작성

**Day 18-21: 영업 자산 준비**
- [ ] Loom 데모 영상 제작 (10분, 시스템 전체 시연)
- [ ] Paid Discovery 판매 페이지 (GHL Funnel 활용)
- [ ] Cold Outreach 메시지 3가지 버전 작성
- [ ] Meta 광고 라이브러리에서 경쟁사 10개사 분석

### Week 4 (Day 22-30): 첫 클라이언트 유치

**아웃리치 목표:** 매일 Instagram DM 30개 + Cold Email 20개

**타겟 선정 기준:**
```
1. Google Maps "medspa [도시명]" 검색
2. Meta 광고 집행 중인 곳 (광고 라이브러리 확인)
3. 리뷰 50개 이하 (아직 시스템 없을 가능성 높음)
4. 팔로워 500-5,000명 (너무 크면 자체 팀 있음)
5. Texas / Georgia 우선 (CPL 낮고 경쟁 적음)
```

**첫 클라이언트 전략:**
- Paid Discovery $997로 시작 (반값 $497로 beta 오퍼 가능)
- 또는 Growth $2,500/월 + $0 셋업 (첫 케이스 스터디용)
- 첫 2주 이내 "First Win" 목표: 리드 → 자동 SMS → 예약 1건

---

## PHASE 2 (Day 31-60): 상품화 + 3개 클라이언트

### 핵심 목표
- [ ] 클라이언트 2개 추가 (총 3개 → MRR $4,500+)
- [ ] Snapshot SaaS Mode 설정
- [ ] 주간 AI 리포트 자동화 테스트
- [ ] 첫 케이스 스터디 완성

### SaaS Mode 설정 (GHL Pro $497/월 필요)
```
GHL → Settings → SaaS Mode 활성화
→ Pricing Tiers 설정:
  - Basic: $297/월 (GHL 소프트웨어만)
  - Pro: $497/월 (GHL + 기본 자동화)
→ Stripe 연동 → 자동 결제
→ 신규 구독 → 서브어카운트 자동 생성 → Snapshot 자동 배포
```

### 케이스 스터디 작성 (첫 클라이언트 기준)
```
제목: "Houston MedSpa, GHL 자동화 도입 후 30일 만에 CPB $43 달성"

구조:
1. 시작 전 상황 (CPB, Show Rate, 리드 후속 처리 방식)
2. 적용한 것 (3-Layer Booking System)
3. 30일 결과 (숫자 중심)
4. 클라이언트 인용
5. 우리가 한 것 (GHL 설정, CAPI 연동, 광고 구조)
```

---

## PHASE 3 (Day 61-90): 3개 → 안정화

### 핵심 목표
- [ ] 3개 클라이언트 MRR $4,500+ 안정화
- [ ] Telegram 일일 리포트 3개 클라이언트 동시 발송
- [ ] CAPI EMQ 7.0+ 전 클라이언트 달성 확인
- [ ] 4번째 클라이언트 파이프라인 확보

### 운영 SOP (3개 클라이언트 동시 관리)

**매일 (10분):**
- Telegram 일일 리포트 확인 (자동 발송됨)
- CPB가 목표 초과 시 → 광고 즉시 조정

**매주 월요일 (1시간/클라이언트):**
- Claude 프롬프트로 주간 리포트 생성 (PROMPT-RPT-01)
- 크리에이티브 성과 확인 → CTR 40% 하락 시 교체
- 새 크리에이티브 2개 준비

**매월 (2시간/클라이언트):**
- Claude 프롬프트로 월간 ROI 리포트 생성 (PROMPT-RPT-02)
- 전략 콜 30분
- 다음 달 캠페인 계획

---

## KPI 체크포인트

| 시점 | CPL | CPB | Show Rate | MRR |
|---|---|---|---|---|
| Day 30 | < $20 | < $120 | > 60% | $1,500 |
| Day 60 | < $15 | < $100 | > 65% | $4,500 |
| Day 90 | < $12 | < $80 | > 70% | $4,500+ |
| Month 6 | < $12 | < $70 | > 75% | $10,000+ |

---

## 예산 계획

| 항목 | Month 1 | Month 2 | Month 3 |
|---|---|---|---|
| MAS 운영비 | $600 | $600 | $600 |
| 아웃리치 도구 | $50 | $50 | $50 |
| 랜딩 페이지 호스팅 | $30 | $30 | $30 |
| 총 비용 | **$680** | **$680** | **$680** |
| 예상 MRR | $1,500 | $3,000 | $4,500 |
| **순익** | **$820** | **$2,320** | **$3,820** |

> Month 3부터 Setup Fee ($2,000~$2,500/클라이언트)가 현금흐름 보완
