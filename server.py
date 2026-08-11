#!/usr/bin/env python3
"""
대시보드 로컬 서버 — 브라우저의 [업데이트 실행] / [저장] 버튼을 받아 수집 스크립트를 돌린다.

왜 서버가 필요한가:
  file:// 로 연 HTML 은 보안상 파이썬을 실행하거나 디스크에 쓸 수 없다.
  그리고 launchd 자동실행은 macOS TCC 가 Desktop 접근을 막아 동작하지 않는다
  (Operation not permitted). 사용자가 직접 띄운 서버는 터미널 권한을 물려받아
  두 문제를 모두 피한다.

흐름 (2단계 — 받아보고 나서 확정):
  [업데이트 실행]  data/ 를 _backup/ 에 스냅샷 → 트렌드 수집 → 무엇이 바뀌었는지 표로 반환
                   (아직 대시보드는 예전 데이터 그대로)
  [저장]           계절 분석 + 날씨 갱신 + 대시보드 재생성 → 스냅샷 삭제 (확정)
  [되돌리기]       스냅샷을 되돌려 수집 이전 상태로 복구

사용:  python3 server.py     → http://localhost:8765 접속
"""

import csv, http.server, json, os, re, shutil, socketserver, subprocess, sys, threading, urllib.parse
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BACKUP = os.path.join(DATA, "_backup")
PORT = int(os.environ.get("PORT", "8765"))
PY = sys.executable or "/usr/bin/python3"

WEEKLY = os.path.join(DATA, "trends_weekly.csv")

_lock = threading.Lock()
# 수집이 1~2분 걸릴 수 있어 브라우저가 멈춘 것처럼 보인다.
# 진행 상황을 실시간으로 담아 두고 /api/progress 로 흘려 준다.
_prog = {"running": False, "step": "", "lines": [], "started": None}


def _mark(step):
    _prog["step"] = step
    _prog["lines"].append("▸ " + step)


