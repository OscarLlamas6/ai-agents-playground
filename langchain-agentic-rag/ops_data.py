"""Datos y reglas de la mesa de ayuda que el MODELO no debe calcular de cabeza: tickets, equipos y plazos (SLA).

Son funciones puras sobre archivos JSON ficticios (data/). Las herramientas de tools.py solo las envuelven.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

DATA = Path(__file__).parent / "data"
SLA_HOURS = {"crítica": 2, "alta": 4, "media": 8, "baja": 24}     # el mismo plazo que dice el manual
WORK_START, WORK_END = 8, 17                                       # lunes a viernes, 8:00 a 17:00


def _load(name: str) -> list[dict]:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def find_ticket(ticket_id: str) -> dict | None:
    return next((t for t in _load("tickets.json") if t["id"].lower() == ticket_id.strip().lower()), None)


def find_asset(hostname: str) -> dict | None:
    return next((a for a in _load("assets.json") if a["hostname"].lower() == hostname.strip().lower()), None)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):                                 # 31 de enero + 1 mes = 28 o 29 de febrero
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError("fecha inválida")


def _next_workday_start(t: datetime) -> datetime:
    t = t.replace(hour=WORK_START, minute=0, second=0, microsecond=0) + timedelta(days=1)
    while t.weekday() >= 5:
        t += timedelta(days=1)
    return t


def add_business_hours(start: datetime, hours: float) -> datetime:
    """Suma horas hábiles (lun–vie, 8:00–17:00): fuera de horario el reloj no avanza."""
    t = start
    if t.weekday() >= 5 or t.hour >= WORK_END:
        t = _next_workday_start(t) if t.weekday() < 5 or t.hour >= WORK_END else t
        while t.weekday() >= 5:
            t = _next_workday_start(t)
    elif t.hour < WORK_START:
        t = t.replace(hour=WORK_START, minute=0, second=0, microsecond=0)
    remaining = timedelta(hours=hours)
    while True:
        day_end = t.replace(hour=WORK_END, minute=0, second=0, microsecond=0)
        available = day_end - t
        if remaining <= available:
            return t + remaining
        remaining -= available
        t = _next_workday_start(t)
