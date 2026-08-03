#!/usr/bin/env python3
"""
자사 일본 Paid/오가닉 성과 vs 도쿄 날씨 상관분석.

핵심 주의점:
  6~8월은 기온이 단조 상승하고 광고비도 함께 늘어난다. 단순 상관계수는
  "둘 다 시간에 따라 커졌다"는 이유만으로 커진다(허위상관). 그래서
    (1) 원자료 상관
    (2) 요일·시간추세·광고비를 통제한 편상관
    (3) 전일 대비 변화량(1차 차분) 상관
    (4) 서울 날씨로 같은 분석 → 위약검정(placebo)
  네 가지를 함께 본다. (4)가 (2)만큼 유의하면 날씨 효과가 아니라 추세다.

의존성 없음(표준 라이브러리만). 사용: python3 analyze_ads_weather.py
"""

import csv, json, math, os
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "일본_Paid,오가닉 데이터.csv")
WX_PATH = os.path.join(HERE, "data", "daily.json")

# ---------------- 통계 유틸 ----------------

def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 200, 3e-16, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < FPMIN: d = FPMIN
    d = 1.0 / d; h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN: d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN: c = FPMIN
        d = 1.0 / d; h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN: d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN: c = FPMIN
        d = 1.0 / d; de = d * c; h *= de
        if abs(de - 1.0) < EPS: break
    return h

def betainc(a, b, x):
    """정규화 불완전 베타함수 I_x(a,b)."""
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1.0 - x) / b

def t_pvalue(t, df):
    """양측 p값."""
    if df <= 0: return float("nan")
    return betainc(df / 2.0, 0.5, df / (df + t * t))

def pearson(x, y):
    n = len(x)
    if n < 3: return float("nan"), float("nan")
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0: return float("nan"), float("nan")
    r = sxy / math.sqrt(sxx * syy)
    r = max(-0.999999999, min(0.999999999, r))
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return r, t_pvalue(t, n - 2)

def rankdata(a):
    order = sorted(range(len(a)), key=lambda i: a[i])
    ranks = [0.0] * len(a)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and a[order[j + 1]] == a[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks

def spearman(x, y):
    return pearson(rankdata(x), rankdata(y))

def ols_resid(y, X):
    """y를 X(절편 포함)에 회귀한 잔차. 정규방정식 + 가우스 소거."""
    n, k = len(y), len(X[0])
    A = [[sum(X[i][p] * X[i][q] for i in range(n)) for q in range(k)] + [sum(X[i][p] * y[i] for i in range(n))]
         for p in range(k)]
    for c in range(k):                      # 부분 피벗팅
        piv = max(range(c, k), key=lambda r: abs(A[r][c]))
        if abs(A[piv][c]) < 1e-12: return None
        A[c], A[piv] = A[piv], A[c]
        pv = A[c][c]
        A[c] = [v / pv for v in A[c]]
        for r in range(k):
            if r != c and A[r][c] != 0:
                f = A[r][c]
                A[r] = [A[r][j] - f * A[c][j] for j in range(k + 1)]
    beta = [A[p][k] for p in range(k)]
    return [y[i] - sum(beta[p] * X[i][p] for p in range(k)) for i in range(n)]

def partial_corr(x, y, controls):
    """controls를 제거한 뒤의 x,y 상관. df = n-2-k."""
    n = len(x)
    X = [[1.0] + [c[i] for c in controls] for i in range(n)]
    rx, ry = ols_resid(x, X), ols_resid(y, X)
    if rx is None or ry is None: return float("nan"), float("nan"), 0
    k = len(controls)
    df = n - 2 - k
    if df <= 0: return float("nan"), float("nan"), df
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx); syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0: return float("nan"), float("nan"), df
    r = max(-0.999999999, min(0.999999999, sxy / math.sqrt(sxx * syy)))
    t = r * math.sqrt(df / (1 - r * r))
    return r, t_pvalue(t, df), df

def mannwhitney(a, b):
    """두 집단 비교(정규성 가정 없음). 정규근사 z와 p 반환."""
    n1, n2 = len(a), len(b)
    if n1 < 3 or n2 < 3: return float("nan"), float("nan")
    ranks = rankdata(list(a) + list(b))
    R1 = sum(ranks[:n1])
    U1 = R1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    sd = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12.0)
    if sd == 0: return float("nan"), float("nan")
    z = (U1 - mu) / sd
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return z, p

# ---------------- 데이터 로드 ----------------

def num(s):
    s = (s or "").strip().replace(",", "").replace("%", "")
    if s in ("", "-"): return None
    try: return float(s)
    except ValueError: return None

