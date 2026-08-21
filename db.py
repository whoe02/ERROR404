import os

import pymysql
import pymysql.cursors


def get_connection():
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def log_event(level: str, path: str, message: str, traceback_text: str | None = None) -> None:
    """Best-effort write to app_log — never let logging itself crash a request."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO app_log (level, path, message, traceback) VALUES (%s,%s,%s,%s)",
                    (level, path, message[:60000], (traceback_text or "")[:60000]),
                )
        finally:
            conn.close()
    except Exception:
        pass
