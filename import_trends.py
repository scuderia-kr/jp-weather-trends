#!/usr/bin/env python3
"""
Google 트렌드 화면에서 내려받은 CSV 를 그대로 가져온다.

왜 필요한가:
  fetch_trends.py 는 비공식 엔드포인트를 쓰기 때문에 Google 이 429 로 막으면
  손을 쓸 수 없다. 반면 트렌드 웹페이지의 '다운로드' 버튼은 일반 브라우저 요청이라
  항상 동작한다. 자동 수집이 막혔을 때의 확실한 우회로다.

가져오는 방법:
  1) 대시보드의 [Google 트렌드에서 직접 보기] 링크로 이동
     (또는 https://trends.google.co.jp/trends/explore?date=2024-01-01%202026-12-31&geo=JP
      &q=ベビー服,コニー,ユニクロ キッズ,プティマイン)
  2) '시간 경과에 따른 관심도' 카드 오른쪽 위 ↓ 다운로드 → multiTimeline.csv
  3) python3 import_trends.py ~/Downloads/multiTimeline.csv

CSV 형식 (Google 이 주는 그대로):
    카테고리: 모든 카테고리
    <빈 줄>
    주,ベビー服: (일본),コニー: (일본),...
    2023-12-31,47,38,24,54
  · 첫 열은 주 시작일(일요일) 또는 날짜
  · 값이 아주 작으면 "<1" 로 온다 → 0 으로 본다
"""

import csv, io, os, re, sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
WEEKLY_CSV = os.path.join(DATA, "trends_weekly.csv")

try:
    from fetch_trends import KEYWORDS
except Exception:
    KEYWORDS = ["ベビー服", "コニー", "ユニクロ キッズ", "プティマイン"]


def _clean_header(h):
    """'ベビー服: (일본)' → 'ベビー服'"""
    return re.sub(r"\s*:\s*\(.*\)\s*$", "", (h or "").strip()).strip()


def _num(v):
    v = (v or "").strip()
    if v in ("", "-"):
        return None
    if v.startswith("<"):          # "<1" = 1 미만
        return 0.0
    try:
        return float(v)
    except ValueError:
        return None


def parse(text):
    """Google 트렌드 export 를 {날짜: {키워드: 값}} 으로. (데이터, 키워드목록, 단위)"""
    # BOM 제거 + 헤더 줄 찾기 (날짜로 시작하는 행 직전이 헤더)
    lines = [l for l in text.replace("﻿", "").splitlines()]
    hdr_i = None
    for i, l in enumerate(lines):
        cells = next(csv.reader([l])) if l.strip() else []
        if len(cells) >= 2 and re.match(r"^\d{4}-\d{2}-\d{2}", (cells[0] or "").strip()):
            hdr_i = i - 1
            break
    if hdr_i is None or hdr_i < 0:
        raise SystemExit("날짜로 시작하는 데이터 행을 찾지 못했습니다. "
                         "Google 트렌드 '시간 경과에 따른 관심도' CSV 가 맞는지 확인하세요.")

    header = next(csv.reader([lines[hdr_i]]))
    unit = (header[0] or "").strip()                    # '주' / '일' / 'Week' / 'Day'
    cols = [_clean_header(h) for h in header[1:]]

    out = {}
    for l in lines[hdr_i + 1:]:
        if not l.strip():
            continue
        cells = next(csv.reader([l]))
        d = (cells[0] or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            continue
        row = {}
        for j, name in enumerate(cols):
            if j + 1 < len(cells):
                row[name] = _num(cells[j + 1])
        out[d] = row
    if not out:
        raise SystemExit("데이터 행을 하나도 읽지 못했습니다.")
    return out, cols, unit


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit("사용법: python3 import_trends.py <다운로드한 CSV 경로>")
    src = os.path.expanduser(sys.argv[1])
    if not os.path.exists(src):
        raise SystemExit("파일이 없습니다: %s" % src)

    with open(src, encoding="utf-8-sig", errors="replace") as f:
        data, cols, unit = parse(f.read())

    dates = sorted(data)
    step = (date.fromisoformat(dates[1]) - date.fromisoformat(dates[0])).days if len(dates) > 1 else 7
    if step != 7:
        raise SystemExit("주간 데이터가 아닙니다(간격 %d일). 트렌드에서 기간을 9개월 이상으로 "
                         "잡으면 주간으로 나옵니다." % step)

    # 파일의 키워드 열과 우리 KEYWORDS 를 맞춘다
    missing = [k for k in KEYWORDS if k not in cols]
    if missing:
        print("! CSV 에 없는 키워드: %s" % ", ".join(missing))
        print("  CSV 열: %s" % ", ".join(cols))
    use = [k for k in KEYWORDS if k in cols] or cols

    today = date.today()
    os.makedirs(DATA, exist_ok=True)
    n_inc = 0
    with open(WEEKLY_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["week_start", "week_end"] + use + ["complete"])
        for d in dates:
            ds = date.fromisoformat(d)
            de = ds + timedelta(days=6)
            ok = 1 if de < today else 0            # 진행 중인 주는 구분해 둔다
            if not ok:
                n_inc += 1
            w.writerow([d, de.isoformat()]
                       + ["" if data[d].get(k) is None else data[d][k] for k in use] + [ok])

    print("가져오기 완료")
    print("  원본: %s" % src)
    print("  기간: %s ~ %s (%d주, 단위 '%s')" % (dates[0], dates[-1], len(dates), unit))
    print("  키워드: %s" % ", ".join(use))
    if n_inc:
        print("  진행 중인 주 %d개는 complete=0 으로 표시(대시보드에서 제외)" % n_inc)
    print("  저장: %s" % WEEKLY_CSV)
    print("\n다음: python3 analyze_seasonality.py && python3 fetch.py")


if __name__ == "__main__":
    main()
