# 한국 실시간 트렌드 현황판 MVP

Google Trends **Trending Now**의 한국·일본 급상승 키워드를 준실시간으로 모으고, 한국은 네이버 데이터랩의 연령별 검색 추이를 결합해 10대·20대 관심 가능성을 살펴보는 반응형 Streamlit 대시보드입니다.

> **중요:** 이 서비스는 **10대·20대 Google 검색어를 직접 제공하는 공식 데이터가 아니며, Google 실시간 급상승과 네이버 연령별 검색추이를 결합한 추정 모델**입니다. 연령 점수는 타깃 적합도를 판단하기 위한 보조지표이지, Google의 연령별 검색량이나 인구 통계를 의미하지 않습니다.

## 데이터 설계

- **주 신호 — Google Trends Trending Now:** 한국(`geo=KR`) RSS 내보내기에서 키워드, 검색량 구간, 게시 시각, 관련 뉴스를 수집합니다. Google 공식 도움말에 따르면 Trending Now는 평균 약 10분마다 새로 고쳐집니다.
- **국가 선택:** 한국(`KR`), 일본(`JP`), 한일 비교 모드를 지원합니다. 비교 점수는 각 시장 안에서 정규화된 상대 신호입니다.
- **스냅샷 보완:** RSS에 없는 상승률은 같은 키워드의 직전 검색량 구간 하한과 비교해 계산합니다. 성공한 다음 수집에서 사라진 키워드는 종료로 기록합니다. RSS 게시 시각은 시작 시각의 프록시입니다.
- **연령 보조 신호 — 네이버 데이터랩:** 공식 `ages` 코드 `2`(13~18세), `3`(19~24세), `4`(25~29세)를 각각 호출합니다. 일간 7일 추이의 최근 수준과 모멘텀을 Google 신호와 결합합니다.
- **공식 Google Trends API 알파:** 최대 약 2일 전까지의 일·주·월·연 분석용 데이터이므로 이 MVP의 실시간 주 데이터원으로 사용하지 않습니다.
- **Google 연령·지역:** Google Trends에는 공식 연령 필터가 없습니다. 대신 지역·하위지역 관심도를 제공하므로 한국 화면에 지역 비교 영역과 공식 Explore 연결을 제공합니다. API 알파 접근 전까지 실시간 지역 숫자를 임의로 생성하지 않습니다.
- **저장:** 모든 현재 상태, 수집 스냅샷, 네이버 연령별 시계열, 수집 로그는 SQLite에 저장합니다.

참고 문서: [Google Trending Now 도움말](https://support.google.com/trends/answer/3076011), [Google Trends API 알파](https://developers.google.com/search/apis/trends), [네이버 통합 검색어 트렌드 API](https://developers.naver.com/docs/serviceapi/datalab/search/search.md)

## 화면 구성

- 지금 뜨는 한국 Google 급상승 키워드
- 최근 4시간 / 24시간 기준 트렌드
- 활성 트렌드 / 종료 트렌드
- 10대 추정 관심 키워드
- 20대 추정 관심 키워드
- 한국 / 일본 / 한일 혼합 비교
- 일본 키워드 `원문 / 한국어 번역 / 원문 + 번역` 표시
- 모바일 반응형 레이아웃
- 국내 지역별 관심 비교 및 Google Explore 연결
- 키워드별 Google·네이버 7일 변화
- 관련 뉴스·검색어와 콘텐츠 아이디어 추천

## 빠른 실행

Python 3.10 이상을 권장합니다. `requirements.txt`는 최신 `width="stretch"` API를 위해 Streamlit 1.50 이상을 사용합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

API 키가 없어도 첫 실행 시 날짜 기준 샘플 데이터가 SQLite에 자동 생성되며 전체 화면을 확인할 수 있습니다. 왼쪽 사이드바에서 **샘플 데이터**가 기본 선택됩니다.

실데이터 모드에서 네이버 키가 없거나 아직 수집 전이면 연령 영역은 `DEMO`로 명확히 표시된 예시 데이터로 대체되며, 화면에서 Secrets 설정 방법을 안내합니다. 일본 화면에는 네이버 연령 지표를 적용하지 않습니다.

## 일본어 자동 번역

일본 또는 한일 비교 모드에서 표시 방식을 선택할 수 있습니다. 샘플 키워드는 검수된 내장 번역을 사용하고, 새 실시간 키워드는 [MyMemory REST API](https://mymemory.translated.net/doc/spec.php)의 일본어→한국어 자동 번역을 사용합니다. 번역 결과는 SQLite에 캐시되며, 서비스 오류나 한도 초과 시 일본어 원문으로 안전하게 대체됩니다. 익명 무료 사용량은 하루 5,000자이므로 뉴스 기사 전체가 아니라 짧은 키워드만 전송합니다.

## 네이버 API 연결

1. [네이버 개발자 센터](https://developers.naver.com/apps/#/register)에서 애플리케이션을 등록합니다.
2. 사용 API에 `데이터랩(검색어트렌드)`를 추가합니다.
3. `.env.example`을 참고해 환경 변수를 설정합니다. `.env`는 자동 로드하지 않으므로 셸, 운영체제, 배포 환경의 secret으로 주입하세요.

```powershell
$env:NAVER_CLIENT_ID="발급받은_ID"
$env:NAVER_CLIENT_SECRET="발급받은_SECRET"
streamlit run app.py
```

네이버 API는 일간/주간/월간 집계이고 하루 호출 한도는 1,000회입니다. 대시보드는 Google을 15분 간격으로 갱신할 수 있지만 네이버는 최근 성공 수집 후 20시간 동안 캐시합니다. 상위 Google 키워드 10개를 5개씩 나누고 세 연령 조건을 호출하므로 통상 하루 6회 요청입니다.

## 10~30분 수집 운영

대시보드에서 `15분 자동 갱신`을 켜면 브라우저 세션이 열려 있는 동안 15분마다 재실행하고, 마지막 성공 수집이 15분 이상 지났을 때만 Google을 다시 수집합니다. 운영 환경에서는 브라우저와 독립적인 스케줄러를 권장합니다.

Google만 15분마다 수집하는 명령:

```powershell
python -m collectors.google_trending_now --db trends.db --geo KR
```

일본은 같은 명령에서 `--geo JP`를 사용합니다.

```powershell
python -m collectors.google_trending_now --db trends.db --geo JP
```

Windows 작업 스케줄러나 cron에서 위 명령을 10~30분 간격으로 등록할 수 있습니다. 네이버는 일간 데이터이므로 하루 한 번만 실행합니다.

```powershell
python -m collectors.naver_datalab "AI 교과서" "프로야구 순위" --db trends.db
```

## 점수 해석

- **Google 종합 신호:** 검색량 구간 하한 40% + 상승률 25% + 시작 최신성 25% + 활성 여부 10%
- **연령 추정 적합도:** 해당 네이버 연령대의 최근 수준·7일 모멘텀 65% + Google 종합 신호 35%
- 네이버 검색 비율은 요청 결과 안에서 최대값을 100으로 둔 상대값입니다. 플랫폼 간 절대 검색량 비교나 연령 인구 점유율로 해석하면 안 됩니다.

## 구조

```text
app.py
collectors/
  __init__.py
  google_trending_now.py
  naver_datalab.py
database.py
scoring.py
mock_data.py
requirements.txt
README.md
```

Google Trending Now의 HTML/내보내기 형식은 공용 API 계약이 아니므로 변경될 수 있습니다. 수집 오류는 `collection_runs`에 남고, 빈 응답이나 네트워크 오류만으로 기존 활성 트렌드를 일괄 종료하지 않도록 방어했습니다.
