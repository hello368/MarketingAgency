# AI 자동화 에이전시 — 마스터 지식 인덱스

> **미션:** 단순 광고 대행이 아닌 "예약 자동화 시스템"을 파는 AI 에이전시
> **북극성 지표:** Cost per Booking (CPB) & Show Rate
> **마지막 업데이트:** 2026-04-16

---

## 핵심 수치 (리서치 검증)

| 지표 | 수치 | 출처 |
|---|---|---|
| US MedSpa 시장 규모 | $7~9B (2025), CAGR 14% | Precedence Research |
| 미국 MedSpa 업체 수 | 9,000개+ | AmSpa 2024 |
| 예약을 가장 먼저 연락한 곳에서 하는 비율 | **70%** | Harvard Business Review 인용 |
| Meta 리드 → 구조화 후속 처리 없이 전환 | **85%+ 전환 안 됨** | 업계 통계 |
| 부재중 하루 3통 → 연간 손실 | **$130,000+** | 업계 데이터 |
| 뷰티/미용 Meta 광고 평균 CPL | **~$43** | WordStream 2025 |
| Botox Meta 광고 CPL | **$20~$40** | Pennock MedSpa 벤치마크 |
| MVC 운영 비용 (에이전시) | **~$600/월** | 리서치 검증 |
| 3 클라이언트 MRR 마진율 | **~87%** | 계산 검증 |

---

## 📁 지식 베이스 구조

### 🗺️ 그래프 (graphs/)
| 파일 | 내용 |
|---|---|
| [00_master_knowledge_graph.mmd](graphs/00_master_knowledge_graph.mmd) | 에이전시 전체 마인드맵 |
| [01_tech_stack.mmd](graphs/01_tech_stack.mmd) | 도구 관계도 + 데이터 플로우 |
| [02_automation_flows.mmd](graphs/02_automation_flows.mmd) | 3개 자동화 플로우 시퀀스 다이어그램 |
| [03_medspa_client_funnel.mmd](graphs/03_medspa_client_funnel.mmd) | 고객 여정 + KPI 추적 |
| [04_agency_revenue_model.mmd](graphs/04_agency_revenue_model.mmd) | 수익 모델 + 스케일 수학 |
| [05_competitive_positioning.mmd](graphs/05_competitive_positioning.mmd) | 경쟁 포지셔닝 쿼드런트 |
| [06_90day_launch_timeline.mmd](graphs/06_90day_launch_timeline.mmd) | **NEW** 90일 런치 간트 차트 |
| [07_sales_process.mmd](graphs/07_sales_process.mmd) | **NEW** 영업 프로세스 플로우차트 |
| [08_capi_full_chain.mmd](graphs/08_capi_full_chain.mmd) | **NEW** CAPI 풀펀넬 이벤트 체인 + 중복제거 |
| [09_mrr_growth_model.mmd](graphs/09_mrr_growth_model.mmd) | **NEW** 12개월 MRR 성장 모델 |

### 📊 시장 인텔리전스 (intelligence/)
| 파일 | 내용 |
|---|---|
| [medspa_market_intelligence.md](intelligence/medspa_market_intelligence.md) | 시장 규모, CPL 벤치마크, Pain Points, 타겟 지역 |
| [treatment_trends_2025.md](intelligence/treatment_trends_2025.md) | 2025 시술 트렌드 + 계절 캘린더 + Meta 광고 금지 시술 |
| [competitive_intelligence.md](intelligence/competitive_intelligence.md) | **NEW** 경쟁사 유형 분석 + 반박 논리 + 조사 SOP |

### 📖 플레이북 (playbooks/)
| 파일 | 내용 |
|---|---|
| [meta_ads_medspa_playbook.md](playbooks/meta_ads_medspa_playbook.md) | 캠페인 구조, 오디언스, 오퍼 전략, A/B 테스트 |
| [ghl_automation_playbook.md](playbooks/ghl_automation_playbook.md) | 4개 워크플로우 레시피, SMS 템플릿, GHL Snapshot |
| [wf05_referral_playbook.md](playbooks/wf05_referral_playbook.md) | **NEW** WF-05 레퍼럴 자동화 + Ambassador 프로그램 |
| [paid_discovery_playbook.md](playbooks/paid_discovery_playbook.md) | **NEW** $997 유료 진단 오퍼 + 세일즈 스크립트 |
| [90day_launch_playbook.md](playbooks/90day_launch_playbook.md) | **NEW** Day 1~90 구체적 실행 플랜 + KPI 체크포인트 |

