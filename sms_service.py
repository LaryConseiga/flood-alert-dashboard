"""
Envoi des alertes SMS via Twilio (messages en français).
Déclenché uniquement si : niveau >= Alerte (2) ET changement par rapport à J-1.
"""
from datetime import datetime
from typing import Optional

from config import ALERT_LEVELS, ALERT_ACTIONS, STATIONS


# ── Formatage du message ───────────────────────────────────────────────────────

def _format_message(station_name: str, run_date: str,
                    q_actuel: float, q_j1: float, q_j3: float,
                    niveau_j1: int, niveau_precedent: Optional[int]) -> str:
    basin    = STATIONS[station_name]["basin"].upper()
    al       = ALERT_LEVELS[niveau_j1]
    label    = al["label"].upper()
    emoji    = al["emoji"]
    action   = ALERT_ACTIONS[niveau_j1]

    # Indicateur de tendance
    if niveau_precedent is None:
        tendance = ""
    elif niveau_j1 > niveau_precedent:
        tendance = " ⬆"
    elif niveau_j1 < niveau_precedent:
        tendance = " ⬇"
    else:
        tendance = ""

    prev_label = (
        ALERT_LEVELS[niveau_precedent]["label"] if niveau_precedent is not None else "—"
    )

    lines = [
        f"[ALERTE PRÉCOCE — {basin}]",
        f"Station  : {station_name}",
        f"Date     : {run_date}",
        f"Niveau   : {emoji} {label}{tendance} (était {prev_label})",
        f"Q actuel : {q_actuel:,.0f} m³/s",
        f"Q prévu J+1 : {q_j1:,.0f} m³/s",
        f"Q prévu J+3 : {q_j3:,.0f} m³/s",
        f"Action   : {action}",
    ]
    return "\n".join(lines)


# ── Envoi Twilio ───────────────────────────────────────────────────────────────

def send_alert(station_name: str, run_date: str,
               q_actuel: float, q_j1: float, q_j3: float,
               niveau_j1: int, niveau_precedent: Optional[int],
               account_sid: str, auth_token: str,
               from_number: str, to_number: str) -> dict:
    """
    Envoie un SMS d'alerte si les conditions sont réunies.

    Retourne un dict :
        sent    : bool — True si SMS expédié
        reason  : str  — raison du non-envoi si sent=False
        sid     : str  — Twilio message SID (si envoyé)
        message : str  — texte complet
    """
    # ── Conditions d'envoi ────────────────────────────────────────────────────
    if niveau_j1 < 2:
        return {"sent": False, "reason": "niveau < Alerte", "sid": None, "message": ""}

    if niveau_precedent is not None and niveau_j1 == niveau_precedent:
        return {"sent": False, "reason": "niveau inchangé", "sid": None, "message": ""}

    message = _format_message(
        station_name, run_date, q_actuel, q_j1, q_j3,
        niveau_j1, niveau_precedent,
    )

    # ── Appel Twilio ──────────────────────────────────────────────────────────
    try:
        from twilio.rest import Client  # import tardif : évite crash si non installé
        client = Client(account_sid, auth_token)
        msg = client.messages.create(
            body=message,
            from_=from_number,
            to=to_number,
        )
        return {"sent": True, "reason": "ok", "sid": msg.sid, "message": message}

    except ImportError:
        return {
            "sent": False,
            "reason": "twilio non installé (pip install twilio)",
            "sid": None,
            "message": message,
        }
    except Exception as exc:
        return {
            "sent": False,
            "reason": str(exc),
            "sid": None,
            "message": message,
        }


# ── Orchestration pour toutes les stations ────────────────────────────────────

def send_alerts_all(predictions: dict, q_actuels: dict,
                    previous_niveaux: dict,
                    account_sid: str, auth_token: str,
                    from_number: str, to_number: str,
                    run_date: str) -> list[dict]:
    """
    Parcourt toutes les stations et envoie les SMS nécessaires.

    predictions      : {station: {"q_j1": .., "q_j3": .., "niveau_j1": ..}}
    q_actuels        : {station: float}  — Q observé du jour
    previous_niveaux : {station: int|None} — niveau de J-1
    """
    results = []
    for station, pred in predictions.items():
        if "error" in pred:
            continue
        result = send_alert(
            station_name=station,
            run_date=run_date,
            q_actuel=q_actuels.get(station, 0.0),
            q_j1=pred["q_j1"],
            q_j3=pred["q_j3"],
            niveau_j1=pred["niveau_j1"],
            niveau_precedent=previous_niveaux.get(station),
            account_sid=account_sid,
            auth_token=auth_token,
            from_number=from_number,
            to_number=to_number,
        )
        result["station"] = station
        results.append(result)
    return results