def load():
    rows = []
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        for x in csv.DictReader(f):
            if not x.get("date"): continue
            rows.append(x)
    wx = json.load(open(WX_PATH, encoding="utf-8"))
    widx = {d: i for i, d in enumerate(wx["dates"])}

    recs = []
    for x in rows:
        d = x["date"]
        if d not in widx: continue
        i = widx[d]
        pv  = num(x["page_visit"]); atc = num(x["add_to_cart"]); od = num(x["orders"])
        gmv = num(x["gross_sales(krw)"]); pc = num(x["Paid_click"]); cost = num(x["paid_cost"])
        po  = num(x["paid_order"]); pgmv = num(x["paid_gmv"])
        if None in (pv, od, cost): continue
        y, m, dd = map(int, d.split("-"))
        recs.append({
            "date": d, "dow": date(y, m, dd).weekday(),  # 0=월
            "page_visit": pv, "add_to_cart": atc or 0.0, "orders": od,
            "gmv_krw": gmv or 0.0, "paid_click": pc or 0.0, "paid_cost": cost,
            "paid_order": po or 0.0, "paid_gmv": pgmv or 0.0,
            "organic_orders": od - (po or 0.0),
            "organic_gmv": (gmv or 0.0) - (pgmv or 0.0),
            "cvr": od / pv if pv else None,
            "atc_rate": (atc or 0.0) / pv if pv else None,
            "roas": (pgmv or 0.0) / cost if cost else None,
            "cpo": cost / po if po else None,
            "tk_tmax": wx["tokyo"]["tmax"][i], "tk_tmean": wx["tokyo"]["tmean"][i],
            "tk_tmin": wx["tokyo"]["tmin"][i], "tk_prcp": wx["tokyo"]["prcp"][i],
            "tk_cloud": wx["tokyo"]["cloud"][i],
            "sl_tmean": wx["seoul"]["tmean"][i], "sl_prcp": wx["seoul"]["prcp"][i],
        })
    return recs

# ---------------- 분석 ----------------

METRICS = [
    ("page_visit", "방문수"), ("add_to_cart", "장바구니"), ("orders", "주문수"),
    ("gmv_krw", "매출(KRW)"), ("cvr", "구매전환율"), ("atc_rate", "장바구니율"),
    ("paid_click", "Paid 클릭"), ("paid_cost", "광고비"), ("paid_order", "Paid 주문"),
    ("paid_gmv", "Paid 매출"), ("roas", "ROAS"), ("cpo", "CPO"),
    ("organic_orders", "오가닉 주문"), ("organic_gmv", "오가닉 매출"),
]
WX = [("tk_tmax", "도쿄 최고기온"), ("tk_tmean", "도쿄 평균기온"),
      ("tk_prcp", "도쿄 강수량"), ("tk_cloud", "도쿄 운량")]

def stars(p):
    if p != p: return "  "
    return "**" if p < 0.01 else ("* " if p < 0.05 else ("† " if p < 0.10 else "  "))

def pair(recs, mk, wk):
    xs, ys = [], []
    for r in recs:
        if r[mk] is None or r[wk] is None: continue
        xs.append(r[wk]); ys.append(r[mk])
    return xs, ys

