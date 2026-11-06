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


def post_tiktok(item, rec):
    """한국어 계정만. Buffer(무료, 틱톡 승인 앱)로 바로 공개 게시: 글·해시태그·AI 표시 포함.
    (예전 방식 tiktok.py = 임시 보관함 업로드는 Buffer가 막힐 때 대비로 남겨 둠)"""
    if item["lang"] != "ko" or "tt_id" in rec:
        return
    now = datetime.datetime.now(KST)
    # 11-06 사용자 1안: Buffer API 한도(30일 1,000번)를 다 써서 12-03 02:29까지 막힘.
    # 그동안은 Claude가 Buffer 웹에서 예약하고 posted.json tt_id에 "web:..."을 적는다. 여기선 시도 안 함.
    # 오류로 남기지 않고 조용히 넘긴다(15분마다 기록이 쌓이지 않게). 빠진 편은 status.json에 missing으로 보인다.
    if now < datetime.datetime(2026, 12, 3, 3, 0, tzinfo=KST):
        print("  틱톡: Buffer API 중지 기간(12-03까지), Buffer 웹 예약 필요")
        return
    # 한도가 풀린 뒤 밀린 편이 한꺼번에 올라가지 않게: 게시 시각에서 6시간 넘게 지난 편은 자동으로 안 올린다.
    if now - datetime.datetime.fromisoformat(item["at"]) > datetime.timedelta(hours=6):
        print("  틱톡: 게시 시각이 6시간 넘게 지나 자동 게시 안 함")
        return
    q = ("mutation($input: CreatePostInput!){ createPost(input:$input){ "
         "... on PostActionSuccess { post { id status } } ... on MutationError { message } } }")
    inp = {"channelId": os.environ["BUFFER_TIKTOK_CHANNEL_ID"], "text": item["ig"]["caption"],
           "schedulingType": "automatic", "mode": "shareNow",
           "metadata": {"tiktok": {"isAiGenerated": True}},
           "assets": [{"video": {"url": BASE_URL + item["video"]}}]}
    req = urllib.request.Request("https://api.buffer.com", method="POST",
                                 data=json.dumps({"query": q, "variables": {"input": inp}}).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + os.environ["BUFFER_API_KEY"]})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.load(r).get("data", {}).get("createPost") or {}
    if "post" not in res:
        raise RuntimeError(f"buffer {res.get('message') or res}")
    rec["tt_id"] = "buffer:" + res["post"]["id"]
    save_state()
    print("  틱톡(Buffer) 게시", rec["tt_id"], res["post"].get("status"))


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
        for step in (post_instagram, post_threads, post_tiktok):
            try:
                step(item, rec)
            except Exception as e:  # 한 플랫폼이 실패해도 다른 쪽은 올린다
                failed = True
                rec.setdefault("errors", []).append(f"{now.isoformat()} {step.__name__}: {e}"[:600])
                save_state()
                print("  실패", step.__name__, str(e)[:300])
        need = ("ig_id", "ig_comment", "th_id", "th_reply") + (("tt_id",) if item["lang"] == "ko" else ())
        if all(k in rec for k in need):
            rec["done"] = datetime.datetime.now(KST).isoformat(timespec="seconds")
            save_state()
    # 공개 영상 사본은 다 올린 뒤 6시간 지나면 지운다(Buffer가 나중에 받아 갈 수 있어서 바로 안 지움)
    for item in items:
        done = STATE.get(item["key"], {}).get("done")
        video = ROOT / item["video"]
        if done and video.exists() and now - datetime.datetime.fromisoformat(done) > datetime.timedelta(hours=6):
            video.unlink()
    if not only and not dry:
        write_status(items, now)
    # 올리기 실패는 status.json → 아침 9시 보고 메일로만 알린다(2026-11-04 사용자: 15분마다 실패 메일 쌓이지 않게).
    # 실패한 단계는 다음 실행이 이어서 하므로 예약 실행은 성공으로 끝낸다. 손으로 --only 돌릴 때만 실패 코드.
    sys.exit(1 if failed and only else 0)


def buffer_status(post_id):
    """Buffer 예약 글 상태(scheduled/sent/error 등). 열쇠 없거나 실패하면 None."""
    if not os.environ.get("BUFFER_API_KEY"):
        return None
    q = 'query($id: PostId!){ post(input:{id:$id}){ status } }'
    req = urllib.request.Request("https://api.buffer.com", method="POST",
                                 data=json.dumps({"query": q, "variables": {"id": post_id}}).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + os.environ["BUFFER_API_KEY"]})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return (json.load(r).get("data", {}).get("post") or {}).get("status")
    except Exception:
        return None


def write_status(items, now):
    """status.json: 아침 9시 보고(D:\\daily-report)가 읽는 점검표. 시간이 지난 편만 판정한다."""
    rows = []
    for item in items:
        rec = STATE.get(item["key"], {})
        due = datetime.datetime.fromisoformat(item["at"]) <= now
        row = {"key": item["key"], "at": item["at"], "due": due,
               "ig": "ok" if "ig_id" in rec else ("missing" if due else "scheduled"),
               "threads": "ok" if "th_id" in rec else ("missing" if due else "scheduled")}
        if item["lang"] == "ko":
            tt = rec.get("tt_id", "")
            st = rec.get("tt_status")
            # Buffer 요청 한도(429, 11-05) 때문에: 게시 확인(sent)된 편은 다시 묻지 않고,
            # 아직 확인 안 된 편도 1시간에 한 번만 묻는다. 결과는 posted.json에 남긴다.
            checked = rec.get("tt_checked")
            fresh = checked and now - datetime.datetime.fromisoformat(checked) < datetime.timedelta(hours=1)
            if tt.startswith("buffer:") and due and st != "sent" and not fresh:
                new = buffer_status(tt.split(":", 1)[1].split()[0])
                rec["tt_checked"] = now.isoformat(timespec="seconds")
                if new:
                    rec["tt_status"] = st = new
                save_state()
            row["tiktok"] = ("ok" if st == "sent" else st) if st else ("scheduled" if tt else ("missing" if due else "none"))
        if rec.get("errors"):
            row["last_error"] = rec["errors"][-1][:200]
        rows.append(row)
    path = ROOT / "status.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if old.get("items") == rows:  # 바뀐 게 없으면 안 써서 커밋(=페이지 다시 만들기)을 줄인다
        return
    path.write_text(json.dumps({"updated": now.isoformat(timespec="seconds"), "items": rows},
                               ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
