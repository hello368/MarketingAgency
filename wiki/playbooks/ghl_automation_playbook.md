# GoHighLevel 자동화 플레이북 (MedSpa)

> **철학:** GHL은 CRM이 아니다. **리드를 예약으로 바꾸는 자동 영업사원**이다.

---

## 1. GHL 계정 구조

```
Agency Account (우리)
└── Sub-Account (클라이언트 MedSpa)
    ├── Pipeline: MedSpa Booking Pipeline
    ├── Calendar: Consultation + Treatment
    ├── Workflows: 자동화 시퀀스
    ├── Conversations: SMS/Email/FB/IG 통합
    └── Reporting: 예약률, 응답률, 매출
```

---

## 2. Pipeline 설계 (6단계)

```
[신규 리드] → [연락 시도] → [상담 예약] → [예약 확정] → [방문 완료] → [재예약/리뷰]
   Stage 1       Stage 2      Stage 3      Stage 4       Stage 5      Stage 6
```

| Stage | 이름 | 자동화 트리거 | 목표 기간 |
|---|---|---|---|
| 1 | 신규 리드 | Meta Webhook 유입 즉시 | 0분 |
| 2 | 연락 시도 | SMS 발송 후 | 0-60분 |
| 3 | 상담 예약 | 캘린더 링크 클릭 | 1-24시간 |
| 4 | 예약 확정 | 예약 생성 이벤트 | 즉시 |
| 5 | 방문 완료 | 예약 시간 경과 | 예약 당일 |
| 6 | 재예약/리뷰 | 방문 완료 후 | +1일 |

---

## 3. 핵심 워크플로우 (Workflow Recipes)

### WF-01: Meta Lead 즉시 반응 시퀀스

```
트리거: Contact Created (Tag: "meta-lead")

── T+0분: SMS 발송
   "안녕하세요 {first_name}님! 저희 [클리닉명]에 관심 가져주셔서 감사해요 😊
   {treatment} 에 대해 궁금하신 점 있으시면 바로 답장 주세요!
   이번 주 상담 가능 시간 확인해드릴게요 → [Calendar Link]"

── T+1시간: 미반응 → SMS 2차
   "{first_name}님, 혹시 못 보셨을까봐 다시 연락드려요!
   이번 주 {treatment} 체험 자리가 3자리 남았어요.
   [Book Now] → {booking_link}"

── T+4시간: 미반응 → Email 발송
   Subject: "Your {treatment} Consultation — We saved you a spot"
   [Before/After 갤러리 + 예약 링크]

── T+24시간: 미반응 → SMS 3차
   "마지막으로 연락드려요, {first_name}님.
   상담 신청이 오늘까지예요. 관심 없으시면 괜찮아요, 알려주세요! 😊"

── T+3일: 미반응 → Stage "콜드 리드" 이동 + 태그 추가
```

---

### WF-02: 예약 확정 후 리마인더 시퀀스

```
트리거: Appointment Created

── 즉시: 예약 확인 SMS
   "{first_name}님, 예약이 확정되었습니다! ✅
   📅 {appointment_date} {appointment_time}
   📍 {clinic_address}
   변경/취소: {reschedule_link}"

── 예약 -24시간: 리마인더 SMS
   "내일 {appointment_time} 예약 잊지 마세요, {first_name}님! 😊
   오시기 전 주의사항: [링크]
   취소가 필요하시면: {reschedule_link}"

── 예약 -2시간: 최종 리마인더 SMS
   "{first_name}님, 오늘 {appointment_time} 뵙겠습니다!
   주차 정보: {parking_info}
   늦으실 경우: {phone_number}"

── 예약 시간 경과 +2시간: 방문 여부 확인
   (내부 알림: 직원이 Pipeline Stage 5로 수동 이동)
```

---

### WF-03: 방문 완료 후 리텐션 시퀀스

