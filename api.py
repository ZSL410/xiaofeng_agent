#!/usr/bin/env python3
"""
晓风Agent API Server
Flask server listening on 127.0.0.1:5001 with token-based authentication.
"""

import functools

from flask import Flask, jsonify, request

app = Flask(__name__)

# ── Configuration ──
API_TOKEN = "xiaofeng2026"


# ── Authentication decorator ──
def require_token(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-API-Token")

        if not token:
            return jsonify({"error": "Missing X-API-Token header"}), 401

        if token != API_TOKEN:
            return jsonify({"error": "Invalid token"}), 401

        return f(*args, **kwargs)

    return decorated


# ── Routes ──
@app.route("/api/health")
@require_token
def health():
    return jsonify({"status": "ok"})


@app.route("/api/file/process", methods=["POST"])
@require_token
def file_process():
    data = request.get_json(silent=True)

    if not data or "paths" not in data:
        return jsonify({"error": "Missing 'paths' in request body"}), 400

    paths = data["paths"]
    if not isinstance(paths, list):
        return jsonify({"error": "'paths' must be a list"}), 400

    count = len(paths)
    return jsonify({"processed": count, "errors": 0, "details": []})


# ── Entry point ──
if __name__ == "__main__":
    print(" * 晓风Agent API Server starting on http://127.0.0.1:5001")
    print(f" * Token: {API_TOKEN}")
    print(" * Endpoints:")
    print("   GET /api/health       (headers: X-API-Token)")
    print("   POST /api/file/process  (headers: X-API-Token, body: {\"paths\": [...]})")
    app.run(host="127.0.0.1", port=5001, debug=False)
