#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mail_queue_sender.py — Drena /mail_queue y manda los mails de confirmación
de turno ('confirma') y liberación de turno ('libera') server-side.

POR QUÉ EXISTE: mailAprob()/mailLiberacion() en turnos.html llamaban a
emailjs.send() directo desde el navegador del instructor. Si esa red
bloqueaba el request (visto en producción 2026-09-10/11), el mail se
perdía sin que quedara registro en ningún lado — ni en EmailJS (el
request nunca salía del browser) ni para Daniel. Desde turnos.html v8.73,
aprobar/liberar un turno solo ENCOLA la intención en /mail_queue (mismo
fbPush que ya funciona siempre); este cron es el único que efectivamente
manda el mail, así no depende de la red/extensiones de quien aprueba.

Corre desde GitHub Actions cada 15 min. En cada ejecución:
  1. Lee /mail_queue completo (las keys de push ya vienen en orden
     cronológico, se procesan en ese orden).
  2. Para cada entrada, lee la reserva actual (/reservas/{reserva_key}).
     - tipo 'confirma': si la reserva ya no existe, o su estado ya no es
       'aprobado' (se liberó/canceló antes de que corriera el cron), el
       evento quedó superado — se borra la entrada SIN mandar mail.
     - tipo 'libera': se manda igual mientras la reserva exista (es aviso
       de un hecho pasado, no depende del estado actual).
  3. Manda el mail vía API REST de EmailJS (Cuenta A — service_8yqlptz,
     la misma que usa turnos.html desde el navegador para estos templates).
  4. Si se mandó bien (o se descartó por superado): borra la entrada de
     /mail_queue y marca la reserva (mail_confirma_enviado / ts, o
     mail_libera_enviado / ts) a fines de auditoría.
  5. Si falló el envío (error de red/EmailJS): NO borra la entrada, suma 1
     a su contador de intentos. Si superó MAX_INTENTOS, deja de reintentar
     y la marca con error=true (para que Daniel la vea en Firebase), pero
     igual la saca de circulación (no se reintenta más).

Solo usa librería estándar (urllib, json, datetime). No requiere pip.

Variables de entorno (las setea el workflow):
  FIREBASE_DB_URL      URL de la Realtime DB (sin barra final)
  EMAILJS_SERVICE_ID   service_8yqlptz (Cuenta A)
  EMAILJS_PUBLIC_KEY   public key de Cuenta A
  EMAILJS_PRIVATE_KEY_A private key de Cuenta A — SECRET, GitHub Secrets
  EMAILJS_TEMPLATE_CONFIRMA  template_4nsseoo
  EMAILJS_TEMPLATE_LIBERA    template_41kdlo3
  MAX_INTENTOS          reintentos antes de abandonar una entrada (default 8,
                         ~2hs a razón de 1 corrida cada 15min)
  DRY_RUN               "1" para probar sin enviar ni tocar Firebase (default "0")
