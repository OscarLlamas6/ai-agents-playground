"""Elemento 2 del agente: LAS HERRAMIENTAS.

Una herramienta es una función de Python con tres cosas que el modelo LEE:
    · su nombre        (el de la función)
    · su descripción   (el docstring)
    · sus parámetros   (los tipos de la firma)
Es todo lo que el modelo sabe de ella. Por eso un docstring vago produce un agente que elige mal.

El modelo nunca ejecuta estas funciones: solo PIDE que se ejecuten. Las ejecuta el código del agente.
Los datos son simulados: no hay ninguna infraestructura real detrás.
"""

from langchain.tools import tool

SERVICES = {
    "correo": "operativo",
    "vpn": "degradado: latencia alta desde hace 20 minutos",
    "wiki": "caído: mantenimiento no programado",
}


@tool
def get_service_status(service: str) -> str:
    """Consulta el estado actual de UN servicio de TI. Servicios válidos: correo, vpn, wiki."""
    status = SERVICES.get(service.strip().lower())
    if status is None:
        # El error es un texto legible: el modelo lo lee y puede corregirse.
        return f"Servicio desconocido: '{service}'. Servicios válidos: {', '.join(SERVICES)}."
    return f"{service}: {status}"


@tool
def uptime_percent(downtime_minutes: int, days: int = 30) -> float:
    """Calcula el porcentaje de disponibilidad de un servicio, dados los minutos que estuvo caído en un periodo de `days` días."""
    total_minutes = days * 24 * 60
    return round(100 * (1 - downtime_minutes / total_minutes), 3)
