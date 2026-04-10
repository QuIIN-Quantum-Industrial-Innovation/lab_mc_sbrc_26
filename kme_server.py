"""
ETSI GS QKD 014 V1.1.1 — Flask KME Server
HTTPS with mutual TLS (mTLS) — no passwords, no pre-shared secrets.
Keys are generated with Python random and stored in SQLite.

Endpoints:
  GET  /api/v1/keys/<slave_SAE_ID>/status
  POST /api/v1/keys/<slave_SAE_ID>/enc_keys   (master SAE — get new keys)
  GET  /api/v1/keys/<slave_SAE_ID>/enc_keys   (master SAE — simple GET form)
  POST /api/v1/keys/<master_SAE_ID>/dec_keys  (slave SAE — retrieve by key ID)
  GET  /api/v1/keys/<master_SAE_ID>/dec_keys  (slave SAE — single key_ID in URL)
"""

import random
import base64
import uuid
import sqlite3
import ssl
import logging
import os

import werkzeug.serving
from flask import Flask, request, jsonify

# ── Configuration ─────────────────────────────────────────────────────────────
CERT_DIR       = "/opt/kme/certs"
KME_CERT       = os.path.join(CERT_DIR, "kme-server.crt")
KME_KEY        = os.path.join(CERT_DIR, "kme-server.key")
CA_CERT        = os.path.join(CERT_DIR, "ca.crt")

DB_PATH        = "/opt/kme/kme.db"
KME_ID         = "kme1"
LISTEN_PORT    = 8020

DEFAULT_KEY_SIZE_BITS  = 256
MAX_KEY_SIZE_BITS      = 1024
MIN_KEY_SIZE_BITS      = 64
MAX_KEY_COUNT          = 100_000
MAX_KEYS_PER_REQUEST   = 128
LOG_PATH       = "/var/log/kme.log"

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("kme")

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)


# ── mTLS: custom Werkzeug request handler ─────────────────────────────────────
# Extracts the client certificate CN from the TLS handshake and makes it
# available to route handlers via request.environ['SSL_CLIENT_CN'].
class MtlsRequestHandler(werkzeug.serving.WSGIRequestHandler):
    def make_environ(self):
        environ = super().make_environ()
        try:
            peer_cert = self.connection.getpeercert()
            for rdn in peer_cert.get("subject", []):
                for attr, val in rdn:
                    if attr == "commonName":
                        environ["SSL_CLIENT_CN"] = val
                        break
        except Exception:
            environ["SSL_CLIENT_CN"] = "unknown"
        return environ


