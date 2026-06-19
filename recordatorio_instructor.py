#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recordatorio_instructor.py — Recordatorio automático por mail al instructor.

Corre desde GitHub Actions (cron). En cada ejecución:
  1. Lee /reservas e /instructores de Firebase (REST, reglas de lectura abiertas).
  2. Busca turnos APROBADOS con instructor asignado cuyo vuelo cae dentro de la
     ventana de aviso (REMIND_HOURS) y que todavía no fueron avisados.
  3. Resuelve el email del instructor (campo `email` en /instructores, matcheando
     por nombre con aprobado_por/instructor de la reserva).
  4. Envía el recordatorio vía EmailJS (API REST server-side, con private key).
  5. Marca la reserva con recordatorio_inst_enviado:true para no repetir.

Solo usa la librería estándar de Python (urllib, json, datetime). No requiere pip.

Configuración por variables de entorno (las setea el workflow):
  FIREBASE_DB_URL      URL de la Realtime DB (sin barra final)
  EMAILJS_SERVICE_ID   service_xxx de la cuenta que tiene el template
  EMAILJS_TEMPLATE_ID  template_8awr1zd
  EMAILJS_PUBLIC_KEY   public key (user_id) de esa cuenta
  EMAILJS_PRIVATE_KEY  private key (accessToken) — SECRET, viene de GitHub Secrets
  CLUB_EMAIL           mail del club (va como Reply-To, variable {{email}})
  REMIND_HOURS         ventana de aviso en horas (default 2)
  TZ_OFFSET            offset horario local respecto de UTC (default -3, Argentina)
  DRY_RUN              "1" para probar sin enviar ni marcar (default "0")
