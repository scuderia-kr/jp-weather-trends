#!/usr/bin/env python3
"""
서울 vs 도쿄 일별 기온 트래커.

Open-Meteo Archive API에서 START_DATE부터 어제/오늘까지의 일별 기온을 받아
  - data/daily.csv   : 원본 데이터 (엑셀에서 바로 열림)
  - data/daily.json  : 동일 데이터 JSON
  - dashboard.html   : 데이터가 내장된 단일 파일 대시보드
를 생성한다. API 키 불필요, 외부 파이썬 패키지 불필요.

사용:  python3 fetch.py
"""

import csv
import json
import os
import urllib.request
from datetime import date, datetime

START_DATE = "2026-01-01"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY_VARS = ",".join([
    "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
    "precipitation_sum",     # mm, 비+눈(녹은 물 환산) 합계
    "rain_sum",              # mm, 비만
    "snowfall_sum",          # cm, 눈만
    "precipitation_hours",   # h, 강수가 있었던 시간
    "weather_code",          # WMO 코드, 그날의 대표 날씨 (아이콘 표기용)
    "cloud_cover_mean",      # %, 일평균 운량
])

# 모델을 명시적으로 고정한다. 기본값 best_match 는 Open-Meteo 가 알아서 고르므로
# 나중에 조용히 바뀔 수 있고, 모델 간 일평균 차이가 최대 3.7°까지 난다.
#   ecmwf_ifs : ECMWF IFS 분석장, 약 9km, 지연 거의 없음 (오늘 날짜까지 나옴)
#   era5      : ERA5 재분석, 약 28km, 약 5일 지연, 확정판
#   era5_land : ERA5-Land, 약 11km, 약 5일 지연
MODEL = "ecmwf_ifs"

# 공개 배포 빌드 여부. 1 이면 매출 데이터를 빼고 브랜드명을 익명화한다.
PUBLIC = os.environ.get("MUMUZ_PUBLIC") == "1"


def _brand():
    """브랜드명은 소스에 두지 않는다(공개 저장소에 그대로 남기 때문).
    로컬에서만 brand.txt(gitignore 대상)나 BRAND 환경변수로 지정한다."""
    v = os.environ.get("BRAND")
    if v:
        return v.strip()
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            t = f.read().strip()
            if t:
                return t
    return "우리 브랜드"


BRAND = _brand()

CITIES = [
    {"key": "seoul", "label": "서울", "lat": 37.5665, "lon": 126.9780, "tz": "Asia/Seoul"},
    {"key": "tokyo", "label": "도쿄", "lat": 35.6895, "lon": 139.6917, "tz": "Asia/Tokyo"},
]

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")


def fetch_city(city, start, end):
    params = (
        "?latitude=%s&longitude=%s&start_date=%s&end_date=%s&daily=%s&timezone=%s&models=%s"
        % (city["lat"], city["lon"], start, end, DAILY_VARS, city["tz"], MODEL)
    )
    with urllib.request.urlopen(ARCHIVE_URL + params, timeout=60) as resp:
        payload = json.load(resp)
    # API 는 요청 좌표를 모델 격자점으로 스냅한다. 실제로 쓰인 격자를 기록해 둔다.
    return payload["daily"], {
        "lat": payload["latitude"],
        "lon": payload["longitude"],
        "elevation": payload.get("elevation"),
    }


