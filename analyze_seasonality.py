#!/usr/bin/env python3
"""
아동패션 검색 수요가 '날씨'에 반응하는가, '계절'에 반응하는가.

자사 매출 데이터는 기간이 짧아 날씨 효과를 검정할 표본이 못 된다.
반면 Google 트렌드는 2024년부터 135주가 쌓여 있어 카테고리 수준 검정이 가능하다.

핵심 아이디어:
  기온과 검색량은 둘 다 계절을 탄다. 그래서 그냥 상관을 내면 계절끼리 맞물려
  큰 값이 나온다(허위상관). 진짜 물음은 이것이다.
      "같은 시기 기준으로, 예년보다 더웠던 주에 검색이 더 늘었나?"
  → 같은 주차(week-of-year)의 평년값을 빼고 편차끼리 비교하면 계절이 완전히 제거된다.

출력: data/seasonality.json  (대시보드가 읽어감)
사용: python3 analyze_seasonality.py
"""

import csv, json, math, os, urllib.request
from collections import defaultdict
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
TRENDS = os.path.join(DATA, "trends_weekly.csv")
WX_LONG = os.path.join(DATA, "tokyo_long.json")
OUT = os.path.join(DATA, "seasonality.json")

TOKYO = (35.6895, 139.6917)


def pearson(x, y):
    n = len(x)
    if n < 3: return float("nan"), float("nan")
    mx, my = sum(x)/n, sum(y)/n
    sxy = sum((a-mx)*(b-my) for a, b in zip(x, y))
    sxx = sum((a-mx)**2 for a in x); syy = sum((b-my)**2 for b in y)
    if sxx <= 0 or syy <= 0: return float("nan"), float("nan")
    r = max(-0.999999999, min(0.999999999, sxy/math.sqrt(sxx*syy)))
    t = r*math.sqrt((n-2)/(1-r*r))
    from analyze_ads_weather import t_pvalue
    return r, t_pvalue(t, n-2)


def ensure_weather(start, end):
    """도쿄 장기 일별 날씨. 없거나 기간이 모자라면 새로 받는다."""
    need = False
    if not os.path.exists(WX_LONG):
        need = True
    else:
        d = json.load(open(WX_LONG))
        if d["time"][0] > start or d["time"][-1] < end: need = True
    if need:
        url = ("https://archive-api.open-meteo.com/v1/archive"
               "?latitude=%s&longitude=%s&start_date=%s&end_date=%s"
               "&daily=temperature_2m_mean,temperature_2m_max,precipitation_sum"
               "&timezone=Asia/Tokyo&models=ecmwf_ifs" % (TOKYO[0], TOKYO[1], start, end))
        print("  도쿄 장기 날씨 수집: %s ~ %s" % (start, end))
        d = json.load(urllib.request.urlopen(url, timeout=120))["daily"]
        os.makedirs(DATA, exist_ok=True)
        json.dump(d, open(WX_LONG, "w"), ensure_ascii=False)
    return d


def main():
    rows = [r for r in csv.DictReader(open(TRENDS, encoding="utf-8-sig"))
            if r.get("complete") != "0"]
    if not rows:
        raise SystemExit("trends_weekly.csv 가 없습니다. 먼저 fetch_trends.py 를 실행하세요.")
    KW = [k for k in rows[0] if k not in ("week_start", "week_end", "complete")]

    wx = ensure_weather(rows[0]["week_start"], rows[-1]["week_end"])
    tmean = dict(zip(wx["time"], wx["temperature_2m_mean"]))
    prcp = dict(zip(wx["time"], wx["precipitation_sum"]))

    W = []
    for r in rows:
        s = date.fromisoformat(r["week_start"])
        days = [(s + timedelta(days=i)).isoformat() for i in range(7)]
        t = [tmean[d] for d in days if d in tmean and tmean[d] is not None]
        p = [prcp[d] for d in days if d in prcp and prcp[d] is not None]
        if len(t) < 7: continue
        rec = {"start": r["week_start"], "t": sum(t)/7, "p": sum(p),
               "woy": s.isocalendar()[1], "month": s.month}
        for k in KW:
            rec[k] = float(r[k]) if r.get(k) else None
        if any(rec[k] is None for k in KW): continue
        W.append(rec)

    print("분석 대상 %d주 (%s ~ %s)" % (len(W), W[0]["start"], W[-1]["start"]))

    # 주차별 평년값
    def norm(key):
        b = defaultdict(list)
        for w in W: b[w["woy"]].append(w[key])
        return {k: sum(v)/len(v) for k, v in b.items()}
    nt, np_ = norm("t"), norm("p")
    nk = {k: norm(k) for k in KW}

    result = {"n_weeks": len(W), "keywords": KW,
              "period": [W[0]["start"], W[-1]["start"]],
              "raw": {}, "deseason": {}, "rain": {}, "monthly": {}, "scatter": {}}

    for k in KW:
        r, p = pearson([w["t"] for w in W], [w[k] for w in W])
        result["raw"][k] = {"r": round(r, 3), "p": round(p, 5)}
        dt = [w["t"] - nt[w["woy"]] for w in W]
        dk = [w[k] - nk[k][w["woy"]] for w in W]
        r, p = pearson(dt, dk)
        result["deseason"][k] = {"r": round(r, 3), "p": round(p, 5)}
        result["scatter"][k] = [[round(a, 2), round(b, 2)] for a, b in zip(dt, dk)]
        dp = [w["p"] - np_[w["woy"]] for w in W]
        r, p = pearson(dp, dk)
        result["rain"][k] = {"r": round(r, 3), "p": round(p, 5)}

    # 월별 평균
    mb = defaultdict(lambda: defaultdict(list))
    mt = defaultdict(list)
    for w in W:
        mt[w["month"]].append(w["t"])
        for k in KW: mb[k][w["month"]].append(w[k])
    result["monthly"] = {k: [round(sum(mb[k][m])/len(mb[k][m]), 1) if mb[k][m] else None
                             for m in range(1, 13)] for k in KW}
    result["monthly_tmean"] = [round(sum(mt[m])/len(mt[m]), 1) if mt[m] else None
                               for m in range(1, 13)]
    result["scatter_t"] = [round(w["t"] - nt[w["woy"]], 2) for w in W]

    # 주차별 원시 시계열 — 대시보드의 1:1 비교용
    result["weeks"] = {
        "starts": [w["start"] for w in W],
        "tmean": [round(w["t"], 2) for w in W],
        "prcp": [round(w["p"], 1) for w in W],
        "series": {k: [w[k] for w in W] for k in KW},
    }

    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)

    print("\n%-14s %14s %16s %16s" % ("키워드", "원자료 r", "계절제거 r", "강수편차 r"))
    def s(p): return "**" if p < 0.01 else ("* " if p < 0.05 else "  ")
    for k in KW:
        a, b, c = result["raw"][k], result["deseason"][k], result["rain"][k]
        print("%-14s %+10.2f%s %+12.2f%s %+12.2f%s"
              % (k, a["r"], s(a["p"]), b["r"], s(b["p"]), c["r"], s(c["p"])))
    peak = {k: 1 + max(range(12), key=lambda m: result["monthly"][k][m] or -1) for k in KW}
    low = {k: 1 + min(range(12), key=lambda m: result["monthly"][k][m] if result["monthly"][k][m] is not None else 999) for k in KW}
    print("\n성수기/비수기:", {k: "%d월↑ %d월↓" % (peak[k], low[k]) for k in KW})
    print("저장:", OUT)


if __name__ == "__main__":
    main()
