#!/bin/zsh
# 맥에서 무인으로 도는 주간 파이프라인 + GitHub 반영.
#
# 왜 필요한가: 구글 트렌드는 GitHub Actions 러너 IP 를 자주 429 로 막는다.
# 집/사무실 IP 에서는 잘 되므로, 맥이 켜져 있으면 여기서 먼저 받아 올려 둔다.
# 그러면 뒤이어 도는 Actions 스케줄은 "이미 최신" 이라 수집을 건너뛰고 배포만 한다.
#
# 안전 규칙(server.py 의 [GitHub 반영] 버튼과 같은 순서):
#   반드시 공개 빌드(MUMUZ_PUBLIC=1, 매출 제외·브랜드 익명화)로 바꾸고
#   유출 검사를 통과한 뒤에만 커밋한다. push 가 끝나면 로컬 대시보드는
#   매출 포함본으로 되돌린다 — 화면에서 매출 비교가 사라지지 않게.
#
# 수동 실행:  ./run_weekly_push.sh
# 자동 실행:  ~/Library/LaunchAgents/com.mumuz.jp-weather-trends.plist

cd "$(dirname "$0")" || exit 1
PY=/usr/bin/python3
mkdir -p logs

say() { echo "$(date '+%H:%M:%S') $*"; }
die() { echo "!! $*"; exit 1; }

# 실패해도 공개 빌드가 로컬에 남지 않게 되돌린다.
# 매출 원본이 없는 무인용 클론에서는 되돌릴 것도 없으므로 건너뛴다.
restore_local() {
  [ -f "일본_Paid,오가닉 데이터.csv" ] || return 0
  say "로컬 대시보드(매출 포함) 복원"
  env -u MUMUZ_PUBLIC $PY fetch.py >/dev/null 2>&1
}

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 주간 수집+반영 시작 ====="

# 0) 원격 먼저 따라잡는다. trends_daily.csv 는 이전 수집분에 이어 붙이는 마스터라,
#    뒤처진 상태에서 돌리면 낡은 기준으로 보정된 값이 원격 것을 덮어쓴다.
git fetch -q origin || say "!! fetch 실패 — 오프라인일 수 있습니다"
if [ "$(git rev-list --count HEAD..origin/main 2>/dev/null)" != "0" ]; then
  if [ -z "$(git status --porcelain)" ]; then
    say "원격 최신으로 맞춤"
    git rebase -q origin/main || { git rebase --abort; die "원격 병합 실패"; }
  else
    say "!! 작업 중인 변경이 있어 원격을 먼저 반영하지 못했습니다 — 그대로 진행합니다"
  fi
fi

# 1) 수집 — 트렌드는 실패해도 계속 간다(날씨만이라도 갱신). 이미 최신이면 스스로 건너뛴다.
$PY fetch_trends.py       || say "!! 트렌드 수집 실패 — 날씨만 갱신하고 계속합니다"
$PY analyze_seasonality.py || die "계절 분석 실패"

# 2) 공개 빌드 (매출 제외)
say "공개 빌드"
MUMUZ_PUBLIC=1 $PY fetch.py || die "공개 빌드 실패"

# 3) 유출 검사 — 하나라도 걸리면 커밋하지 않는다
say "유출 검사"
fail=0
grep -q '"ads": *{' dashboard.html && { echo "  - dashboard.html 에 매출 데이터가 있습니다"; fail=1; }
if [ -f brand.txt ]; then
  brand=$(tr -d '[:space:]' < brand.txt)
  [ -n "$brand" ] && grep -q "$brand" dashboard.html && { echo "  - dashboard.html 에 브랜드명이 남아 있습니다"; fail=1; }
fi
git ls-files | grep -Ei 'paid|오가닉|brand\.txt' && { echo "  - 추적되면 안 되는 파일이 있습니다"; fail=1; }
[ "$fail" = "0" ] || { restore_local; die "유출 검사에 걸려 중단했습니다 — push 하지 않았습니다"; }

# 4) 커밋 — 생성물만 담는다. 작업 중인 다른 파일까지 딸려 올라가면 안 된다.
#    (먼저 커밋해서 작업트리를 깨끗이 만들어야 아래 리베이스가 된다.)
git add -A data dashboard.html
if git diff --staged --quiet; then
  say "바뀐 내용이 없습니다"
else
  git -c user.name=dashboard -c user.email=dashboard@local \
      commit -q -m "데이터 갱신 $(date '+%Y-%m-%d') [skip ci]" || { restore_local; die "커밋 실패"; }
  say "커밋 완료"
fi

# 5) 원격 병합 — Actions 가 만든 커밋이 앞서 있을 수 있다.
#    생성물이 충돌하면 방금 만든 로컬본을 채택한다(리베이스 중엔 replay 되는 쪽이 theirs).
git fetch -q origin || { restore_local; die "fetch 실패"; }
if [ "$(git rev-list --count HEAD..origin/main)" != "0" ]; then
  say "원격 변경분 병합"
  git rebase -X theirs origin/main || { git rebase --abort; restore_local; die "병합 실패 — 터미널에서 확인이 필요합니다"; }
fi

# 6) push
if [ "$(git rev-list --count origin/main..HEAD)" != "0" ]; then
  say "push"
  git push -q origin HEAD:main || { restore_local; die "push 실패"; }
  say "GitHub 반영 완료 — 1~2분 뒤 배포 페이지에 나타납니다"
else
  say "올릴 커밋이 없습니다"
fi

# 7) 로컬은 매출 포함본으로
restore_local
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 완료 ====="
