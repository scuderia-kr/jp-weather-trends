#!/usr/bin/env python3
"""
Google 트렌드 일본 키워드 수집기.

두 가지 시계열을 만든다.

  ① 주간 장기 시계열  (메인, 2024-01-01 ~ 어제)
     Google은 9개월을 넘는 요청에 자동으로 주간(WEEK) 해상도를 준다.
     전 구간을 매번 한 번의 요청으로 통째로 다시 받으므로 스케일이 항상 일관된다.
     (창을 나눠 받아 이어붙이면 스케일이 어긋나는데, 전량 재수집이면 그 문제가 없다.)
     주의: Google의 주간 버킷은 '일요일 시작'이다. 월~일이 아니다.
     → data/trends_weekly.csv

  ② 월~일 주간 집계  (보조, 최근 90일)
     ①의 일요일 기준이 아쉬울 때를 위해 일별로 받아 직접 월~일로 끊는다.
     일별은 약 9개월 창까지만 제공되므로 90일 창을 쓰고, 이전 수집분과 겹치는
     구간의 비율로 보정(chaining)해 마스터에 누적한다.
     → data/trends_daily.csv, data/trends_monsun.csv

Google 트렌드에는 공식 API가 없다. 브라우저 내부 엔드포인트를 호출하며
쿠키 없이 부르면 429가 난다. 매 실행마다 쿠키를 먼저 받고, 429는 재시도한다.

사용:  python3 fetch_trends.py
"""

import csv, json, os, time, urllib.error, urllib.parse, urllib.request
import http.cookiejar as cookiejar
from datetime import date, datetime, timedelta

KEYWORDS = ["ベビー服", "コニー", "ユニクロ キッズ", "プティマイン"]
GEO = "JP"
HISTORY_START = "2024-01-01"   # 주간 장기 시계열 시작일
DAILY_WINDOW = 90              # 월~일 집계용 일별 창
# Google 할당량이 빡빡할 때 주간만 받고 싶으면 MUMUZ_SKIP_DAILY=1 로 실행
SKIP_DAILY = os.environ.get("MUMUZ_SKIP_DAILY") == "1"
TZ_MIN = -540                  # JST
HL = "ja"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW_DIR = os.path.join(DATA, "trends")
WEEKLY_CSV = os.path.join(DATA, "trends_weekly.csv")     # ① 일요일 시작 주간
DAILY_CSV = os.path.join(DATA, "trends_daily.csv")       # ② 일별 마스터
MONSUN_CSV = os.path.join(DATA, "trends_monsun.csv")     # ② 월~일 집계

UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Referer": "https://trends.google.com/",
}

# 쿠키(NID)는 반드시 .google.com 도메인으로 받아야 한다.
# trends.google.co.jp 로 워밍업하면 NID 가 .google.co.jp 에만 붙어서,
# 정작 API 를 부르는 trends.google.com 요청에는 쿠키가 하나도 실리지 않는다.
# → 무조건 429. (2026-08-10 자동 갱신이 이 이유로 통째로 실패했다.)
WARMUP = "https://trends.google.com/?geo=" + GEO


def opener_with_cookies():
    jar = cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = list(UA.items())
    op.open(WARMUP, timeout=30).read(2048)
    if not any(c.domain.endswith("google.com") for c in jar):
        raise SystemExit("google.com 쿠키를 받지 못했습니다. 네트워크/차단 여부를 확인하세요.")
    return op


class RateLimited(SystemExit):
    """429 가 계속될 때. 호출부(server.py)가 사용자에게 다르게 안내하려고 구분한다."""


# 429 는 IP 차단이 아니라 '세션 단위 스로틀'이다. 실측하면 1차 429 → 재시도 200 이
# 대부분이고, 쿠키를 새로 받으면 성공률이 더 오른다. 그래서 오래 기다리는 대신
# 짧게 여러 번 + 매 재시도마다 쿠키 재발급으로 간다. (최대 대기 ~110초)
BACKOFF = [5, 10, 20, 30, 45]


def get_json(op, url, tries=None, reopen=None):
    """429/503은 쿠키를 새로 받아 재시도. 응답 앞의 )]}' 접두어를 제거한다.

    reopen: 새 opener 를 만들어 주는 콜백. 주면 재시도마다 세션을 갈아 끼운다.
    """
    waits = BACKOFF if tries is None else BACKOFF[:tries]
    last = None
    for k in range(len(waits) + 1):
        try:
            body = op.open(url, timeout=60).read().decode("utf-8")
            return json.loads(body[body.index("{"):]), op
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 503):
                if k < len(waits):
                    print("  HTTP %d — %d초 후 재시도(%d/%d), 쿠키 재발급"
                          % (e.code, waits[k], k + 1, len(waits)), flush=True)
                    time.sleep(waits[k])
                    if reopen:
                        try:
                            op = reopen()
                        except Exception:
                            pass      # 쿠키 재발급 실패해도 기존 세션으로 한 번 더
                    continue
                break                 # 재시도 소진 → 아래에서 안내 메시지로 종료
            raise
    if getattr(last, "code", None) == 429:
        raise RateLimited(
            "RATE_LIMITED: Google 트렌드가 계속 요청을 제한합니다(429). "
            "공식 API 가 아니라 브라우저용 엔드포인트를 쓰기 때문에 짧은 시간에 "
            "여러 번 호출하면 막힙니다. 잠시 뒤 다시 시도해 주세요. 데이터는 그대로입니다.")
    raise SystemExit("Google 트렌드 요청 실패: %s" % last)