### ⚙️ 시스템 (systems/)
| 파일 | 내용 |
|---|---|
| [ai_automation_agency_model.md](systems/ai_automation_agency_model.md) | 비즈니스 모델, 패키지 설계, 세일즈 프로세스 |
| [tech_stack_architecture.md](systems/tech_stack_architecture.md) | 도구 비교, CAPI 구현, EMQ 목표, MVC 스택 |
| [prompt_library.md](systems/prompt_library.md) | 광고 카피, 리포트, 트렌드 분석, 커뮤니케이션 프롬프트 |
| [code_issues_and_fixes.md](systems/code_issues_and_fixes.md) | **NEW** ghl_meta_bridge.py 버그 3개 + 수정 코드 |

### 🔧 구현 코드 (scripts/)
| 파일 | 내용 |
|---|---|
| [ghl_meta_bridge.py](../scripts/ghl_meta_bridge.py) | FastAPI 서버 — 3개 플로우 실제 구현 |
| [marketing_bridge.py](../scripts/marketing_bridge.py) | Telegram Bot — Claude Code 브릿지 |

### 📋 전략 문서 (docs/)
| 파일 | 내용 |
|---|---|
| [ghl_meta_automation_strategy.md](../docs/ghl_meta_automation_strategy.md) | API 연동 전략, 필드 매핑, 리스크 관리 |

---

## 🚀 Phase별 실행 로드맵

### Phase 1 (즉시 실행)
- [ ] `.env` 파일 설정 (Meta + GHL + Telegram 키)
- [ ] `pip install -r requirements.txt`
- [ ] GHL에 커스텀 필드 생성 (meta_lead_id, meta_ad_id 등)
- [ ] Meta App에서 Webhooks → leadgen 이벤트 등록
- [ ] GHL Webhooks → Appointment Created 등록
- [ ] `uvicorn scripts.ghl_meta_bridge:app --port 8000` 실행
- [ ] `/admin/health` 엔드포인트로 상태 확인

### Phase 2 (첫 클라이언트 이후)
- [ ] GHL Snapshot 패키지화 (4개 워크플로우 완성 후)
- [ ] Meta CAPI EMQ 7.0+ 달성 확인
- [ ] 일일 Telegram 리포트 검증
- [ ] 첫 케이스 스터디 작성

### Phase 3 (클라이언트 5+ 이후)
- [ ] Make.com으로 CAPI 이벤트 체인 고도화 (fbclid 캡처 포함)
- [ ] Claude API로 주간 리포트 자동 생성 연동
- [ ] GHL SaaS Mode 업그레이드 → Snapshot 외부 판매

### Phase 4 (클라이언트 10+ / 20+ 이후)
- [ ] n8n 도입 — AI 리드 스코어링, 맞춤형 시퀀스
- [ ] Looker Studio 클라이언트 대시보드
- [ ] 교육 프로그램 / 화이트레이블 에이전시 파트너십

---

## 💡 즉시 활용 가능한 영업 멘트

> "다른 에이전시는 리드를 넘겨드립니다.
> 저희는 리드가 들어오면 **2분 안에 자동으로 문자가 가고**,
> 예약이 잡히면 **Meta가 그 데이터를 학습해서** 다음 달 광고가 더 좋아지는
> **예약 자동화 시스템**을 만들어드립니다.
> 저희 클라이언트는 CPL이 아닌 **Cost per Booking**을 봅니다."

---

## 🔄 지식 베이스 업데이트 규칙

1. 새로운 인사이트/리서치 → `intelligence/` 폴더에 추가 또는 업데이트
2. 새로운 워크플로우 발견 → `playbooks/ghl_automation_playbook.md` 레시피 추가
3. 새로운 AI 도구 검증 → `systems/tech_stack_architecture.md` 업데이트
4. 새로운 프롬프트 → `systems/prompt_library.md` 섹션 추가
5. 그래프 변경 → 해당 `.mmd` 파일 수정
6. 클라이언트 케이스 스터디 → `intelligence/` 폴더에 `case_study_[name].md`로 저장