"""

import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# ── Configuración ──────────────────────────────────────────────
DB_URL       = os.environ.get("FIREBASE_DB_URL", "").rstrip("/")
SERVICE_ID   = os.environ.get("EMAILJS_SERVICE_ID", "")
TEMPLATE_ID  = os.environ.get("EMAILJS_TEMPLATE_ID", "")
PUBLIC_KEY   = os.environ.get("EMAILJS_PUBLIC_KEY", "")
PRIVATE_KEY  = os.environ.get("EMAILJS_PRIVATE_KEY", "")
CLUB_EMAIL   = os.environ.get("CLUB_EMAIL", "")
REMIND_HOURS = float(os.environ.get("REMIND_HOURS", "2"))
TZ_OFFSET    = float(os.environ.get("TZ_OFFSET", "-3"))
DRY_RUN      = os.environ.get("DRY_RUN", "0") == "1"

EMAILJS_URL = "https://api.emailjs.com/api/v1.0/email/send"
FROM_NAME   = "Aeroclub Río Grande"   # variable {{name}} del template


def faltan_config():
    faltan = [n for n, v in [
        ("FIREBASE_DB_URL", DB_URL), ("EMAILJS_SERVICE_ID", SERVICE_ID),
        ("EMAILJS_TEMPLATE_ID", TEMPLATE_ID), ("EMAILJS_PUBLIC_KEY", PUBLIC_KEY),
        ("EMAILJS_PRIVATE_KEY", PRIVATE_KEY), ("CLUB_EMAIL", CLUB_EMAIL),
    ] if not v]
    return faltan


def fb_get(path):
    url = "{}/{}.json".format(DB_URL, path)
    req = urllib.request.Request(url, headers={"User-Agent": "aeroclub-cron"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if raw and raw != "null" else None


def fb_marcar_enviado(key):
    """PATCH la reserva con la marca de recordatorio enviado (reglas de escritura abiertas)."""
    url = "{}/reservas/{}.json".format(DB_URL, key)
    body = json.dumps({
        "recordatorio_inst_enviado": True,
        "recordatorio_inst_ts": datetime.utcnow().isoformat() + "Z",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="PATCH",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "aeroclub-cron"})
    urllib.request.urlopen(req, timeout=30).read()


def enviar_mail(params):
    payload = {
        "service_id": SERVICE_ID,
        "template_id": TEMPLATE_ID,
        "user_id": PUBLIC_KEY,
        "accessToken": PRIVATE_KEY,
        "template_params": params,
    }
    req = urllib.request.Request(EMAILJS_URL, data=json.dumps(payload).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "aeroclub-cron"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "replace")


def construir_indice_instructores(instData):
    """nombre (normalizado) -> email. Solo instructores con email cargado."""
    idx = {}
    for k, v in (instData or {}).items():
        if not isinstance(v, dict):
            continue
        nombre = (v.get("nombre") or "").strip()
        email = (v.get("email") or "").strip()
        if nombre and email:
            idx[nombre.lower()] = {"nombre": nombre, "email": email}
    return idx


def hora_texto(r):
    if r.get("horaInicio") and r.get("horaFin"):
        return r["horaInicio"] + " a " + r["horaFin"]
    return r.get("hora", "")


def flight_dt_local(r):
    """datetime naive en hora local de la reserva."""
    y, m, d = map(int, r["fecha"].split("-"))
    hh, mm = map(int, (r.get("hora") or "00:00").split(":"))
    return datetime(y, m, d, hh, mm, 0)


def main():
    faltan = faltan_config()
    if faltan:
        print("ERROR: faltan variables de entorno:", ", ".join(faltan))
        sys.exit(1)

    ahora_local = datetime.utcnow() + timedelta(hours=TZ_OFFSET)
    print("== Recordatorio instructor ==")
    print("Ahora (local UTC{:+g}): {:%Y-%m-%d %H:%M}".format(TZ_OFFSET, ahora_local))
    print("Ventana: turnos dentro de las próximas {:g} h".format(REMIND_HOURS))
    if DRY_RUN:
        print(">> DRY_RUN activo: no se envían mails ni se marcan reservas.")

    reservas = fb_get("reservas") or {}
    instructores = fb_get("instructores") or {}
    idxInst = construir_indice_instructores(instructores)

    revisados = enviados = saltados_sinmail = saltados_fuera = errores = 0

    for key, r in reservas.items():
        if not isinstance(r, dict):
            continue
        if r.get("estado") != "aprobado":
            continue
        actor = (r.get("aprobado_por") or r.get("instructor") or "").strip()
        if not actor:
            continue  # aprobado automático (turismo) sin instructor: no aplica
        if r.get("recordatorio_inst_enviado") is True:
            continue
        if not r.get("fecha"):
            continue

        revisados += 1
        try:
            fdt = flight_dt_local(r)
        except Exception as e:
            print("  ! reserva", key, "fecha/hora inválida:", e)
            continue

        delta_h = (fdt - ahora_local).total_seconds() / 3600.0
        # Avisar si el vuelo está en el futuro y dentro de la ventana
        if not (0 < delta_h <= REMIND_HOURS):
            saltados_fuera += 1
            continue

        inst = idxInst.get(actor.lower())
        if not inst:
            saltados_sinmail += 1
            print("  ! sin email para instructor '{}' — turno {} {} (se omite)".format(
                actor, r.get("fecha"), hora_texto(r)))
            continue

        params = {
            "instructor_email": inst["email"],
            "instructor_nombre": inst["nombre"],
            "alumno_nombre": r.get("nombre", ""),
            "turno_hora": hora_texto(r),
            "matricula_avion": r.get("avion", "LV-OAD"),
            "name": FROM_NAME,
            "email": CLUB_EMAIL,
        }

        desc = "{} -> {} | {} {} | alumno: {}".format(
            inst["nombre"], inst["email"], r.get("fecha"), hora_texto(r), r.get("nombre", ""))

        if DRY_RUN:
            print("  [DRY] enviaría:", desc)
            enviados += 1
            continue

        try:
            status, body = enviar_mail(params)
            if status == 200:
                fb_marcar_enviado(key)
                enviados += 1
                print("  ✓ enviado:", desc)
            else:
                errores += 1
                print("  ✗ EmailJS status", status, body, "—", desc)
        except urllib.error.HTTPError as e:
            errores += 1
            print("  ✗ HTTP", e.code, e.read().decode("utf-8", "replace"), "—", desc)
        except Exception as e:
            errores += 1
            print("  ✗ error:", e, "—", desc)

    print("--")
    print("Revisados aprobados c/instructor: {} | enviados: {} | sin email: {} | "
          "fuera de ventana: {} | errores: {}".format(
              revisados, enviados, saltados_sinmail, saltados_fuera, errores))
    # Si hubo errores de envío, fallar el job para que se note en Actions
    if errores:
        sys.exit(2)


if __name__ == "__main__":
    main()