```
트리거: Stage 변경 → "방문 완료"

── T+1일: 리뷰 요청 SMS
   "{first_name}님, 어제 방문해 주셔서 감사해요! 💕
   치료는 어떠셨나요? 솔직한 후기 남겨주시면 정말 큰 도움이 돼요 🙏
   → [Google Review Link] (30초면 돼요!)"

── T+14일: 결과 체크인 SMS
   "{first_name}님! {treatment} 결과 잘 나오고 있으신가요? 😊
   궁금하신 점 있으시면 편하게 연락 주세요!
   다음 세션 예약하시려면: {booking_link}"

── T+30일: 재예약 프로모션
   "{first_name}님, 벌써 한 달이 됐네요!
   단골 고객 특별 혜택으로 다음 {treatment} 15% 할인해드려요 🎁
   이번 달 한정이에요 → {booking_link}"

── T+60일: 미재예약 → VIP 리스트 추가 + 시즈널 프로모션 트리거 대기
```

---

### WF-04: 노쇼 복구 시퀀스

```
트리거: 예약 시간 경과 + 수동 태그 "no-show"

── T+30분: 공감 SMS
   "{first_name}님, 오늘 오시지 못하셨군요.
   괜찮으세요? 혹시 다른 날짜로 변경해드릴까요?
   → {reschedule_link}"

── T+4시간: 미반응 → 재예약 인센티브 SMS
   "다음번 예약 시 $20 크레딧 드릴게요 😊
   → {booking_link}"

── T+3일: 미반응 → 콜드 리드 워크플로우로 이동
```

---

## 4. GHL Custom Fields (Meta 연동용 필수 필드)

GHL 서브어카운트에 아래 커스텀 필드를 반드시 생성:

| Field Key | Field Name | Type | 용도 |
|---|---|---|---|
| `meta_lead_id` | Meta Lead ID | Text | Attribution |
| `meta_ad_id` | Meta Ad ID | Text | 광고 단위 추적 |
| `meta_adset_id` | Meta Ad Set ID | Text | 광고세트 추적 |
| `meta_campaign_id` | Meta Campaign ID | Text | 캠페인 추적 |
| `meta_form_id` | Meta Form ID | Text | 폼 추적 |
| `lead_timestamp` | Lead Captured At | DateTime | 리드 수신 시간 |
| `treatment` | Treatment Interest | Text | 관심 시술 |
| `lead_source_detail` | Lead Source Detail | Text | 세부 소스 |

---

## 5. SMS 응답 처리 규칙 (Conversation AI)

**자동 응답 키워드 설정:**

| 고객 응답 | 자동 처리 |
|---|---|
| "Yes", "Sure", "OK", "예약" | 예약 링크 발송 |
| "How much", "가격", "Price" | 가격 안내 + 예약 링크 |
| "No", "Not interested", "그만" | 태그 추가 "opted-out" + 시퀀스 중단 |
| "What is [treatment]" | 해당 시술 정보 페이지 링크 |
| 기타 | 직원 알림 (즉시 수동 응대) |

---

## 6. 리포팅 설정

**GHL 대시보드 위젯 설정:**
1. Today's New Leads (Tag: meta-lead, 오늘)
2. This Week's Appointments (Status: Confirmed)
3. Pipeline Conversion Rate (Stage 1 → Stage 4)
4. Revenue Closed This Month (Stage 5)
5. Average Response Time (첫 연락까지 시간)

---

## 7. GHL Snapshot 패키지 (상품화)

**우리의 MedSpa GHL Snapshot 포함 내용:**
```
✅ Pipeline (6 Stage 설계)
✅ WF-01: Meta Lead 반응 시퀀스
✅ WF-02: 예약 리마인더 시퀀스
✅ WF-03: 방문 후 리텐션 시퀀스
✅ WF-04: 노쇼 복구 시퀀스
✅ SMS 템플릿 20개
✅ Email 템플릿 10개
✅ Custom Fields 셋업
✅ Dashboard 위젯 설정
✅ 리뷰 요청 자동화
```

**판매 가격:** $997 (일회성) 또는 월 리테이너에 포함