def fetch_series(op, timeframe):
    """{키워드: {날짜: 값}}, 해상도, (갱신된)opener 반환."""
    req = {"comparisonItem": [{"keyword": k, "geo": GEO, "time": timeframe} for k in KEYWORDS],
           "category": 0, "property": ""}
    url = "https://trends.google.com/trends/api/explore?" + urllib.parse.urlencode(
        {"hl": HL, "tz": str(TZ_MIN), "req": json.dumps(req, ensure_ascii=False)})
    data, op = get_json(op, url, reopen=opener_with_cookies)
    w = next(x for x in data["widgets"] if x.get("id") == "TIMESERIES")
    res = w["request"].get("resolution", "?")

    url2 = "https://trends.google.com/trends/api/widgetdata/multiline?" + urllib.parse.urlencode(
        {"hl": HL, "tz": str(TZ_MIN), "req": json.dumps(w["request"], ensure_ascii=False),
         "token": w["token"]})
    time.sleep(2)
    # 토큰은 발급받은 세션에 묶여 있어, 여기서는 쿠키를 갈면 안 된다
    data2, op = get_json(op, url2)
    tl = data2["default"]["timelineData"]

    out = {k: {} for k in KEYWORDS}
    for pt in tl:
        d = datetime.utcfromtimestamp(int(pt["time"])).date().isoformat()
        for ki, k in enumerate(KEYWORDS):
            out[k][d] = float(pt["value"][ki])
    return out, res, op


# ---------------- ① 주간 장기 시계열 ----------------

def write_weekly(series, end):
    """전량 재수집이므로 통째로 덮어쓴다.

    Google은 요청 종료일이 속한 주를 통째로 돌려준다. 그 주는 아직 진행 중이라
    며칠치만 반영된 값인데, 완결된 주와 나란히 두면 급락/급등으로 오독된다.
    → complete 플래그로 구분해 둔다(대시보드는 미완결 주를 빼고 그린다).
    """
    dates = sorted(next(iter(series.values())).keys())
    os.makedirs(DATA, exist_ok=True)
    incomplete = 0
    with open(WEEKLY_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["week_start", "week_end"] + KEYWORDS + ["complete"])
        for d in dates:
            ds = date.fromisoformat(d)
            de = ds + timedelta(days=6)
            ok = 1 if de <= end else 0
            if not ok: incomplete += 1
            w.writerow([d, de.isoformat()] + [series[k].get(d, "") for k in KEYWORDS] + [ok])
    return dates, incomplete


# ---------------- ② 월~일 집계 ----------------

