#!/bin/zsh
# 매주 월요일 실행 파이프라인.
# 순서가 중요하다: fetch.py 가 트렌드/계절 분석 결과를 대시보드에 'embed' 하므로
# 반드시 마지막에 돌아야 한다. 순서를 바꾸면 지난주 데이터가 박힌 페이지가 나온다.
cd "$(dirname "$0")" || exit 1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 주간 수집 시작 ====="
fail=0
/usr/bin/python3 fetch_trends.py       || { echo "!! [1/3] 트렌드 수집 실패"; fail=1; }
/usr/bin/python3 analyze_seasonality.py || { echo "!! [2/3] 계절 분석 실패"; fail=1; }
/usr/bin/python3 fetch.py              || { echo "!! [3/3] 날씨+대시보드 생성 실패"; fail=1; }
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 완료 (fail=$fail) ====="
exit $fail
