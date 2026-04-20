# AI 자동화 에이전시 기술 스택 아키텍처

> 리서치 검증 완료 버전 (2026-04-16)
> 원칙: 복잡성 최소화, ROI 최대화. GHL이 할 수 있는 건 GHL에서 처리.

---

## 1. 레이어별 스택 선택 기준

```
Layer 1: Traffic       → Meta Ads Manager
Layer 2: CRM/OS        → GoHighLevel (GHL)
Layer 3: AI Bridge     → GHL Native CAPI + Make.com (CAPI 전용 모듈)
Layer 4: AI Logic      → n8n (AI 헤비 로직, 20+ 클라이언트 이후)
Layer 5: Copywriting   → Claude API + GPT-4o
Layer 6: Creative      → Midjourney + HeyGen + Runway ML
Layer 7: Reporting     → Looker Studio (무료) + Telegram Bot
```

---

## 2. 자동화 도구 비교 (검증된 실데이터)

### Zapier vs Make.com vs n8n

| 기준 | Zapier | Make.com | n8n |
|---|---|---|---|
| 사용 난이도 | 가장 쉬움 | 중간 | 가파른 학습곡선 |
| 월 10만 작업 비용 | $300+ | $100 이하 | ~$50 (호스팅만) |
| AI 에이전트 기능 | 기초 | 중간 | **고급** (LangChain, 70+ AI 노드, 영구 메모리) |
| GHL 연동 | 가능 | 가능 (전용 모듈) | 가능 (REST API) |
| Meta CAPI 연동 | 가능 | **가능 (전용 FB CAPI 모듈)** | 가능 (HTTP) |
| 자체 호스팅 | 불가 | 불가 | **가능 (무제한 실행)** |
| **추천 단계** | 사용 X (비효율) | **1~20 클라이언트** | **20+ 클라이언트 또는 AI 차별화** |

**결론:**
- 초기~20 클라이언트: **GHL 네이티브 + Make.com**으로 충분
- 20+ 클라이언트 또는 AI 기능 차별화 필요 시: **n8n 추가**
- Zapier: 고유한 통합이 없으면 사용하지 않음

---

## 3. Meta CAPI 구현 옵션 비교

> **중요:** Meta는 2025년 5월 Offline Conversions API를 **완전 종료** → 모든 오프라인 전환 추적은 반드시 CAPI를 통해야 함

| 방법 | 셋업 시간 | 월 비용 | 권장 대상 |
|---|---|---|---|
| **GHL 네이티브 CAPI** | 30분 | GHL 포함 | **신규 설정의 1순위** |
| Make.com + FB CAPI 모듈 | 1~2시간 | Make 플랜 포함 | 크로스플랫폼 자동화 |
| Server-side GTM | 4~8시간 | $10~$50/월 | Meta + Google 동시 |
| Custom API (n8n/FastAPI) | 8~16시간 | 호스팅만 | 복잡한 오프라인 추적 |

---

## 4. CAPI 이벤트 매칭 품질 (EMQ) 목표

| 이벤트 | 목표 EMQ | 최우선 액션 |
|---|---|---|
| Lead (폼 제출) | **8.0+** | 해시된 이메일 추가 (단독으로 +4점) |
| Schedule (예약) | **8.5+** | 저장된 fbclid 포함 |
| Purchase (서비스 완료) | **8.8~9.3** | 이메일 + 전화 + 주소 + fbclid 풀 데이터 |

**절대 규칙:**
- SHA-256 해싱 필수: email, phone (E.164), first/last name, city, state, ZIP
- **해시하면 안 되는 것:** `fbp` (브라우저 ID), `fbc` (클릭 ID) — 해싱하면 매칭 파괴
- `fbclid`는 랜딩 페이지 첫 방문 시 반드시 캡처 → GHL 커스텀 필드에 저장

---

## 5. 풀펀널 CAPI 이벤트 체인

```
광고 클릭 (fbclid 캡처, fbp 쿠키 설정)
  │
  ▼
랜딩 페이지
  ├─ 브라우저 Pixel fires: Lead (eventID: A)
  └─ CAPI fires: Lead (eventID: A) → 중복 제거
  │
  ▼
GHL Contact 생성 → 자동 SMS/이메일 시퀀스 시작
  │
  ▼
예약 완료: CAPI fires Schedule (저장된 fbclid 포함)
  │
  ▼
예약 확인/방문: CAPI fires AppointmentShowed (custom)
  │
  ▼
서비스 완료/결제: CAPI fires Purchase (action_source: "physical_store")
```

> **이것이 단순 CPL 절감이 아닌 CPB 절감의 핵심이다.**
> 폼 제출이 아닌 실제 방문·결제 이벤트로 Meta 알고리즘을 훈련시키면
> "예약하는 사람"을 찾아주는 캠페인으로 진화한다.

---

## 6. AI 크리에이티브 스택

| 목적 | 도구 | 용도 |
|---|---|---|
| **광고 카피** | Claude API | 규정 준수 의료 카피, SOP, 리포트 |
| **광고 카피 (훅/브레인스토밍)** | GPT-4o | 고볼륨 훅 테스트, SNS 카피 |
| **이미지 (캠페인 비주얼)** | Midjourney | 라이프스타일 이미지, 분위기 |
| **이미지 (상업용 라이선스)** | Adobe Firefly | 규제 클라이언트 대상 |
| **로컬 SNS 콘텐츠 대량** | Canva Magic Studio | 빠른 소셜 포스트 |
| **비디오 (UGC 스타일)** | Runway ML Gen-3 | 시술 데모, 치료 클립 |
| **비디오 (AI 스포크스퍼슨)** | HeyGen | 카메라 앞에 설 사람 없는 클리닉용 |
| **비디오 (긴 클립)** | Kling AI | 2분 이내 서비스 쇼케이스 |

---

## 7. 최소 실행 가능 스택 (MAS) — 신규 에이전시

| 레이어 | 도구 | 월 비용 |
|---|---|---|
| CRM + 마케팅 OS | GoHighLevel Agency Pro | $497 |
| 자동화 브릿지 | Make.com Core | $11~$29 |
| AI 카피라이팅 | Claude Pro + ChatGPT Plus | $40 |
| 이미지 생성 | Midjourney Basic | $10 |
| 비디오 (선택) | Runway Standard | $15 |
| 리포팅 | Looker Studio | 무료 |
| **총합** | | **~$600/월** |

> **수익성 검증:** 클라이언트 3명 × $1,500/월 = $4,500 MRR
> 운영 비용 $600 → **마진율 87%** (광고비 제외)

---

## 8. GHL Snapshot 상품화 전략

**Snapshot이란:** GHL 설정 전체(워크플로우, 파이프라인, 캘린더, 폼, 템플릿)를 
패키지로 묶어 새 서브계정에 즉시 배포하는 기능

**MedSpa Snapshot 판매 모델:**

| 상품 | 가격 | 대상 |
|---|---|---|
| Snapshot 라이선스 (일회성) | $497~$997 | DIY 에이전시/클리닉 |
| GHL SaaS 구독 (월) | $297~$497/월 | MedSpa 직접 운영 |
| Done-for-You (Snapshot 포함) | $1,500~$3,000/월 | 풀서비스 클라이언트 |

**실제 사례:** 부동산 CRM Snapshot $497/월로 2개월 만에 17명 확보 → MRR $8,500

**구현 자동화:** 결제 → 서브계정 자동 생성 → Snapshot 배포 → 자격증명 이메일 발송
= 인력 없이 온보딩 완전 자동화
