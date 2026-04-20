# 코드 이슈 & 수정 가이드

> 파일: `scripts/ghl_meta_bridge.py`
> 마지막 분석: 2026-04-16

---

## BUG-01: CAPI event_id 누락 (우선순위 Critical)

### 문제
```python
# 현재 코드 (ghl_meta_bridge.py ~210행)
payload = {
    "data": [{
        "event_name": event_name,
        "event_time": int(time.time()),
        "action_source": "crm",
        "user_data": user_data,
        "custom_data": custom_data or {},
    }],
    "access_token": self.token,
}
```

**증상:** 브라우저 Pixel과 서버 CAPI가 동일 이벤트를 2번 카운트 → Meta 전환 데이터 2배 부풀림 → 최적화 신호 왜곡

### 수정
```python
import uuid

async def send_capi_event(self, event_name: str, contact: dict, 
                          custom_data: dict = None, event_id: str = None) -> dict:
    
    # event_id 없으면 자동 생성 (timestamp + contact identifier)
    if not event_id:
        event_id = f"evt_{event_name}_{int(time.time())}_{contact.get('email', '')[:8]}"
    
    payload = {
        "data": [{
            "event_name": event_name,
            "event_time": int(time.time()),
            "event_id": event_id,          # ← 추가
            "action_source": "crm",
            "user_data": user_data,
            "custom_data": custom_data or {},
        }],
        "access_token": self.token,
    }
```

**브라우저 픽셀 측에서도 동일 event_id 사용:**
```javascript
// GHL 랜딩 페이지 헤더에 추가
fbq('track', 'Lead', {}, {eventID: 'evt_Lead_[TIMESTAMP]_[EMAIL_PREFIX]'});
```

---

## BUG-02: fbclid 미저장 (우선순위 High)

### 문제
`fbclid`(광고 클릭 ID)가 GHL에 저장되지 않음 → Schedule, Purchase CAPI 이벤트에 `fbc` 파라미터 포함 불가 → EMQ 점수 -2~3점 → CPB 최적화 불완전

### 수정 방법 (두 단계)

**Step 1: GHL 랜딩 페이지에 fbclid 캡처 스크립트 추가**
```html
<script>
// 랜딩 페이지 <head>에 추가
(function() {
  const urlParams = new URLSearchParams(window.location.search);
  const fbclid = urlParams.get('fbclid');
  if (fbclid) {
    // GHL 히든 필드에 저장
    localStorage.setItem('meta_fbclid', fbclid);
    localStorage.setItem('meta_fbclid_ts', Date.now());
    // fbc 형식: fb.{version}.{timestamp}.{fbclid}
    const fbc = `fb.1.${Date.now()}.${fbclid}`;
    document.cookie = `_fbc=${fbc}; path=/; max-age=7776000`; // 90일
  }
})();

// 폼 제출 시 GHL 히든 필드에 주입
document.addEventListener('DOMContentLoaded', function() {
  const fbclid = localStorage.getItem('meta_fbclid');
  const hiddenField = document.querySelector('input[name="meta_fbclid"]');
  if (fbclid && hiddenField) hiddenField.value = fbclid;
});
</script>
```

**Step 2: GHL 커스텀 필드 추가**
| Field Key | Field Name | Type |
|---|---|---|
| `meta_fbclid` | Meta Click ID (fbclid) | Text |
| `meta_fbc` | Meta Browser Click ID (fbc) | Text |
| `meta_fbp` | Meta Browser ID (fbp) | Text |

**Step 3: CAPI 페이로드에 fbc 포함**
```python
# process_ghl_booking 함수 수정
contact = {
    "email": lead["email"],
    "phone": lead["phone"],
    "first_name": lead["first_name"],
    "last_name": lead["last_name"],
    "fbc": lead.get("meta_fbc", ""),    # ← 추가
    "fbp": lead.get("meta_fbp", ""),    # ← 추가
}

# send_capi_event 함수 수정
user_data = {
    "em": [sha256(contact.get("email", ""))],
    "ph": [sha256(normalize_phone(contact.get("phone", "")))],
    "fn": [sha256(contact.get("first_name", ""))],
    "ln": [sha256(contact.get("last_name", ""))],
}
# fbp, fbc는 해싱하면 안 됨 — 그대로 추가
if contact.get("fbc"):
    user_data["fbc"] = contact["fbc"]
if contact.get("fbp"):
    user_data["fbp"] = contact["fbp"]
```

---

## BUG-03: 예약 가치 하드코딩 (우선순위 Medium)

### 문제
```python
# ghl_meta_bridge.py 353행
custom_data = {
    "value": 150.00,  # ← 모든 클라이언트, 모든 시술에 고정
    "currency": "USD",
}
```

### 수정
**.env 파일에 클라이언트별 설정 추가:**
```env
# 시술별 평균 가치 (쉼표 구분)
TREATMENT_VALUES={"botox": 350, "filler": 800, "hifu": 2000, "morpheus8": 1500, "default": 300}
```

**코드 수정:**
```python
import json

TREATMENT_VALUES = json.loads(os.environ.get(
    "TREATMENT_VALUES", 
    '{"default": 300}'
))

# process_ghl_booking에서
treatment_key = (treatment or "").lower().replace(" ", "")
treatment_value = TREATMENT_VALUES.get(treatment_key, TREATMENT_VALUES.get("default", 150))

custom_data = {
    "value": treatment_value,
    "currency": "USD",
    "treatment_type": treatment or lead.get("treatment", ""),
}
```

---

## SECURITY-01: Bot Token 하드코딩 (우선순위 High)

### 문제
```python
# marketing_bridge.py 12행
TOKEN = '8412230853:AAGJJxFtp7wc3EpbORlBgEj1HvEP-eYqLgY'  # 노출됨
MY_CHAT_ID = '1460264431'
```

### 수정
```python
# marketing_bridge.py 수정
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MY_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
```

**.env.example에 추가 (이미 포함됨):**
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

## 수정 우선순위 체크리스트

- [ ] **BUG-01** CAPI event_id 추가 (`send_capi_event` 함수)
- [ ] **SECURITY-01** Bot Token .env 이동
- [ ] **BUG-02** GHL 랜딩 페이지 fbclid 캡처 스크립트 설치
- [ ] **BUG-02** GHL 커스텀 필드 fbclid, fbc, fbp 추가
- [ ] **BUG-02** CAPI 페이로드에 fbc, fbp 포함
- [ ] **BUG-03** 시술별 가치 .env 설정 + 코드 수정
- [ ] 수정 후 Meta Events Manager에서 테스트 이벤트 검증
- [ ] EMQ 점수 48시간 후 확인 (목표: Lead 8.0+)