def load_trends():
    """fetch_trends.py 가 만든 검색 트렌드를 대시보드에 함께 실어 준다. 없으면 None."""
    wpath = os.path.join(DATA_DIR, "trends_weekly.csv")
    if not os.path.exists(wpath):
        return None
    with open(wpath, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None
    kws = [k for k in rows[0].keys()
           if k not in ("week_start", "week_end", "days_covered", "complete")]
    # 진행 중인 주는 며칠치만 반영된 값이라 완결 주와 비교하면 오독된다 → 제외
    total = len(rows)
    rows = [r for r in rows if r.get("complete", "1") != "0"]
    out = {
        "keywords": kws,
        # ① 주간 장기 (Google 기준 = 일요일 시작)
        "starts": [r["week_start"] for r in rows],
        "ends": [r["week_end"] for r in rows],
        "series": {k: [float(r[k]) if r.get(k) else None for r in rows] for k in kws},
        "excluded_partial": total - len(rows),
        "monsun": [],
    }
    mpath = os.path.join(DATA_DIR, "trends_monsun.csv")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                out["monsun"].append({
                    "start": r["week_start"], "end": r["week_end"],
                    "days": r.get("days_covered", ""),
                    "vals": {k: (float(r[k]) if r.get(k) else None) for k in kws},
                })
    return out


def load_season():
    """analyze_seasonality.py 결과 (계절 vs 날씨 판정). 없으면 None."""
    p = os.path.join(DATA_DIR, "seasonality.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


ADS_CSV = os.path.join(HERE, "일본_Paid,오가닉 데이터.csv")


def _num(s):
    s = (s or "").strip().replace(",", "").replace("%", "")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_ads():
    """자사 일본 성과를 대시보드에 함께 실어 검색·날씨와 같은 축에서 비교한다.

    주의: 여기서 실은 값은 dashboard.html 안에 그대로 박힌다. 즉 그 파일을 공개하면
    매출·광고비가 전부 노출된다. 공개 배포용 빌드는 MUMUZ_PUBLIC=1 로 제외한다.
    """
    if PUBLIC:
        print("  [공개 모드] 매출 데이터 제외 + 브랜드명 익명화")
        return None
    if not os.path.exists(ADS_CSV):
        return None
    rows = []
    with open(ADS_CSV, encoding="utf-8-sig") as f:
        for x in csv.DictReader(f):
            if not x.get("date"):
                continue
            pv, od = _num(x["page_visit"]), _num(x["orders"])
            if pv is None or od is None:
                continue
            rows.append({
                "date": x["date"], "visits": pv, "orders": od,
                "gmv": _num(x["gross_sales(krw)"]) or 0.0,
                "cost": _num(x["paid_cost"]) or 0.0,
            })
    if not rows:
        return None
    rows.sort(key=lambda r: r["date"])
    # 마지막 날은 수집 당일이라 미완결 → 제외 (분석 스크립트와 동일 규칙)
    dropped = rows[-1]["date"]
    rows = rows[:-1]
    return {
        "dates": [r["date"] for r in rows],
        "visits": [r["visits"] for r in rows],
        "orders": [r["orders"] for r in rows],
        "gmv": [r["gmv"] for r in rows],
        "cost": [r["cost"] for r in rows],
        "dropped": dropped,
    }


def main():
    end = date.today().isoformat()
    print("수집 기간: %s ~ %s" % (START_DATE, end))

    series = {}
    grids = {}
    dates = None
    for city in CITIES:
        daily, grid = fetch_city(city, START_DATE, end)
        grids[city["key"]] = grid
        if dates is None:
            dates = daily["time"]
        elif daily["time"] != dates:
            raise SystemExit("도시별 날짜 축이 어긋납니다. API 응답을 확인하세요.")
        series[city["key"]] = {
            "tmax": daily["temperature_2m_max"],
            "tmin": daily["temperature_2m_min"],
            "tmean": daily["temperature_2m_mean"],
            "prcp": daily["precipitation_sum"],
            "rain": daily["rain_sum"],
            "snow": daily["snowfall_sum"],
            "phours": daily["precipitation_hours"],
            "wcode": daily["weather_code"],
            "cloud": daily["cloud_cover_mean"],
        }
        print("  %s: %d일  (격자 %.4f, %.4f · 고도 %sm)"
              % (city["label"], len(daily["time"]), grid["lat"], grid["lon"], grid["elevation"]))

    os.makedirs(DATA_DIR, exist_ok=True)

    # --- CSV ---
    csv_path = os.path.join(DATA_DIR, "daily.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "date",
            "seoul_tmax", "seoul_tmin", "seoul_tmean",
            "seoul_precip_mm", "seoul_rain_mm", "seoul_snow_cm", "seoul_precip_hours",
            "seoul_weather_code", "seoul_cloud_pct",
            "tokyo_tmax", "tokyo_tmin", "tokyo_tmean",
            "tokyo_precip_mm", "tokyo_rain_mm", "tokyo_snow_cm", "tokyo_precip_hours",
            "tokyo_weather_code", "tokyo_cloud_pct",
            "tmean_diff_seoul_minus_tokyo",
            "seoul_tmean_change",  # 전일 대비 일평균기온 변화
            "tokyo_tmean_change",
        ])
        s, t = series["seoul"], series["tokyo"]
        for i, d in enumerate(dates):
            diff = None
            if s["tmean"][i] is not None and t["tmean"][i] is not None:
                diff = round(s["tmean"][i] - t["tmean"][i], 1)

            def delta(city):
                if i == 0 or city["tmean"][i] is None or city["tmean"][i-1] is None:
                    return None
                return round(city["tmean"][i] - city["tmean"][i-1], 1)

            w.writerow([
                d,
                s["tmax"][i], s["tmin"][i], s["tmean"][i],
                s["prcp"][i], s["rain"][i], s["snow"][i], s["phours"][i],
                s["wcode"][i], s["cloud"][i],
                t["tmax"][i], t["tmin"][i], t["tmean"][i],
                t["prcp"][i], t["rain"][i], t["snow"][i], t["phours"][i],
                t["wcode"][i], t["cloud"][i],
                diff, delta(s), delta(t),
            ])

    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "trends": load_trends(),
        "ads": load_ads(),
        "season": load_season(),
        "model": MODEL,
        "grids": grids,
        "start": dates[0],
        "end": dates[-1],
        "dates": dates,
        "seoul": series["seoul"],
        "tokyo": series["tokyo"],
    }

    json_path = os.path.join(DATA_DIR, "daily.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    # --- HTML (데이터 내장 → file:// 로 열어도 동작) ---
    tpl_path = os.path.join(HERE, "template.html")
    with open(tpl_path, encoding="utf-8") as f:
        tpl = f.read()
    # 브랜드명은 빌드 시점에 주입한다. 공개 배포본에는 실제 이름을 넣지 않는다.
    brand = "우리 브랜드" if PUBLIC else BRAND
    brand_file = "브랜드" if PUBLIC else BRAND
    html = (tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
               .replace("__BRANDFILE__", brand_file)
               .replace("__BRAND__", brand))
    out_path = os.path.join(HERE, "dashboard.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print("\n생성 완료")
    print("  " + csv_path)
    print("  " + json_path)
    print("  " + out_path)


if __name__ == "__main__":
    main()
