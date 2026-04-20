# WF-05: Referral (레퍼럴) 자동화 플레이북

> **목적:** 기존 만족 고객이 지인을 소개하도록 자동화된 인센티브 시퀀스를 운영한다.
> **왜 중요:** 신규 리드 CAC(고객 획득 비용) 대비 레퍼럴 CAC는 0에 가깝다. MedSpa 산업에서 레퍼럴은 신뢰도가 높아 Show Rate도 높다.

---

## 1. 워크플로우 레시피 (WF-05)

```
트리거: Stage 변경 → "방문 완료" + 방문 후 30일 경과 (WF-03 완료 후)

── T+0 (방문 후 30일): 레퍼럴 첫 번째 요청 SMS
   "{first_name}님, 지난달 {treatment} 결과 잘 나오고 계신가요? 😊
   저희가 특별한 부탁이 있어요 — 주변에 피부 고민이 있는 분께
   저희 클리닉을 소개해 주시면, {first_name}님께 $50 크레딧을 드려요!
   (소개받은 분이 예약하시는 날 적용됩니다) 💕"

── T+7일: 미반응 → 레퍼럴 카드 이미지 + 링크 SMS
   "{first_name}님! 지인 소개 한 분만 해주셔도 다음 {treatment}가
   $50 저렴해져요 😊 소개 링크: {referral_link}
   링크 공유하시면 자동으로 추적돼요!"

── T+14일: 레퍼럴 완료 확인
   시스템: 소개 연락처 등록 여부 확인
   완료 시 → 즉시 $50 크레딧 GHL에 태깅
   미완료 시 → 시퀀스 종료 (과도한 연락 방지)
```

---

## 2. GHL 설정

### 레퍼럴 추적 커스텀 필드

| Field Key | Field Name | Type | 용도 |
|---|---|---|---|
| `referral_source` | Referral Source | Text | 누가 소개했는지 |
| `referral_credit` | Referral Credit ($) | Number | 크레딧 금액 |
| `referral_link` | Referral Link | Text | 개인화된 소개 링크 |
| `referral_count` | Referrals Sent | Number | 소개한 건수 |

### 레퍼럴 링크 생성 방법
```
GHL Calendar URL + UTM: 
?utm_source=referral&utm_medium=sms&utm_campaign={contact_id}

예: https://[clinic].com/book?utm_source=referral&utm_medium=sms&utm_campaign=abc123
```
→ 신규 연락처 생성 시 `utm_campaign` 값으로 소개자 ID 추적

### 소개 완료 자동화
```
트리거: 신규 Contact 생성 + utm_campaign 파라미터 있음

── 즉시: 소개자 Contact 업데이트
   referral_count += 1
   referral_credit += $50 태깅

── 소개자에게 SMS 발송:
   "{first_name}님, {새 고객 이름}님이 예약하셨어요! 🎉
   $50 크레딧이 적용됐습니다. 다음 방문 시 자동으로 적용돼요!"
```

---

## 3. 인센티브 구조

| 소개 건수 | 인센티브 | 설명 |
|---|---|---|
| 1건 | $50 크레딧 | 기본 |
| 3건 | $200 크레딧 (보너스 $50) | "VIP 소개자" 태그 |
| 5건+ | 무료 시술 1회 (최대 $200) | "Ambassador" 태그 + 온라인 리뷰 요청 |

### Ambassador 프로그램 (5건+ 소개자)
```
── Ambassador 태그 추가 → 특별 그룹 SMS 채널
── 월 1회 "Ambassador 전용 선착순 예약" 오퍼
── 인스타그램 태그 + 온라인 리뷰 협조 요청
── 추천사 제작 (사전 동의 시) → 광고 크리에이티브 활용
```

---

## 4. 메트릭 & KPI

| 지표 | 목표 |
|---|---|
| 레퍼럴 요청 응답률 | > 15% |
| 레퍼럴 → 예약 전환율 | > 40% (일반 리드 25% 대비 높음) |
| 레퍼럴 Show Rate | > 80% (신뢰 기반이므로 높음) |
| 레퍼럴 CAC | < $50 (크레딧 비용만) |

### GHL 대시보드 추가 위젯
- This Month Referrals (Tag: referral-lead)
- Referral Conversion Rate
- Top Referrers List

---

## 5. 적용 타이밍 (전체 워크플로우 흐름)

```
WF-01 → WF-02 → WF-03 → WF-04 (노쇼 시) → WF-05 (방문 완료 후 30일)
  ↑__________________________________(WF-05가 새 리드 생성 → WF-01 재시작)
```

> 레퍼럴로 들어온 신규 리드는 WF-01부터 다시 시작 → 자동화 플라이휠 완성
