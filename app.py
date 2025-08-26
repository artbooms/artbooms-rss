import os
from flask import Flask, Response, request, jsonify, make_response
from datetime import datetime
from article_processor import generate_items, load_cache
from rss_generator import build_rss


app = Flask(__name__)


@app.get("/healthz")
def healthz():
return jsonify({"ok": True, "time": datetime.utcnow().isoformat() + "Z"})


@app.get("/")
def root():
return jsonify({
"service": "artbooms-rss",
"endpoints": ["/rss.xml", "/feed", "/healthz"],
})


@app.get("/feed")
@app.get("/rss.xml")
def feed():
force = request.args.get("force") == "1"
items, meta = generate_items(force=force)
xml_bytes, headers = build_rss(items, meta)


resp = make_response(xml_bytes)
resp.headers["Content-Type"] = "application/rss+xml; charset=utf-8"
# Intestazioni HTTP per i crawler
if headers.get("ETag"): resp.headers["ETag"] = headers["ETag"]
if headers.get("Last-Modified"): resp.headers["Last-Modified"] = headers["Last-Modified"]
if headers.get("Cache-Control"): resp.headers["Cache-Control"] = headers["Cache-Control"]
return resp


if __name__ == "__main__":
port = int(os.environ.get("PORT", "5000"))
app.run(host="0.0.0.0", port=port)
