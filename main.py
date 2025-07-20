#!/usr/bin/env python3
"""
Firestore Cleanup Cloud Run Service

Performs periodic maintenance on Firestore "Spaces":
  1. Remove inactive user documents from each space's 'activeUsers' subcollection
     where lastUpdate < now - ACTIVE_USER_MINUTES.
  2. Recalculate and update 'currentUserCount' on the parent space document.
  3. Remove old message documents from each space's 'messages' subcollection
     where timestamp < now - MESSAGE_TTL_HOURS.

Features:
  - Configurable thresholds via environment variables.
  - Batch deletion loop (handles >500 deletes).
  - Optional simple lock to avoid overlapping executions.
  - Aggregation count (Firestore) with fallback to streaming count.
  - Structured JSON logging for Cloud Logging.
"""

import os
import json
import time
import logging
import datetime
from flask import Flask, jsonify, request
from google.cloud import firestore

# ---------------------------------------------------------------------------
# Configuration (Environment Variables)
# ---------------------------------------------------------------------------
DATABASE_ID          = os.getenv("DATABASE_ID", "(default)")
ACTIVE_USER_MINUTES  = int(os.getenv("ACTIVE_USER_MINUTES", "10"))
MESSAGE_TTL_HOURS    = int(os.getenv("MESSAGE_TTL_HOURS", "24"))
BATCH_SIZE           = int(os.getenv("BATCH_SIZE", "450"))   # < 500 Firestore batch limit
LOCK_TTL_SECONDS     = int(os.getenv("LOCK_TTL_SECONDS", "600"))
LOCK_COLLECTION      = os.getenv("LOCK_COLLECTION", "MaintenanceLocks")
LOCK_DOC_ID          = os.getenv("LOCK_DOC_ID", "firestore-cleanup-lock")
LOG_LEVEL            = os.getenv("LOG_LEVEL", "INFO").upper()

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(level=LOG_LEVEL,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def log_event(event: str, **kwargs):
    """Emit a JSON-structured log line."""
    payload = {"event": event, **kwargs}
    logger.info(json.dumps(payload))


# ---------------------------------------------------------------------------
# Firestore Client
# ---------------------------------------------------------------------------
# NOTE: Use google.cloud.firestore Client to support multi-database (DATABASE_ID != "(default)")
db = firestore.Client(database=DATABASE_ID)


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------
def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def acquire_lock() -> bool:
    """
    Best-effort lock: create/update a document with an expiry timestamp.
    Returns True if lock acquired, False if an unexpired lock exists.
    """
    lock_ref = db.collection(LOCK_COLLECTION).document(LOCK_DOC_ID)

    @firestore.transactional
    def txn_attempt(transaction):
        snap = lock_ref.get(transaction=transaction)
        now = utcnow()
        if snap.exists:
            expires_at = snap.get("expiresAt")
            if isinstance(expires_at, datetime.datetime) and expires_at > now:
                # Lock still valid; do not acquire
                return False
        transaction.set(lock_ref, {
            "holder": "cleanup-service",
            "startedAt": now,
            "expiresAt": now + datetime.timedelta(seconds=LOCK_TTL_SECONDS)
        })
        return True

    transaction = db.transaction()
    acquired = txn_attempt(transaction)
    if acquired:
        log_event("lock_acquired", lock=LOCK_DOC_ID)
    else:
        log_event("lock_busy", lock=LOCK_DOC_ID)
    return acquired


def release_lock():
    lock_ref = db.collection(LOCK_COLLECTION).document(LOCK_DOC_ID)
    try:
        lock_ref.delete()
        log_event("lock_released", lock=LOCK_DOC_ID)
    except Exception as e:
        # Non-critical; just log
        log_event("lock_release_error", error=str(e))


def delete_query_in_batches(query, batch_size=BATCH_SIZE) -> int:
    """
    Iteratively delete documents returned by a query in batches.
    Returns the total number of documents deleted.
    """
    total_deleted = 0
    while True:
        # Limit each round to batch_size docs to stay under Firestore 500 limit
        docs = list(query.limit(batch_size).stream())
        if not docs:
            break
        batch = db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        total_deleted += len(docs)
    return total_deleted


def count_collection(coll_ref) -> int:
    """
    Attempt an aggregation count; fallback to streaming count on older libs.
    """
    try:
        # Firestore aggregation count (requires recent google-cloud-firestore)
        agg_query = coll_ref.count()
        agg_result = agg_query.get()
        # Result is a list of aggregation snapshots; use the first
        return agg_result[0].value
    except Exception:
        # Fallback: stream all docs and count (higher cost)
        return sum(1 for _ in coll_ref.stream())


# ---------------------------------------------------------------------------
# Flask Application
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/healthz", methods=["GET"])
def healthz():
    """Simple health check endpoint."""
    return jsonify({"status": "ok", "database": DATABASE_ID}), 200


@app.route("/", methods=["GET", "POST"])
def run_cleanup():
    """
    Main cleanup endpoint.
    Can be invoked by Cloud Scheduler (POST) with OIDC auth or manually.
    """
    request_id = request.headers.get("X-Cloud-Trace-Context", "manual")
    start_time = time.time()
    now = utcnow()

    # Acquire lock (avoid overlapping runs)
    if not acquire_lock():
        return jsonify({
            "status": "skipped",
            "reason": "lock_active",
            "request_id": request_id
        }), 200

    active_threshold = now - datetime.timedelta(minutes=ACTIVE_USER_MINUTES)
    message_threshold = now - datetime.timedelta(hours=MESSAGE_TTL_HOURS)

    stats = {
        "spaces_scanned": 0,
        "active_users_deleted": 0,
        "messages_deleted": 0,
        "spaces_with_user_deletions": 0,
        "spaces_with_message_deletions": 0
    }

    log_event("cleanup_start",
              request_id=request_id,
              database=DATABASE_ID,
              active_threshold=active_threshold.isoformat(),
              message_threshold=message_threshold.isoformat())

    try:
        spaces_ref = db.collection("Spaces")

        # (Optional optimization: filter recent spaces if you maintain a lastActivity field)
        for space_snap in spaces_ref.stream():
            space_id = space_snap.id
            stats["spaces_scanned"] += 1

            # --- Active Users Cleanup ---
            active_users_ref = space_snap.reference.collection("activeUsers")
            old_users_query = active_users_ref.where("lastUpdate", "<", active_threshold)
            deleted_users = delete_query_in_batches(old_users_query)
            stats["active_users_deleted"] += deleted_users
            if deleted_users:
                stats["spaces_with_user_deletions"] += 1

            # Recount after deletions
            remaining_count = count_collection(active_users_ref)
            space_snap.reference.update({"currentUserCount": remaining_count})

            # --- Messages Cleanup ---
            messages_ref = space_snap.reference.collection("messages")
            old_messages_query = messages_ref.where("timestamp", "<", message_threshold)
            deleted_msgs = delete_query_in_batches(old_messages_query)
            stats["messages_deleted"] += deleted_msgs
            if deleted_msgs:
                stats["spaces_with_message_deletions"] += 1

            log_event("space_processed",
                      space_id=space_id,
                      deleted_active_users=deleted_users,
                      remaining_active_users=remaining_count,
                      deleted_messages=deleted_msgs)

    except Exception as e:
        log_event("cleanup_error", error=str(e), stats=stats)
        release_lock()
        return jsonify({
            "status": "error",
            "error": str(e),
            "stats": stats,
            "request_id": request_id
        }), 500
    else:
        release_lock()
        duration = round(time.time() - start_time, 2)
        stats["duration_seconds"] = duration
        log_event("cleanup_complete", **stats)
        return jsonify({
            "status": "ok",
            "stats": stats,
            "request_id": request_id
        }), 200


# ---------------------------------------------------------------------------
# Local Dev Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # For local testing only (Cloud Run will use Gunicorn command)
    port = int(os.getenv("PORT", "8080"))
    log_event("dev_server_start", port=port, database=DATABASE_ID)
    app.run(host="0.0.0.0", port=port, debug=True)