# ── Database helpers ──────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                key_id           TEXT PRIMARY KEY,
                master_sae_id    TEXT NOT NULL,
                slave_sae_id     TEXT NOT NULL,
                key_data         TEXT NOT NULL,
                key_size_bits    INTEGER NOT NULL,
                delivered_master INTEGER DEFAULT 0,
                delivered_slave  INTEGER DEFAULT 0
            )
        """)
        conn.commit()
    log.info("Database initialised at %s", DB_PATH)


# ── Key generation ────────────────────────────────────────────────────────────
def generate_key(size_bits: int) -> str:
    """Return a base64-encoded random key of size_bits bits."""
    raw = bytes(random.getrandbits(8) for _ in range(size_bits // 8))
    return base64.b64encode(raw).decode()


def generate_key_id() -> str:
    return str(uuid.uuid4())


def count_stored_keys(master_sae_id: str, slave_sae_id: str) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM keys "
            "WHERE master_sae_id=? AND slave_sae_id=? "
            "AND delivered_master=0 AND delivered_slave=0",
            (master_sae_id, slave_sae_id),
        ).fetchone()
    return row[0]


# ── Route: GET status ─────────────────────────────────────────────────────────
@app.route("/api/v1/keys/<slave_sae_id>/status", methods=["GET"])
def get_status(slave_sae_id):
    master_sae_id = request.environ.get("SSL_CLIENT_CN", "unknown")
    stored = count_stored_keys(master_sae_id, slave_sae_id)
    log.info("GET status: master=%s slave=%s stored_keys=%d",
             master_sae_id, slave_sae_id, stored)
    return jsonify({
        "source_KME_ID":      KME_ID,
        "target_KME_ID":      KME_ID,
        "master_SAE_ID":      master_sae_id,
        "slave_SAE_ID":       slave_sae_id,
        "key_size":           DEFAULT_KEY_SIZE_BITS,
        "stored_key_count":   stored,
        "max_key_count":      MAX_KEY_COUNT,
        "max_key_per_request": MAX_KEYS_PER_REQUEST,
        "max_key_size":       MAX_KEY_SIZE_BITS,
        "min_key_size":       MIN_KEY_SIZE_BITS,
        "max_SAE_ID_count":   0,
    })


# ── Route: enc_keys (master SAE requests new keys) ────────────────────────────
@app.route("/api/v1/keys/<slave_sae_id>/enc_keys", methods=["GET", "POST"])
def enc_keys(slave_sae_id):
    master_sae_id = request.environ.get("SSL_CLIENT_CN", "unknown")

    body = {}
    if request.method == "POST" and request.is_json:
        body = request.get_json(silent=True) or {}

    number   = int(request.args.get("number",  body.get("number",  1)))
    key_size = int(request.args.get("size",     body.get("size",    DEFAULT_KEY_SIZE_BITS)))

    if number < 1 or number > MAX_KEYS_PER_REQUEST:
        return jsonify({"message": f"number must be 1–{MAX_KEYS_PER_REQUEST}"}), 400
    if key_size < MIN_KEY_SIZE_BITS or key_size > MAX_KEY_SIZE_BITS:
        return jsonify({"message": f"size must be {MIN_KEY_SIZE_BITS}–{MAX_KEY_SIZE_BITS} bits"}), 400
    if key_size % 8 != 0:
        return jsonify({"message": "size shall be a multiple of 8"}), 400

    keys_out = []
    with get_db() as conn:
        for _ in range(number):
            kid  = generate_key_id()
            kdat = generate_key(key_size)
            conn.execute(
                "INSERT INTO keys "
                "(key_id, master_sae_id, slave_sae_id, key_data, key_size_bits, "
                "delivered_master, delivered_slave) "
                "VALUES (?, ?, ?, ?, ?, 1, 0)",
                (kid, master_sae_id, slave_sae_id, kdat, key_size),
            )
            keys_out.append({"key_ID": kid, "key": kdat})
        conn.commit()

    log.info("enc_keys: master=%s slave=%s count=%d size=%d bits",
             master_sae_id, slave_sae_id, number, key_size)
    for k in keys_out:
        log.info("  key delivered to master: %s", k["key_ID"])

    return jsonify({"keys": keys_out})


# ── Route: dec_keys (slave SAE retrieves keys by ID) ─────────────────────────
@app.route("/api/v1/keys/<master_sae_id>/dec_keys", methods=["GET", "POST"])
def dec_keys(master_sae_id):
    slave_sae_id = request.environ.get("SSL_CLIENT_CN", "unknown")

    if request.method == "POST" and request.is_json:
        body = request.get_json(silent=True) or {}
        requested_ids = [item["key_ID"] for item in body.get("key_IDs", [])]
    else:
        kid = request.args.get("key_ID")
        requested_ids = [kid] if kid else []

    if not requested_ids:
        return jsonify({"message": "no key_IDs supplied"}), 400

    keys_out = []
    missing  = []

    with get_db() as conn:
        for kid in requested_ids:
            row = conn.execute(
                "SELECT key_data FROM keys "
                "WHERE key_id=? AND master_sae_id=? AND delivered_slave=0",
                (kid, master_sae_id),
            ).fetchone()

            if row is None:
                missing.append(kid)
            else:
                keys_out.append({"key_ID": kid, "key": row["key_data"]})
                conn.execute(
                    "UPDATE keys SET delivered_slave=1 WHERE key_id=?", (kid,)
                )

        if missing:
            conn.rollback()
            log.warning("dec_keys: key(s) not found: %s", missing)
            return jsonify({"message": "one or more keys specified are not found on KME"}), 400

        conn.commit()

    log.info("dec_keys: slave=%s master=%s count=%d",
             slave_sae_id, master_sae_id, len(keys_out))
    for k in keys_out:
        log.info("  key delivered to slave:  %s", k["key_ID"])

    return jsonify({"keys": keys_out})


# ── TLS context ───────────────────────────────────────────────────────────────
def build_ssl_context():
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(certfile=KME_CERT, keyfile=KME_KEY)
    ctx.load_verify_locations(cafile=CA_CERT)
    ctx.verify_mode = ssl.CERT_REQUIRED          # enforce mutual TLS
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    log.info("KME server starting — KME_ID=%s  DB=%s  port=%d",
             KME_ID, DB_PATH, LISTEN_PORT)
    ssl_ctx = build_ssl_context()
    werkzeug.serving.run_simple(
        "0.0.0.0",
        LISTEN_PORT,
        app,
        ssl_context=ssl_ctx,
        request_handler=MtlsRequestHandler,
        use_reloader=False,
    )