def run(script, timeout=900):
    """스크립트 하나 실행. 출력을 줄 단위로 진행상황에 쌓는다. (성공여부, 출력)."""
    lines = []
    try:
        p = subprocess.Popen([PY, "-u", os.path.join(HERE, script)], cwd=HERE,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = datetime.now() + timedelta(seconds=timeout)
        for line in p.stdout:
            line = line.rstrip()
            if line:
                lines.append(line)
                _prog["lines"].append(line)
                del _prog["lines"][:-40]      # 최근 40줄만 유지
            if datetime.now() > deadline:
                p.kill()
                return False, "\n".join(lines) + "\n%s 실행이 %d초를 넘겨 중단했습니다." % (script, timeout)
        p.wait()
        return p.returncode == 0, "\n".join(lines).strip()
    except Exception as e:
        return False, "\n".join(lines) + "\n%s 실행 실패: %s" % (script, e)


def _brand_name():
    p = os.path.join(HERE, "brand.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get("BRAND", "").strip()


def _restore_local():
    """공개 빌드로 덮어쓴 dashboard.html 을 매출 포함 로컬본으로 되돌린다."""
    env = dict(os.environ); env.pop("MUMUZ_PUBLIC", None)
    subprocess.run([PY, os.path.join(HERE, "fetch.py")], cwd=HERE,
                   capture_output=True, env=env, timeout=600)


def read_weekly(path):
    if not os.path.exists(path):
        return {}, []
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}, []
    kw = [k for k in rows[0] if k not in ("week_start", "week_end", "complete")]
    return {r["week_start"]: r for r in rows}, kw


def last_week_range(today=None):
    """직전 '완결된' 주 — Google 기준 일요일 시작 ~ 토요일 끝."""
    today = today or date.today()
    # 이번 주 일요일(오늘 포함 가장 최근 일요일)
    sun = today - timedelta(days=(today.weekday() + 1) % 7)
    start = sun - timedelta(days=7)
    return start, start + timedelta(days=6)


def snapshot():
    if os.path.exists(BACKUP):
        shutil.rmtree(BACKUP)
    os.makedirs(BACKUP, exist_ok=True)
    for name in os.listdir(DATA):
        src = os.path.join(DATA, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(BACKUP, name))


def restore():
    if not os.path.exists(BACKUP):
        return False
    for name in os.listdir(BACKUP):
        shutil.copy2(os.path.join(BACKUP, name), os.path.join(DATA, name))
    shutil.rmtree(BACKUP)
    return True


def diff_summary(before, after, kw):
    """수집 전후 비교 — 새로 생긴 주와 값이 바뀐 주."""
    added, changed = [], []
    for ws, row in sorted(after.items()):
        if row.get("complete") == "0":
            continue
        old = before.get(ws)
        vals = {k: row.get(k, "") for k in kw}
        if old is None:
            added.append({"week": ws, "end": row.get("week_end", ""), "vals": vals})
        else:
            d = {k: [old.get(k, ""), row.get(k, "")] for k in kw
                 if (old.get(k) or "") != (row.get(k) or "")}
            if d:
                changed.append({"week": ws, "end": row.get("week_end", ""), "diff": d})
    return added, changed


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def log_message(self, fmt, *args):
        pass  # 요청 로그는 조용히

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self.path = "/dashboard.html"
            return super().do_GET()
        if path == "/api/progress":
            el = (datetime.now() - _prog["started"]).total_seconds() if _prog["started"] else 0
            return self._json({"ok": True, "running": _prog["running"], "step": _prog["step"],
                               "elapsed": int(el), "lines": _prog["lines"][-14:]})
        if path == "/api/status":
            cur, kw = read_weekly(WEEKLY)
            done = [w for w, r in cur.items() if r.get("complete") != "0"]
            s, e = last_week_range()
            return self._json({
                "ok": True,
                "weeks": len(done),
                "latest": max(done) if done else None,
                "target": {"start": s.isoformat(), "end": e.isoformat()},
                "have_target": s.isoformat() in cur and cur[s.isoformat()].get("complete") != "0",
                "pending": os.path.exists(BACKUP),
            })
        return super().do_GET()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not _lock.acquire(blocking=False):
            return self._json({"ok": False, "error": "이미 다른 작업이 실행 중입니다."}, 409)
        try:
            if path == "/api/update":
                return self._update()
            if path == "/api/save":
                return self._save()
            if path == "/api/publish":
                return self._publish()
            if path == "/api/import":
                return self._import()
            if path == "/api/revert":
                ok = restore()
                return self._json({"ok": ok,
                                   "message": "수집 이전 상태로 되돌렸습니다." if ok else "되돌릴 스냅샷이 없습니다."})
            return self._json({"ok": False, "error": "알 수 없는 경로"}, 404)
        finally:
            _lock.release()

    def _update(self):
        _prog.update(running=True, step="", lines=[], started=datetime.now())
        _mark("현재 데이터 백업")
        before, _ = read_weekly(WEEKLY)
        snapshot()
        _mark("Google 트렌드 수집 시작 (첫 요청은 429가 나서 재시도합니다)")
        ok, out = run("fetch_trends.py", timeout=900)
        _prog["running"] = False
        if not ok:
            restore()
            # 429 는 흔한 일시적 실패라 원인과 대처를 따로 알려 준다
            limited = "RATE_LIMITED" in out or "429" in out
            err = ("Google 트렌드가 요청을 제한했습니다(429). 데이터는 손상되지 않았고 "
                   "이전 상태 그대로입니다. 10~30분 뒤에 [업데이트 실행]을 다시 눌러 주세요."
                   if limited else "트렌드 수집에 실패했습니다 — 이전 상태로 되돌렸습니다.")
            return self._json({"ok": False, "error": err, "rate_limited": limited,
                               "log": out}, 429 if limited else 500)
        after, kw = read_weekly(WEEKLY)
        added, changed = diff_summary(before, after, kw)
        s, e = last_week_range()
        return self._json({
            "ok": True, "keywords": kw, "added": added, "changed": changed,
            "target": {"start": s.isoformat(), "end": e.isoformat()},
            "target_included": any(a["week"] == s.isoformat() for a in added)
                               or s.isoformat() in after,
            "log": out,
            "message": "받아왔습니다. 내용을 확인하고 [저장]을 누르면 확정됩니다.",
        })

    def _publish(self):
        """공개 빌드로 다시 만들어 검사한 뒤 GitHub 에 push 한다.

        순서가 안전의 핵심이다. 반드시 공개 빌드(매출 제외 + 익명화)로 바꾼 뒤
        검사에 통과해야만 커밋한다. push 가 끝나면 로컬 대시보드는 매출 포함본으로
        되돌려 놓는다 — 화면에서 매출 비교가 사라지지 않게.
        """
        _prog.update(running=True, step="", lines=[], started=datetime.now())
        try:
            _mark("공개용으로 다시 빌드 (매출 제외 · 브랜드 익명화)")
            env = dict(os.environ, MUMUZ_PUBLIC="1")
            p = subprocess.run([PY, "-u", os.path.join(HERE, "fetch.py")], cwd=HERE,
                               capture_output=True, text=True, timeout=600, env=env)
            if p.returncode != 0:
                return self._json({"ok": False, "error": "공개 빌드 실패",
                                   "log": (p.stdout or "") + (p.stderr or "")}, 500)

            _mark("유출 검사")
            dash = os.path.join(HERE, "dashboard.html")
            html = open(dash, encoding="utf-8").read()
            problems = []
            if '"ads": {' in html:
                problems.append("dashboard.html 에 매출 데이터가 있습니다")
            brand = _brand_name()
            if brand and brand in html:
                problems.append("dashboard.html 에 브랜드명이 남아 있습니다")
            tracked = subprocess.run(["git", "ls-files"], cwd=HERE,
                                     capture_output=True, text=True).stdout.split("\n")
            for t in tracked:
                low = t.lower()
                if "paid" in low or "오가닉" in t or t.strip() == "brand.txt":
                    problems.append("추적되면 안 되는 파일: " + t)
            if problems:
                _restore_local()
                return self._json({"ok": False, "error": "유출 검사에 걸려 중단했습니다.",
                                   "problems": problems}, 400)

            # 순서가 중요하다: 먼저 커밋해서 작업트리를 깨끗이 만든 뒤에야 리베이스가 된다.
            _mark("커밋")
            subprocess.run(["git", "add", "-A"], cwd=HERE, capture_output=True)
            has = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=HERE).returncode != 0
            if has:
                # [skip ci] 를 붙이면 push 트리거가 죽어서 Pages 가 재배포되지 않는다.
                # 저장소만 최신이고 공개 페이지는 옛날 것인 상태가 되므로 붙이지 않는다.
                # push 로 뜬 워크플로는 되커밋하지 않아 무한 반복은 생기지 않는다.
                msg = "데이터 갱신 " + date.today().isoformat()
                subprocess.run(["git", "-c", "user.name=dashboard",
                                "-c", "user.email=dashboard@local",
                                "commit", "-q", "-m", msg], cwd=HERE, capture_output=True)

            _mark("원격 변경분 병합")
            subprocess.run(["git", "fetch", "-q", "origin"], cwd=HERE, capture_output=True, timeout=180)
            behind = subprocess.run(["git", "rev-list", "--count", "HEAD..origin/main"],
                                    cwd=HERE, capture_output=True, text=True).stdout.strip()
            if behind and behind != "0":
                # Actions 가 만든 커밋이 앞서 있다. 생성물이 충돌하면 방금 만든
                # 로컬본을 채택한다(리베이스 중에는 replay 되는 쪽이 'theirs').
                rb = subprocess.run(["git", "rebase", "-X", "theirs", "origin/main"],
                                    cwd=HERE, capture_output=True, text=True, timeout=180)
                if rb.returncode != 0:
                    subprocess.run(["git", "rebase", "--abort"], cwd=HERE, capture_output=True)
                    _restore_local()
                    return self._json({"ok": False,
                                       "error": "원격과 병합하지 못했습니다. 터미널에서 확인이 필요합니다.",
                                       "log": (rb.stdout or "") + (rb.stderr or "")}, 409)

            _mark("push")
            pushed = False
            ahead = subprocess.run(["git", "rev-list", "--count", "origin/main..HEAD"],
                                   cwd=HERE, capture_output=True, text=True).stdout.strip()
            if ahead and ahead != "0":
                pr = subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=HERE,
                                    capture_output=True, text=True, timeout=180)
                if pr.returncode != 0:
                    _restore_local()
                    return self._json({"ok": False, "error": "push 실패",
                                       "log": (pr.stdout or "") + (pr.stderr or "")}, 500)
                pushed = True

            url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=HERE,
                                 capture_output=True, text=True).stdout.strip()
            m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", url)
            page = "https://%s.github.io/%s/" % (m.group(1), m.group(2)) if m else ""
            _restore_local()
            return self._json({"ok": True, "pushed": pushed, "page": page,
                               "message": ("GitHub 에 반영했습니다. 1~2분 뒤 배포 페이지에 나타납니다."
                                           if pushed else "바뀐 내용이 없어 push 하지 않았습니다.")})
        except Exception as e:
            _restore_local()
            return self._json({"ok": False, "error": str(e)}, 500)
        finally:
            _prog["running"] = False

    def _import(self):
        """브라우저가 올린 Google 트렌드 CSV 를 그대로 받아 반영한다.
        429 로 자동 수집이 막혔을 때의 확실한 우회로."""
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 8 * 1024 * 1024:
            return self._json({"ok": False, "error": "파일이 비었거나 너무 큽니다(최대 8MB)."}, 400)
        raw = self.rfile.read(n)
        tmp = os.path.join(DATA, "_upload.csv")
        os.makedirs(DATA, exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(raw)
        before, _ = read_weekly(WEEKLY)
        snapshot()
        _prog.update(running=True, step="", lines=[], started=datetime.now())
        _mark("업로드한 CSV 반영")
        try:
            p = subprocess.run([PY, "-u", os.path.join(HERE, "import_trends.py"), tmp],
                               cwd=HERE, capture_output=True, text=True, timeout=120)
            out = ((p.stdout or "") + (p.stderr or "")).strip()
            ok = p.returncode == 0
        except Exception as e:
            ok, out = False, str(e)
        finally:
            _prog["running"] = False
            if os.path.exists(tmp):
                os.remove(tmp)
        if not ok:
            restore()
            return self._json({"ok": False, "error": "CSV 를 읽지 못했습니다 — 이전 상태로 되돌렸습니다.",
                               "log": out}, 400)
        after, kw = read_weekly(WEEKLY)
        added, changed = diff_summary(before, after, kw)
        return self._json({"ok": True, "keywords": kw, "added": added, "changed": changed,
                           "log": out,
                           "message": "CSV 를 반영했습니다. 확인 후 [저장]을 누르면 확정됩니다."})

    def _save(self):
        _prog.update(running=True, step="", lines=[], started=datetime.now())
        steps = []
        for script, label in (("analyze_seasonality.py", "계절 분석"),
                              ("fetch.py", "날씨 갱신 + 대시보드 생성")):
            _mark(label)
            ok, out = run(script)
            steps.append({"step": label, "ok": ok, "log": out[-1500:]})
            if not ok:
                _prog["running"] = False
                return self._json({"ok": False, "error": "%s 단계에서 실패했습니다." % label,
                                   "steps": steps}, 500)
        _prog["running"] = False
        if os.path.exists(BACKUP):
            shutil.rmtree(BACKUP)   # 확정 → 스냅샷 파기
        return self._json({"ok": True, "steps": steps,
                           "message": "저장했습니다. 페이지를 새로고침하면 반영됩니다."})


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    if not os.path.exists(os.path.join(HERE, "dashboard.html")):
        print("dashboard.html 이 없습니다. 먼저 python3 fetch.py 를 실행하세요.")
        return
    with Server(("127.0.0.1", PORT), Handler) as httpd:
        print("=" * 58)
        print("  일본 날씨·검색 트렌드 대시보드 서버")
        print("=" * 58)
        print("  브라우저에서 열기 →  http://localhost:%d" % PORT)
        print("  종료: Ctrl+C")
        print("=" * 58)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n서버를 종료했습니다.")


if __name__ == "__main__":
    main()
