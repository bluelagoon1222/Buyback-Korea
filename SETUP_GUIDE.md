# 자사주 CHECK (Buyback-Korea) — 설치 안내 (6단계, 약 20분)

Finviz-Korea·액티브 ETF CHECK와 같은 방식입니다. 이번에는 **DART 인증키 등록(3단계)** 이 하나 추가됩니다.
저장소 이름은 `Buyback-Korea` 로 가정했습니다. 다른 이름을 쓰시면 링크의 `Buyback-Korea` 부분만 바꿔 주세요.

> 압축 파일 안의 파일은 메모장으로 열어 저장하지 마세요(한글이 깨집니다). 그대로 업로드만 하시면 됩니다.

---

## 1단계. DART 인증키 발급 (무료, 5분)

1. 링크 열기: https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do
2. 이메일 등 필수 항목을 입력해 **인증키 신청**(회원가입)을 완료합니다. 이메일 인증 후 로그인하면 키가 발급됩니다.
3. 발급된 키 확인: https://opendart.fss.or.kr/mng/userApiKeyListView.do  (영문·숫자 40자리)
   - 이 키는 **비밀번호처럼 취급**해 주세요. 채팅이나 파일에 붙이지 말고 아래 4단계에서 GitHub Secret에만 넣습니다.
   - 하루 호출 한도가 있지만(수만 건) 이 사이트는 첫 실행 약 7천 건, 이후 하루 수백 건만 사용합니다.

---

## 2단계. 새 저장소 만들기

1. 링크 열기: https://github.com/new
2. **Repository name** 에 입력:
   ```
   Buyback-Korea
   ```
3. **Public** 선택 (사이트 공개에 필수)
4. 다른 체크박스는 건드리지 말고 맨 아래 초록색 **Create repository** 클릭

---

## 3단계. 파일 업로드

1. 받으신 `Buyback-Korea.zip` 을 압축 해제합니다. 폴더 안에 `index.html`, `README.md`, `scripts`, `data`, `.github`, `.nojekyll` 이 보여야 합니다.
2. 링크 열기: https://github.com/bluelagoon1222/Buyback-Korea/upload/main
3. 압축 해제한 폴더 **안의 파일과 폴더 전부(Ctrl+A)** 를 브라우저 업로드 영역에 끌어다 놓습니다.
   - `.github` 폴더와 `.nojekyll` 파일이 함께 올라가야 합니다. 보이지 않으면 윈도우 탐색기 **보기 → 숨긴 항목 표시** 를 켜 주세요.
4. 목록이 다 뜨면 초록색 **Commit changes** 클릭

---

## 4단계. DART 인증키를 Secret으로 등록 (한 번만)

1. 링크 열기: https://github.com/bluelagoon1222/Buyback-Korea/settings/secrets/actions/new
2. **Name** 칸에 정확히 아래와 같이 입력 (대문자, 밑줄):
   ```
   DART_API_KEY
   ```
3. **Secret** 칸에 1단계에서 발급받은 40자리 키를 붙여 넣고 **Add secret** 클릭
   - 등록 후에는 값이 보이지 않는 것이 정상입니다. 잘못 넣었으면 같은 화면에서 Update 하시면 됩니다.

---

## 5단계. 자동 수집 권한 + 공개 사이트 켜기

1. 링크 열기: https://github.com/bluelagoon1222/Buyback-Korea/settings/actions
   맨 아래 **Workflow permissions** 에서 **Read and write permissions** 선택 → **Save**
2. 링크 열기: https://github.com/bluelagoon1222/Buyback-Korea/settings/pages
   **Source** = `Deploy from a branch`, **Branch** = `main` / `/ (root)` 선택 → **Save**
3. 1~2분 후 아래 주소로 사이트가 열립니다. (처음에는 노란색 "샘플 데이터" 띠가 보입니다 — 정상입니다)
   ```
   https://bluelagoon1222.github.io/Buyback-Korea/
   ```

---

## 6단계. 첫 수집 실행

1. 링크 열기: https://github.com/bluelagoon1222/Buyback-Korea/actions/workflows/update.yml
2. 오른쪽 **Run workflow** → 다시 초록색 **Run workflow** (칸은 그대로 두세요)
3. 첫 실행은 2024년 1월부터의 공시를 모두 받으므로 **40분~2시간** 걸립니다. 중간에 멈춰도 다음 자동 실행에서 이어서 받도록 되어 있어 한 번만 눌러 두시면 됩니다.
   - **초록 체크**: 성공. 사이트를 새로고침하면 샘플 띠가 사라지고 실제 데이터가 보입니다. 과거 수집이 덜 끝났으면 파란 안내 띠가 남은 월 수를 표시합니다.
   - **빨간 X**: 실패. 실행 항목 클릭 → `update` → **Collect data from DART and Naver** 단계를 펼쳐 마지막 20줄 정도를 복사해 저에게 붙여 주세요. 바로 수정본을 드리겠습니다.
   - 로그에 `DART_API_KEY is not set` 이 보이면 4단계 Secret 이름이 틀린 경우입니다.

이후에는 **평일 08:30·19:30(KST)** 에 자동 갱신되고, 실패하면 22:30에 한 번 더 시도합니다.

---

## 자주 묻는 것

- **어떤 공시가 들어가나요?** 주요사항보고서의 자기주식 취득 결정·신탁계약 체결·신탁계약 해지·처분 결정, 거래소 공시의 자기주식 소각 결정, 기업가치 제고 계획(밸류업) 공시. 상장사만 포함하며 정정 공시는 "정정" 표시가 붙고 합계에서는 제외됩니다.
- **주가 반응은 어떻게 계산하나요?** 공시 전일 종가 대비 익일(D+1)·5거래일·20거래일·최근 종가 등락률과, 같은 기간 KOSPI(코스닥 종목은 KOSDAQ) 대비 초과수익입니다. 네이버 금융 가격 기준이며 배당은 제외됩니다.
- **첫 실행 후 "상세 미수집 N건" 안내가 보이는데요?** DART 상세 조회나 문서 해석이 일시적으로 실패한 건입니다. 다음 실행에서 최대 3회까지 다시 시도하고, 그래도 안 되는 건은 공시 링크만 표시됩니다.
- **60일 동안 저장소에 변화가 없으면 예약 실행이 꺼진다고 들었는데요?** 이 사이트는 매일 데이터 파일을 스스로 커밋하므로 해당되지 않습니다. 혹시 "scheduled workflow disabled" 메일이 오면 6단계 링크에서 **Enable workflow** 를 한 번 눌러 주시면 됩니다.
- **수집 시작 연도를 바꾸고 싶으면?** `.github/workflows/update.yml` 의 `--since 20240101` 을 원하는 날짜로 바꾸면 됩니다(영문 파일이므로 GitHub 화면에서 연필 아이콘으로 바로 수정 가능).