"""

import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── Configuración ──────────────────────────────────────────────
DB_URL        = os.environ.get("FIREBASE_DB_URL", "").rstrip("/")
SERVICE_ID    = os.environ.get("EMAILJS_SERVICE_ID", "")
PUBLIC_KEY    = os.environ.get("EMAILJS_PUBLIC_KEY", "")
PRIVATE_KEY   = os.environ.get("EMAILJS_PRIVATE_KEY_A", "")
TMPL_CONFIRMA = os.environ.get("EMAILJS_TEMPLATE_CONFIRMA", "")
TMPL_LIBERA   = os.environ.get("EMAILJS_TEMPLATE_LIBERA", "")
MAX_INTENTOS  = int(os.environ.get("MAX_INTENTOS", "8"))
DRY_RUN       = os.environ.get("DRY_RUN", "0") == "1"

EMAILJS_URL = "https://api.emailjs.com/api/v1.0/email/send"

DIAS_ES  = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def faltan_config():
    faltan = [n for n, v in [
        ("FIREBASE_DB_URL", DB_URL), ("EMAILJS_SERVICE_ID", SERVICE_ID),
        ("EMAILJS_PUBLIC_KEY", PUBLIC_KEY), ("EMAILJS_PRIVATE_KEY_A", PRIVATE_KEY),
        ("EMAILJS_TEMPLATE_CONFIRMA", TMPL_CONFIRMA), ("EMAILJS_TEMPLATE_LIBERA", TMPL_LIBERA),
    ] if not v]
    return faltan


def fb_get(path):
    url = "{}/{}.json".format(DB_URL, path)
    req = urllib.request.Request(url, headers={"User-Agent": "aeroclub-cron"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if raw and raw != "null" else None


def fb_patch(path, body_dict):
    url = "{}/{}.json".format(DB_URL, path)
    body = json.dumps(body_dict).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="PATCH",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "aeroclub-cron"})
    urllib.request.urlopen(req, timeout=30).read()


def fb_delete(path):
    url = "{}/{}.json".format(DB_URL, path)
    req = urllib.request.Request(url, method="DELETE",
                                 headers={"User-Agent": "aeroclub-cron"})
    urllib.request.urlopen(req, timeout=30).read()


def fecha_linda(fecha_str):
    """'2026-06-24' -> 'Miércoles 24 de junio de 2026' (igual a fmtFechaLinda de turnos.html)."""
    y, m, d = map(int, fecha_str.split("-"))
    dt = datetime(y, m, d)
    return "{} {} de {} de {}".format(DIAS_ES[dt.weekday()], d, MESES_ES[m - 1], y)


def hora_texto(r):
    if r.get("horaInicio") and r.get("horaFin"):
        return r["horaInicio"] + " a " + r["horaFin"]
    return r.get("hora", "")


def enviar_mail(template_id, params):
    payload = {
        "service_id": SERVICE_ID,
        "template_id": template_id,
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


def main():
    faltan = faltan_config()
    if faltan:
        print("ERROR: faltan variables de entorno:", ", ".join(faltan))
        sys.exit(1)

    print("== Mail queue sender ==")
    if DRY_RUN:
        print(">> DRY_RUN activo: no se envían mails ni se toca Firebase.")

    cola = fb_get("mail_queue") or {}
    if not cola:
        print("Cola vacía, nada para hacer.")
        return

    enviados = descartados = errores = pendientes = 0

    # Las keys de push() de Firebase son ordenables cronológicamente.
    for qkey in sorted(cola.keys()):
        item = cola[qkey]
        if not isinstance(item, dict):
            continue
        tipo = item.get("tipo")
        reserva_key = item.get("reserva_key")
        intentos = int(item.get("intentos") or 0)

        if tipo not in ("confirma", "libera") or not reserva_key:
            print("  ! entrada inválida en cola, se borra:", qkey, item)
            if not DRY_RUN:
                fb_delete("mail_queue/" + qkey)
            continue

        r = fb_get("reservas/" + reserva_key)
        if not r:
            print("  - {} ({}): reserva ya no existe, se descarta.".format(qkey, tipo))
            descartados += 1
            if not DRY_RUN:
                fb_delete("mail_queue/" + qkey)
            continue

        if tipo == "confirma" and r.get("estado") != "aprobado":
            print("  - {} ({}): estado actual '{}' ya no es 'aprobado', evento "
                  "superado, se descarta.".format(qkey, tipo, r.get("estado")))
            descartados += 1
            if not DRY_RUN:
                fb_delete("mail_queue/" + qkey)
            continue

        if not r.get("email"):
            print("  ! {} ({}): reserva {} sin email, se descarta.".format(
                qkey, tipo, reserva_key))
            descartados += 1
            if not DRY_RUN:
                fb_delete("mail_queue/" + qkey)
            continue

        if tipo == "confirma":
            template_id = TMPL_CONFIRMA
            instructor = r.get("aprobado_por") or r.get("instructor") or ""
            params = {
                "to_email": r["email"], "alumno_nombre": r.get("nombre", ""),
                "fecha": fecha_linda(r["fecha"]), "hora": hora_texto(r),
                "avion": r.get("avion", "LV-OAD"), "instructor": instructor,
            }
            marca_ok = {"mail_confirma_enviado": True,
                        "mail_confirma_ts": datetime.now(timezone.utc).isoformat()}
        else:
            template_id = TMPL_LIBERA
            params = {
                "to_email": r["email"], "alumno_nombre": r.get("nombre", ""),
                "fecha": fecha_linda(r["fecha"]), "hora": hora_texto(r),
                "avion": r.get("avion", "LV-OAD"),
            }
            marca_ok = {"mail_libera_enviado": True,
                        "mail_libera_ts": datetime.now(timezone.utc).isoformat()}

        desc = "{} -> {} | {} {} | {}".format(
            r.get("nombre", ""), r["email"], r.get("fecha"), hora_texto(r), tipo)

        if DRY_RUN:
            print("  [DRY] enviaría:", desc)
            enviados += 1
            continue

        try:
            status, body = enviar_mail(template_id, params)
            if status == 200:
                fb_delete("mail_queue/" + qkey)
                fb_patch("reservas/" + reserva_key, marca_ok)
                enviados += 1
                print("  ✓ enviado:", desc)
            else:
                raise RuntimeError("EmailJS status {} {}".format(status, body))
        except Exception as e:
            intentos += 1
            if intentos >= MAX_INTENTOS:
                errores += 1
                fb_patch("mail_queue/" + qkey, {"intentos": intentos, "error": str(e)})
                print("  ✗ {} — abandonado tras {} intentos: {}".format(desc, intentos, e))
            else:
                pendientes += 1
                fb_patch("mail_queue/" + qkey, {"intentos": intentos})
                print("  … {} — intento {}/{} falló: {}".format(
                    desc, intentos, MAX_INTENTOS, e))

    print("--")
    print("Enviados: {} | descartados (superados): {} | reintentarán: {} | "
          "abandonados con error: {}".format(enviados, descartados, pendientes, errores))
    if errores:
        sys.exit(2)


if __name__ == "__main__":
    main()
