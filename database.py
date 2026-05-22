"""
Couche d'accès MySQL (XAMPP) : mesures, prédictions, log SMS.
Requiert : pip install mysql-connector-python
"""
from contextlib import contextmanager
from typing import Optional

import mysql.connector

from config import MYSQL_CONFIG


# ── Connexion ─────────────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cursor = conn.cursor(dictionary=True)
    try:
        yield conn, cursor
        conn.commit()
    finally:
        cursor.close()
        conn.close()


# ── Initialisation du schéma ──────────────────────────────────────────────────

def init_schema():
    with get_conn() as (conn, cur):
        cur.execute("""
        CREATE TABLE IF NOT EXISTS mesures (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            station      VARCHAR(60) NOT NULL,
            date         DATE        NOT NULL,
            Q            FLOAT,
            precip_mm    FLOAT,
            t2m_mean     FLOAT,
            t2m_max      FLOAT,
            t2m_min      FLOAT,
            rh2m_pct     FLOAT,
            pression_hpa FLOAT,
            sm_surface   FLOAT,
            sm_root      FLOAT,
            source       VARCHAR(20) DEFAULT 'openmeteo',
            UNIQUE KEY uq_station_date (station, date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            station      VARCHAR(60) NOT NULL,
            run_date     DATE        NOT NULL,
            Q_predit_j1  FLOAT,
            Q_predit_j3  FLOAT,
            niveau_j1    TINYINT,
            niveau_j3    TINYINT,
            UNIQUE KEY uq_station_rundate (station, run_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS sms_log (
            id        INT AUTO_INCREMENT PRIMARY KEY,
            station   VARCHAR(60)  NOT NULL,
            run_date  DATE         NOT NULL,
            niveau    TINYINT      NOT NULL,
            message   TEXT         NOT NULL,
            sid       VARCHAR(64),
            statut    VARCHAR(20)  DEFAULT 'sent',
            ts        DATETIME     DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)


# ── Insertions ─────────────────────────────────────────────────────────────────

def upsert_mesure(station: str, date_str: str, row: dict):
    sql = """
        INSERT INTO mesures
            (station, date, Q, precip_mm, t2m_mean, t2m_max, t2m_min,
             rh2m_pct, pression_hpa, sm_surface, sm_root)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            Q=VALUES(Q), precip_mm=VALUES(precip_mm),
            t2m_mean=VALUES(t2m_mean), t2m_max=VALUES(t2m_max),
            t2m_min=VALUES(t2m_min), rh2m_pct=VALUES(rh2m_pct),
            pression_hpa=VALUES(pression_hpa),
            sm_surface=VALUES(sm_surface), sm_root=VALUES(sm_root)
    """
    values = (
        station, date_str,
        row.get("Q"), row.get("precip_mm"), row.get("t2m_mean"),
        row.get("t2m_max"), row.get("t2m_min"), row.get("rh2m_pct"),
        row.get("pression_hpa"), row.get("sm_surface"), row.get("sm_root"),
    )
    with get_conn() as (conn, cur):
        cur.execute(sql, values)


def upsert_prediction(station: str, run_date: str,
                      q_j1: float, q_j3: float,
                      niveau_j1: int, niveau_j3: int):
    sql = """
        INSERT INTO predictions
            (station, run_date, Q_predit_j1, Q_predit_j3, niveau_j1, niveau_j3)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            Q_predit_j1=VALUES(Q_predit_j1), Q_predit_j3=VALUES(Q_predit_j3),
            niveau_j1=VALUES(niveau_j1), niveau_j3=VALUES(niveau_j3)
    """
    with get_conn() as (conn, cur):
        cur.execute(sql, (station, run_date, q_j1, q_j3, niveau_j1, niveau_j3))


def log_sms(station: str, run_date: str, niveau: int,
            message: str, sid: Optional[str] = None, statut: str = "sent"):
    sql = """
        INSERT INTO sms_log (station, run_date, niveau, message, sid, statut)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    with get_conn() as (conn, cur):
        cur.execute(sql, (station, run_date, niveau, message, sid, statut))


# ── Lectures ───────────────────────────────────────────────────────────────────

def get_mesures(station: str, n_days: int = 95) -> list[dict]:
    with get_conn() as (conn, cur):
        cur.execute("""
            SELECT * FROM mesures
            WHERE station = %s
            ORDER BY date DESC
            LIMIT %s
        """, (station, n_days))
        rows = cur.fetchall()
    return list(reversed(rows))


def get_last_prediction(station: str) -> Optional[dict]:
    with get_conn() as (conn, cur):
        cur.execute("""
            SELECT * FROM predictions
            WHERE station = %s
            ORDER BY run_date DESC
            LIMIT 1
        """, (station,))
        row = cur.fetchone()
    return row


def get_predictions_history(station: str, n: int = 30) -> list[dict]:
    with get_conn() as (conn, cur):
        cur.execute("""
            SELECT * FROM predictions
            WHERE station = %s
            ORDER BY run_date DESC
            LIMIT %s
        """, (station, n))
        rows = cur.fetchall()
    return list(reversed(rows))


def get_sms_log(n: int = 50) -> list[dict]:
    with get_conn() as (conn, cur):
        cur.execute("""
            SELECT * FROM sms_log
            ORDER BY ts DESC
            LIMIT %s
        """, (n,))
        return cur.fetchall()


def get_previous_niveau(station: str, before_date: str) -> Optional[int]:
    with get_conn() as (conn, cur):
        cur.execute("""
            SELECT niveau_j1 FROM predictions
            WHERE station = %s AND run_date < %s
            ORDER BY run_date DESC
            LIMIT 1
        """, (station, before_date))
        row = cur.fetchone()
    return row["niveau_j1"] if row else None


def count_mesures(station: str) -> int:
    with get_conn() as (conn, cur):
        cur.execute(
            "SELECT COUNT(*) AS n FROM mesures WHERE station = %s", (station,)
        )
        return cur.fetchone()["n"]
