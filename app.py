import os
from flask import Flask, Response, jsonify
from datetime import datetime
from rss_generator import generate_rss_feed

app = Flask(__name__)

@app.route("/healthz")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat() + "Z"})

@app.route("/rss.xml")
@app.route("/feed")
def feed():
    rss_xml = generate_rss_feed()
    return Response(rss_xml, mimetype="application/rss+xml")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