def load_daily_master():
    if not os.path.exists(DAILY_CSV): return {}
    m = {}
    with open(DAILY_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            m[row["date"]] = {k: (float(row[k]) if row.get(k) not in (None, "") else None)
                              for k in KEYWORDS}
    return m


def save_daily_master(m):
    with open(DAILY_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["date"] + KEYWORDS)
        for d in sorted(m):
            w.writerow([d] + ["" if m[d].get(k) is None else round(m[d][k], 3) for k in KEYWORDS])


def chain_merge(master, new):
    """겹치는 구간의 평균비로 new를 master 스케일에 맞춘 뒤 병합."""
    dates_new = sorted(next(iter(new.values())).keys())
    overlap = [d for d in dates_new if d in master]
    factors = {}
    for k in KEYWORDS:
        pairs = [(master[d][k], new[k][d]) for d in overlap
                 if master[d].get(k) is not None and new[k].get(d) is not None]
        pairs = [(a, b) for a, b in pairs if b > 0]
        if len(pairs) >= 7:
            sa = sum(a for a, _ in pairs); sb = sum(b for _, b in pairs)
            factors[k] = (sa / sb) if sb > 0 else 1.0
        else:
            factors[k] = 1.0
    for d in dates_new:
        row = master.setdefault(d, {k: None for k in KEYWORDS})
        for k in KEYWORDS:
            v = new[k].get(d)
            if v is None: continue
            if row.get(k) is None:
                row[k] = v * factors[k]
    return factors, len(overlap)


def write_monsun(master):
    """일별 마스터를 월~일로 끊어 집계. 7일 다 있는 주만 확정으로 본다."""
    ds = sorted(master)
    if not ds: return []
    first = date.fromisoformat(ds[0]); last = date.fromisoformat(ds[-1])
    cur = first - timedelta(days=first.weekday())        # 그 주 월요일
    rows = []
    while cur <= last:
        days = [(cur + timedelta(days=i)).isoformat() for i in range(7)]
        rec = {"week_start": cur.isoformat(), "week_end": (cur + timedelta(days=6)).isoformat()}
        cov = []
        for k in KEYWORDS:
            got = [master[d][k] for d in days if d in master and master[d].get(k) is not None]
            rec[k] = round(sum(got) / len(got), 2) if got else ""
            cov.append(len(got))
        rec["days_covered"] = min(cov) if cov else 0
        if rec["days_covered"] > 0: rows.append(rec)
        cur += timedelta(days=7)
    hdr = ["week_start", "week_end"] + KEYWORDS + ["days_covered"]
    with open(MONSUN_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=hdr)
        w.writeheader()
        for r in rows: w.writerow({h: r.get(h, "") for h in hdr})
    return rows


def main():
    today = date.today()
    end = today - timedelta(days=1)          # 어제까지 (당일은 미완결)
    print("Google 트렌드 수집  geo=%s  키워드 %d개" % (GEO, len(KEYWORDS)), flush=True)
    op = opener_with_cookies()
    os.makedirs(RAW_DIR, exist_ok=True)

    # ① 주간 장기
    tf1 = "%s %s" % (HISTORY_START, end.isoformat())
    print("\n[1/2] 주간 장기 시계열 수집: %s" % tf1, flush=True)
    wk, res, op = fetch_series(op, tf1)
    print("    해상도=%s, 수신 %d주" % (res, len(next(iter(wk.values())))))
    if res != "WEEK":
        print("    ! 주간이 아닌 %s 로 왔습니다. 기간이 짧으면 일별로 옵니다." % res)
    dates, incomplete = write_weekly(wk, end)
    print("    저장: %s (%s ~ %s)" % (WEEKLY_CSV, dates[0], dates[-1]))
    if incomplete:
        print("    진행 중인 주 %d개는 complete=0 으로 표시(대시보드에서 제외)" % incomplete)
    with open(os.path.join(RAW_DIR, "weekly_%s.json" % today), "w", encoding="utf-8") as f:
        json.dump({"fetched": today.isoformat(), "timeframe": tf1, "resolution": res,
                   "geo": GEO, "data": wk}, f, ensure_ascii=False)

    # ② 월~일 (보조)
    # 이건 '있으면 좋은' 부가 지표다. 여기서 실패해도 ①은 이미 저장됐으므로
    # 전체를 실패로 만들지 않는다. Google 할당량을 아끼는 효과도 있다.
    rows = []
    if SKIP_DAILY:
        print("\n[2/2] 월~일 집계 건너뜀 (MUMUZ_SKIP_DAILY=1)", flush=True)
    else:
        dstart = end - timedelta(days=DAILY_WINDOW - 1)
        tf2 = "%s %s" % (dstart.isoformat(), end.isoformat())
        print("\n[2/2] 월~일 집계용 일별 수집: %s" % tf2, flush=True)
        try:
            time.sleep(3)
            dl, res2, op = fetch_series(op, tf2)
            print("    해상도=%s, 수신 %d일" % (res2, len(next(iter(dl.values())))), flush=True)
            master = load_daily_master(); had = len(master)
            factors, ov = chain_merge(master, dl)
            save_daily_master(master)
            rows = write_monsun(master)
            print("    마스터 %d일 → %d일 (겹침 %d일, 보정 %s)"
                  % (had, len(master), ov, {k: round(v, 3) for k, v in factors.items()}), flush=True)
            print("    저장: %s (%d주)" % (MONSUN_CSV, len(rows)), flush=True)
        except SystemExit as e:
            print("    ! 월~일 보조 수집 실패 — 건너뜁니다(주간 데이터는 정상 저장됨).", flush=True)
            print("      사유: %s" % str(e)[:160], flush=True)
        except Exception as e:
            print("    ! 월~일 보조 수집 오류 — 건너뜁니다: %s" % e, flush=True)

    # 직전주 요약
    ms = today - timedelta(days=today.weekday()) - timedelta(days=7)
    prev = next((r for r in rows if r["week_start"] == ms.isoformat()), None)
    if prev:
        print("\n[직전주 %s ~ %s · 월~일]" % (prev["week_start"], prev["week_end"]))
        for k in KEYWORDS:
            print("  %-12s %6s   (수집일수 %s/7)" % (k, prev[k], prev["days_covered"]))


if __name__ == "__main__":
    main()
