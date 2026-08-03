# 일본 날씨 · 검색 트렌드 트래커

서울/도쿄 날씨와 일본 Google 검색 트렌드를 주차별로 추적하고,
아동패션 카테고리 수요가 **날씨에 반응하는지 계절에 반응하는지** 검정하는 대시보드.

**대시보드 →** https://scuderia-kr.github.io/jp-weather-trends/

## 결론 요약

기온과 검색량은 둘 다 계절을 타기 때문에 단순 상관은 크게 나온다(최대 r=-0.61).
같은 주차의 평년값을 빼고 편차끼리 비교하면 상관이 **+0.11 이하로 떨어지고 전부 무의미**해진다.
즉 수요는 날씨가 아니라 **계절(환절기 구매 사이클)** 을 따른다. 성수기는 11월.

## 구성

| 파일 | 역할 |
|---|---|
| `fetch.py` | Open-Meteo 날씨 수집 + 대시보드 생성 |
| `fetch_trends.py` | Google 트렌드 주간 수집 (2024-01~) |
| `import_trends.py` | 트렌드 CSV 수동 가져오기 (자동 수집이 429로 막힐 때) |
| `analyze_seasonality.py` | 계절 제거 후 날씨 효과 검정 |
| `analyze_ads_weather.py` | 자사 성과 × 날씨 상관분석 (로컬 전용) |
| `server.py` | 로컬 서버 — 브라우저에서 수집/저장 버튼 사용 |

외부 패키지 없음 (파이썬 표준 라이브러리만).

## 사용

```bash
python3 server.py          # http://localhost:8765
```

또는 `dashboard.html` 을 더블클릭하면 서버 없이 열람 가능.

## 성과 데이터

매출·광고비는 **저장소에 포함되지 않는다.** 대시보드의 `[매출 CSV 불러오기]` 로
로컬 파일을 고르면 브라우저 안에서만 파싱되어 비교 차트가 그려진다(전송·저장 없음).

## 데이터 출처

- 날씨: [Open-Meteo](https://open-meteo.com) Historical Weather API (ECMWF IFS, 약 9km)
- 검색: Google 트렌드 (공식 API 없음 — 브라우저 엔드포인트 사용, 429 발생 시 CSV 수동 가져오기)
