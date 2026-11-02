"""틱톡 임시 보관함(초안) 업로드. 앱 심사 전이라 바로 게시는 안 되고, 틱톡 앱 알림에서 사용자가 글을 붙여 게시한다.

토큰: 환경 변수 TIKTOK_CLIENT_KEY·TIKTOK_CLIENT_SECRET·TIKTOK_REFRESH_TOKEN (access는 매번 새로 받음, 24시간짜리)
refresh 토큰이 바뀌면 tiktok_token.enc(공개 저장소, TIKTOK_TOKEN_KEY로 암호화)에 새 값을 남긴다.
"""
import json, os, math, pathlib, urllib.parse, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent
ENC = ROOT / "tiktok_token.enc"
API = "https://open.tiktokapis.com/v2"


def _req(method, url, data=None, headers=None, raw=None):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            t = r.read()
            return json.loads(t) if t else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"tiktok {e.code} {url.split('?')[0]} {e.read().decode(errors='replace')[:300]}")


def _fernet():
    key = os.environ.get("TIKTOK_TOKEN_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


def refresh_token():
    f = _fernet()
    if f and ENC.exists():
        return f.decrypt(ENC.read_bytes()).decode()
    return os.environ["TIKTOK_REFRESH_TOKEN"]


def access_token():
    rt = refresh_token()
    params = urllib.parse.urlencode({"client_key": os.environ["TIKTOK_CLIENT_KEY"],
                                     "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
                                     "grant_type": "refresh_token", "refresh_token": rt}).encode()
    r = _req("POST", "https://open.tiktokapis.com/v2/oauth/token/", raw=params,
             headers={"Content-Type": "application/x-www-form-urlencoded"})
    if "access_token" not in r:
        raise RuntimeError(f"tiktok 토큰 갱신 실패 {r.get('error')} {r.get('error_description')}")
    f = _fernet()
    if f and r.get("refresh_token") and r["refresh_token"] != rt:
        ENC.write_bytes(f.encrypt(r["refresh_token"].encode()))
    return r["access_token"]


def upload_draft(video_path):
    """영상 파일을 틱톡 임시 보관함으로 보낸다. publish_id 반환."""
    tok = access_token()
    size = pathlib.Path(video_path).stat().st_size
    chunk = size if size <= 64 * 1024 * 1024 else 10 * 1024 * 1024
    count = math.ceil(size / chunk) if size > chunk else 1
    init = _req("POST", f"{API}/post/publish/inbox/video/init/",
                {"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk if count > 1 else size,
                                 "total_chunk_count": count}},
                {"Authorization": "Bearer " + tok, "Content-Type": "application/json; charset=UTF-8"})
    if init.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"tiktok init {init['error']}")
    url, pid = init["data"]["upload_url"], init["data"]["publish_id"]
    data = pathlib.Path(video_path).read_bytes()
    for i in range(count):
        start = i * chunk
        end = size - 1 if i == count - 1 else start + chunk - 1
        _req("PUT", url, raw=data[start:end + 1],
             headers={"Content-Type": "video/mp4", "Content-Length": str(end - start + 1),
                      "Content-Range": f"bytes {start}-{end}/{size}"})
    return pid, tok


def status(pid, tok):
    r = _req("POST", f"{API}/post/publish/status/fetch/", {"publish_id": pid},
             {"Authorization": "Bearer " + tok, "Content-Type": "application/json; charset=UTF-8"})
    return r.get("data", {})


if __name__ == "__main__":  # 시험: python tiktok.py ../.secrets/tiktok.env 영상.mp4
    import sys, time
    for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    pid, tok = upload_draft(sys.argv[2])
    print("publish_id", pid)
    for _ in range(12):
        time.sleep(10)
        st = status(pid, tok)
        print(st.get("status"), st.get("fail_reason", ""))
        if st.get("status") in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE", "FAILED"):
            break
