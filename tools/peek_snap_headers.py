import urllib.request

for cid in (1, 13):
    url = f"http://127.0.0.1:8000/api/cameras/{cid}/snapshot.jpg"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            age = r.headers.get("X-Frame-Age-Ms", "?")
            print(cid, "ok", len(r.read()), "age_ms", age)
    except Exception as e:
        print(cid, "err", e)