def main():
    recs = load()
    recs.sort(key=lambda r: r["date"])
    dropped = recs[-1]["date"]
    recs = recs[:-1]                      # 마지막 날은 수집 당일이라 미완결 → 제외
    n = len(recs)

    print("=" * 78)
    print("자사 일본 성과 × 도쿄 날씨 상관분석")
    print("=" * 78)
    print("기간: %s ~ %s  (%d일)" % (recs[0]["date"], recs[-1]["date"], n))
    print("제외: %s (수집 당일·미완결)" % dropped)
    print("유의수준 표기: ** p<0.01,  * p<0.05,  † p<0.10")
    print("n=%d 기준 p<0.05 가 되려면 |r| > %.2f 필요\n" % (n, 1.96 / math.sqrt(n - 1)))

    tot = lambda k: sum(r[k] for r in recs)
    print("[성과 요약]")
    print("  총 방문 %s · 총 주문 %d건 · 총 매출 %s원" % (f"{tot('page_visit'):,.0f}", tot("orders"), f"{tot('gmv_krw'):,.0f}"))
    print("  총 광고비 %s원 · Paid 주문 %d건 · Paid 매출 %s원 · 종합 ROAS %.0f%%"
          % (f"{tot('paid_cost'):,.0f}", tot("paid_order"), f"{tot('paid_gmv'):,.0f}",
             100 * tot("paid_gmv") / tot("paid_cost")))
    print("  Paid 매출 비중 %.1f%%\n" % (100 * tot("paid_gmv") / tot("gmv_krw")))

    tmeans = [r["tk_tmean"] for r in recs]
    print("[도쿄 날씨] 평균기온 %.1f°(%.1f~%.1f) · 강수 %.0fmm · 비 온 날 %d/%d일\n"
          % (sum(tmeans)/n, min(tmeans), max(tmeans),
             sum(r["tk_prcp"] for r in recs), sum(1 for r in recs if r["tk_prcp"] >= 1), n))

    # (1) 원자료 상관
    print("-" * 78)
    print("[1] 원자료 상관 (Pearson r) — 추세 보정 없음. 그대로 믿으면 안 되는 값")
    print("-" * 78)
    print("%-14s" % "" + "".join("%16s" % lab for _, lab in WX))
    for mk, mlab in METRICS:
        cells = []
        for wk, _ in WX:
            xs, ys = pair(recs, mk, wk)
            r, p = pearson(xs, ys)
            cells.append("%9s%-6s" % ("%+.2f" % r, stars(p)) if r == r else "%15s" % "–")
        print("%-14s" % mlab + "".join(cells))

    # (2) 편상관: 요일 + 시간추세 + 광고비 통제
    print("\n" + "-" * 78)
    print("[2] 편상관 — 요일·시간추세·광고비(log) 통제 후. 이게 실질 결론")
    print("-" * 78)
    print("%-14s" % "" + "".join("%16s" % lab for _, lab in WX))
    for mk, mlab in METRICS:
        cells = []
        for wk, _ in WX:
            xs, ys = pair(recs, mk, wk)
            sub = [r for r in recs if r[mk] is not None and r[wk] is not None]
            ctrl = [[1.0 if r["dow"] == d else 0.0 for r in sub] for d in range(6)]
            ctrl.append([float(i) for i in range(len(sub))])
            if mk != "paid_cost":
                ctrl.append([math.log(r["paid_cost"] + 1) for r in sub])
            r_, p, df = partial_corr(xs, ys, ctrl)
            cells.append("%9s%-6s" % ("%+.2f" % r_, stars(p)) if r_ == r_ else "%15s" % "–")
        print("%-14s" % mlab + "".join(cells))

    # (3) 1차 차분
    print("\n" + "-" * 78)
    print("[3] 전일 대비 변화량 상관 — 공통 추세를 물리적으로 제거")
    print("-" * 78)
    print("%-14s" % "" + "".join("%16s" % lab for _, lab in WX))
    for mk, mlab in METRICS:
        cells = []
        for wk, _ in WX:
            dx, dy = [], []
            for a, b in zip(recs, recs[1:]):
                if None in (a[mk], b[mk], a[wk], b[wk]): continue
                dx.append(b[wk] - a[wk]); dy.append(b[mk] - a[mk])
            r, p = pearson(dx, dy) if len(dx) > 3 else (float("nan"), float("nan"))
            cells.append("%9s%-6s" % ("%+.2f" % r, stars(p)) if r == r else "%15s" % "–")
        print("%-14s" % mlab + "".join(cells))

    # (4) 위약검정: 서울 날씨
    print("\n" + "-" * 78)
    print("[4] 위약검정 — 일본 매출에 영향이 없어야 할 '서울' 날씨로 동일 분석")
    print("     서울이 도쿄만큼 유의하면 → 날씨 효과가 아니라 시간추세 착시")
    print("-" * 78)
    print("%-14s%16s%16s%16s%16s" % ("", "서울기온(원자료)", "도쿄기온(원자료)", "서울기온(편상관)", "도쿄기온(편상관)"))
    for mk, mlab in METRICS:
        cells = []
        for wk in ("sl_tmean", "tk_tmean"):
            xs, ys = pair(recs, mk, wk)
            r, p = pearson(xs, ys)
            cells.append("%9s%-6s" % ("%+.2f" % r, stars(p)) if r == r else "%15s" % "–")
        for wk in ("sl_tmean", "tk_tmean"):
            xs, ys = pair(recs, mk, wk)
            sub = [r for r in recs if r[mk] is not None and r[wk] is not None]
            ctrl = [[1.0 if r["dow"] == d else 0.0 for r in sub] for d in range(6)]
            ctrl.append([float(i) for i in range(len(sub))])
            if mk != "paid_cost":
                ctrl.append([math.log(r["paid_cost"] + 1) for r in sub])
            r_, p, _ = partial_corr(xs, ys, ctrl)
            cells.append("%9s%-6s" % ("%+.2f" % r_, stars(p)) if r_ == r_ else "%15s" % "–")
        print("%-14s" % mlab + "".join(cells))

    # (5) 비 온 날 vs 안 온 날
    print("\n" + "-" * 78)
    print("[5] 비 온 날(1mm+) vs 안 온 날 — 평균 비교 (Mann-Whitney)")
    print("-" * 78)
    wet = [r for r in recs if r["tk_prcp"] >= 1]
    dry = [r for r in recs if r["tk_prcp"] < 1]
    print("  비 온 날 %d일 / 안 온 날 %d일\n" % (len(wet), len(dry)))
    print("%-14s%14s%14s%10s%10s" % ("", "비 온 날", "안 온 날", "차이", "p"))
    for mk, mlab in METRICS:
        a = [r[mk] for r in wet if r[mk] is not None]
        b = [r[mk] for r in dry if r[mk] is not None]
        if len(a) < 3 or len(b) < 3: continue
        ma, mb = sum(a)/len(a), sum(b)/len(b)
        z, p = mannwhitney(a, b)
        d = (ma/mb - 1) * 100 if mb else float("nan")
        print("%-14s%14s%14s%9s%%%8s%s"
              % (mlab, f"{ma:,.2f}", f"{mb:,.2f}", "%+.1f" % d, "%.3f" % p, stars(p)))

if __name__ == "__main__":
    main()
