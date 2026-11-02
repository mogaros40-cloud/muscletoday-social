"""인스타 릴스·스레드 예약 업로드.

schedule.json에서 시간이 된(at <= 지금) 항목을 올리고 posted.json에 단계별로 기록한다.
같은 단계는 두 번 하지 않는다(중간에 실패해도 다음 실행이 이어서 함).
비밀값은 환경 변수(깃허브 Secrets)로만 받는다. 로컬: python post.py --env ../.secrets/meta.env
사용: python post.py [--only ko-SH001] [--dry]
"""
import json, os, sys, time, pathlib, datetime, urllib.request, urllib.parse, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent
BASE_URL = "https://mogaros40-cloud.github.io/muscletoday-social/"
IG = "https://graph.facebook.com/v23.0"
TH = "https://graph.threads.net/v1.0"
KST = datetime.timezone(datetime.timedelta(hours=9))


def load_env_file(path):
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def call(method, url, params):
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"{e.code} {url.split('?')[0]} {body[:400]}")


def wait_ready(url, token, field, ok, bad, tries=40, gap=15):
    for _ in range(tries):
        r = call("GET", url, {"fields": field, "access_token": token})
        st = r.get(field)
        if st in ok:
            return
        if st in bad:
            raise RuntimeError(f"처리 실패 {url.rsplit('/', 1)[-1]}: {r}")
        time.sleep(gap)
    raise RuntimeError(f"처리 시간 초과 {url.rsplit('/', 1)[-1]}")


def post_instagram(item, rec):
    lang = item["lang"].upper().replace("KO", "KR")
    uid, tok = os.environ[f"IG_USER_ID_{lang}"], os.environ["IG_TOKEN_KR"]
    if "ig_id" not in rec:
        if "ig_container" not in rec:
            c = call("POST", f"{IG}/{uid}/media", {
                "media_type": "REELS", "video_url": BASE_URL + item["video"],
                "caption": item["ig"]["caption"], "share_to_feed": "true",
                "thumb_offset": "0", "is_ai_generated": "true", "access_token": tok})
            rec["ig_container"] = c["id"]
            save_state()
        wait_ready(f"{IG}/{rec['ig_container']}", tok, "status_code", {"FINISHED"}, {"ERROR", "EXPIRED"})
        p = call("POST", f"{IG}/{uid}/media_publish", {"creation_id": rec["ig_container"], "access_token": tok})
        rec["ig_id"] = p["id"]
        save_state()
        print("  인스타 게시", rec["ig_id"])
    if item["ig"].get("comment") and "ig_comment" not in rec:
        c = call("POST", f"{IG}/{rec['ig_id']}/comments", {"message": item["ig"]["comment"], "access_token": tok})
        rec["ig_comment"] = c["id"]
        save_state()
        print("  인스타 첫 댓글", rec["ig_comment"])


def post_threads(item, rec):
    lang = item["lang"].upper().replace("KO", "KR")
    uid, tok = os.environ[f"THREADS_USER_ID_{lang}"], os.environ[f"THREADS_TOKEN_{lang}"]
    t = item["threads"]
    if "th_id" not in rec:
        if "th_container" not in rec:
            params = {"media_type": "VIDEO", "video_url": BASE_URL + item["video"], "text": t["text"],
                      "is_made_with_ai": "true", "access_token": tok}
            if t.get("topic"):
                params["topic_tag"] = t["topic"]
            c = call("POST", f"{TH}/{uid}/threads", params)
            rec["th_container"] = c["id"]
            save_state()
        wait_ready(f"{TH}/{rec['th_container']}", tok, "status", {"FINISHED"}, {"ERROR", "EXPIRED"})
        p = call("POST", f"{TH}/{uid}/threads_publish", {"creation_id": rec["th_container"], "access_token": tok})
        rec["th_id"] = p["id"]
        save_state()
        print("  스레드 게시", rec["th_id"])
    if t.get("reply") and "th_reply" not in rec:
        if "th_reply_container" not in rec:
            c = call("POST", f"{TH}/{uid}/threads", {"media_type": "TEXT", "text": t["reply"],
                                                     "reply_to_id": rec["th_id"], "access_token": tok})
            rec["th_reply_container"] = c["id"]
            save_state()
        time.sleep(5)
        try:
            p = call("POST", f"{TH}/{uid}/threads_publish", {"creation_id": rec["th_reply_container"], "access_token": tok})
            rec["th_reply"] = p["id"]
        except RuntimeError as e:  # 답글은 만들 때 자동 게시되는 경우가 있다
            if "already" not in str(e).lower() and "published" not in str(e).lower():
                raise
            rec["th_reply"] = rec["th_reply_container"]
        save_state()
        print("  스레드 답글", rec["th_reply"])


STATE_PATH = ROOT / "posted.json"
STATE = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}


def save_state():
    STATE_PATH.write_text(json.dumps(STATE, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    args = sys.argv[1:]
    if "--env" in args:
        load_env_file(args[args.index("--env") + 1])
    only = args[args.index("--only") + 1] if "--only" in args else None
    dry = "--dry" in args
    items = json.loads((ROOT / "schedule.json").read_text(encoding="utf-8"))
    now = datetime.datetime.now(KST)
    failed = False
    for item in items:
        key = item["key"]
        if only and key != only:
            continue
        if not only and datetime.datetime.fromisoformat(item["at"]) > now:
            continue
        rec = STATE.setdefault(key, {})
        if rec.get("done"):
            continue
        print(key, item["at"], "(시험)" if dry else "")
        if dry:
            continue
        for step in (post_instagram, post_threads):
            try:
                step(item, rec)
            except Exception as e:  # 한 플랫폼이 실패해도 다른 쪽은 올린다
                failed = True
                rec.setdefault("errors", []).append(f"{now.isoformat()} {step.__name__}: {e}"[:600])
                save_state()
                print("  실패", step.__name__, str(e)[:300])
        if all(k in rec for k in ("ig_id", "ig_comment", "th_id", "th_reply")):
            rec["done"] = datetime.datetime.now(KST).isoformat(timespec="seconds")
            save_state()
            video = ROOT / item["video"]
            if video.exists():  # 메타가 이미 복사해 갔으니 공개 사본은 지운다
                video.unlink()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
