# Meta Ads MedSpa 플레이북

> **원칙:** 예쁜 광고보다 반응하는 광고. 노출보다 예약. 모든 크리에이티브는 "이 광고가 예약으로 이어지는가?"로 평가한다.

---

## 1. 캠페인 구조 (Account Architecture)

```
Ad Account
├── Campaign 1: Lead Generation (Booking Intent)
│   ├── Ad Set 1-1: Lookalike 1% (based on CAPI Bookers)
│   ├── Ad Set 1-2: Interest Targeting (Beauty, Anti-aging, Skincare)
│   └── Ad Set 1-3: Retargeting (Engaged IG/FB - 30days)
│
├── Campaign 2: Brand Awareness (Retargeting)
│   ├── Ad Set 2-1: Website Visitors (30d)
│   └── Ad Set 2-2: Video Views (75% watched)
│
└── Campaign 3: Seasonal/Promo (Event-based)
    └── Ad Set 3-1: Broad Targeting (US, 25-55F, radius 15mi)
```

---

## 2. 오디언스 전략

### 핵심 오디언스 (Cold Traffic)
**인구 통계:**
- 성별: 여성 (85% 이상)
- 연령: 30-55세 (핵심 구매층)
- 지역: 클리닉 반경 15-20 마일
- 언어: English

**관심사 타겟팅 조합:**
```
Primary: Anti-aging, Skin care, Beauty treatments
Secondary: Botox, Fillers, Medical aesthetics
Behavioral: Beauty enthusiasts, Frequent travelers (disposable income 신호)
Exclude: Competitors' page followers (마케팅 업계 종사자 제외)
```

### CAPI 기반 룩어라이크 (최우선 스케일업)
```
Seed Audience: GHL에서 예약 완료한 유저 (Meta CAPI "Schedule" 이벤트)
LAL 1%: 가장 강력한 신규 오디언스
LAL 2-3%: 스케일링 시 확장
```
> **이것이 핵심이다:** 단순 리드 룩어라이크가 아닌 **예약한 사람** 룩어라이크. CPB가 40-60% 낮아진다.

---

## 3. 광고 포맷별 전략

### A. 리드폼 광고 (메인 전환 도구)

**최적 폼 구조:**
```
[Intro Screen]
제목: "Free [Treatment] Consultation — Limited Spots"
이미지: Before/After or Treatment Demo

[Questions — 최대 3개]
1. "Which treatment are you most interested in?" (체크박스)
   ☐ Botox/Fillers  ☐ Laser Treatment  ☐ Skin Rejuvenation  ☐ Other
2. "What is your main concern?" (드롭다운)
3. "When would you like to come in?" (드롭다운)

[Contact Info]
Full Name / Email / Phone (Auto-fill 활성화)

[Thank You Screen]
"We'll call you within 2 hours to schedule your consultation!"
```

**폼 설정 체크리스트:**
- [ ] "Higher Intent" 폼 유형 선택 (검토 화면 포함)
- [ ] Context Card 추가 (클리닉 설명)
- [ ] Organic leads 허용 OFF
- [ ] Custom Disclaimer 추가

---

### B. 비디오 광고 (어웨어니스 + 신뢰 구축)

**검증된 Hook 공식 (첫 3초가 전부):**

| Hook 유형 | 예시 | 사용 치료 |
|---|---|---|
| 숫자 Hook | "5분 만에 10년 젊어 보이는 방법" | HIFU, Botox |
| Before/After | "6주 전 vs 지금 (같은 조명, 같은 각도)" | 스킨케어, 레이저 |
| Pain Point | "눈가 주름 때문에 항상 피곤해 보인다는 말 들으세요?" | Botox, Filler |
| Social Proof | "이번 달 347명이 선택한 [치료명]" | 전체 |
| Behind the Scenes | "MedSpa에서 실제로 무슨 일이 일어나는지 공개합니다" | 신규 고객 교육 |

**비디오 구조 (15-30초):**
```
0-3초:  HOOK (위 공식 중 택1)
3-10초: PROOF (Before/after or 의사 설명)
10-20초: OFFER ("이번 주 무료 상담 예약 시 10% 할인")
20-30초: CTA ("아래 '더 알아보기' 클릭 → 30초 신청")
```

---

### C. 이미지/캐러셀 광고 (오퍼 중심)

**고성과 이미지 광고 구조:**
```
[이미지]: Before/After 또는 Treatment 현장 사진 (진짜 클라이언트)
[헤드라인]: "Houston MedSpa — Botox Starting at $10/unit"
[본문 첫 줄]: "Tired of looking tired? ←(Pain point)"
[본문]: 3-4줄 Benefits + Social proof
[CTA]: "Book Free Consultation"
```

---

## 4. 오퍼 (Offer) 전략

### 검증된 MedSpa 오퍼 유형

| 오퍼 | CPL 효과 | 주의사항 |
|---|---|---|
| Free Consultation | 낮은 CPL, 낮은 Show rate | No-show 많음 |
| $X Off First Treatment | 중간 CPL, 높은 Show rate | 할인 클라이언트 유입 |
| Free Add-on Treatment | 낮은 CPL, 중간 Show rate | 수익성 계산 필요 |
| VIP Member Pricing | 높은 CPL, 높은 LTV | 장기 관계 구축 |
| **"결과 보장" 패키지** | 중간 CPL, 높은 CVR | **추천: 신뢰 극대화** |

**추천 오퍼 공식:**
```
"Free [X-Point] Facial Assessment
+ Personalized Treatment Plan
+ $50 Credit Toward First Treatment
— This Week Only (Limited to 10 spots)"
```

---

## 5. 광고 예산 배분 전략

### $3,000/월 예산 기준

```
40% ($1,200) → Lead Gen (Lookalike / CAPI-based)
30% ($900)   → Lead Gen (Interest Targeting)
20% ($600)   → Retargeting (Website/Engagement)
10% ($300)   → Testing Budget (새 크리에이티브 테스트)
```

### 스케일링 신호
다음 조건이 3일 연속 충족되면 예산 20% 증액:
- CPL < $12
- CPB < $70
- 일일 리드 5건 이상

---

## 6. A/B 테스팅 프레임워크

**항상 하나씩만 테스트:**
```
Week 1-2: Hook 테스트 (동일 오퍼, 다른 훅 4개)
Week 3-4: Offer 테스트 (동일 훅, 다른 오퍼 3개)
Week 5-6: Audience 테스트 (승리 크리에이티브 × 다른 오디언스)
Week 7+: Format 테스트 (이미지 vs 비디오 vs 캐러셀)
```

---

## 7. 크리에이티브 피로도 관리

**신호:** CTR이 첫 주 대비 40% 이상 하락 시 크리에이티브 교체
**사이클:** 
- 상시 4-6개 광고 세트 동시 운영
- 주 2개씩 신규 크리에이티브 추가
- 콘텐츠 하베스팅으로 TikTok/IG 트렌드 주 1회 업데이트

---

## 8. Meta Pixel + CAPI 셋업 체크리스트

- [ ] Pixel 코드 웹사이트에 설치 (GHL 랜딩 페이지)
- [ ] Lead 이벤트 추적 설정 (폼 제출)
- [ ] Conversions API (CAPI) 서버 사이드 연동
- [ ] 이벤트 매칭 품질 확인 (EMQ 7.0+ 목표)
- [ ] CAPI "Schedule" 이벤트 테스트 이벤트 도구로 검증
- [ ] 7-day click + 1-day view 어트리뷰션 윈도우 설정
