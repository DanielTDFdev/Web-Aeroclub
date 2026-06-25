#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recordatorio_instructor.py — Recordatorio automático por mail al instructor.

Corre desde GitHub Actions (cron). En cada ejecución:
  1. Lee /reservas e /instructores de Firebase (REST, reglas de lectura abiertas).
  2. Agrupa los turnos APROBADOS con instructor asignado por (instructor, fecha),
     descartando los que ya pasaron.
  3. Para cada grupo, toma el turno más temprano TODAVÍA FUTURO. Si ese turno cae
     dentro de la ventana de aviso (REMIND_HOURS) y el día no fue avisado todavía
     para ese instructor, manda UN SOLO mail con el listado completo de turnos
     del día (ordenados por hora).
  4. Marca el día como avisado en /recordatorios_diarios/{fecha}/{instructor} para
     no repetir, y además marca cada reserva individual con
     recordatorio_inst_enviado:true (solo a fines de auditoría/compatibilidad;
     el anti-duplicado real corre por el nodo diario).

Solo usa la librería estándar de Python (urllib, json, datetime). No requiere pip.

Configuración por variables de entorno (las setea el workflow):
  FIREBASE_DB_URL      URL de la Realtime DB (sin barra final)
  EMAILJS_SERVICE_ID   service_xxx de la cuenta que tiene el template
  EMAILJS_TEMPLATE_ID  template_8awr1zd
  EMAILJS_PUBLIC_KEY   public key (user_id) de esa cuenta
  EMAILJS_PRIVATE_KEY  private key (accessToken) — SECRET, viene de GitHub Secrets
  CLUB_EMAIL           mail del club (va como Reply-To, variable {{email}})
  REMIND_HOURS         ventana de aviso en horas, respecto del turno MÁS TEMPRANO
                        del día (default 2)
  TZ_OFFSET            offset horario local respecto de UTC (default -3, Argentina)
  DRY_RUN              "1" para probar sin enviar ni marcar (default "0")

IMPORTANTE — template EmailJS (template_8awr1zd):
  Recibe el listado completo del día en una sola variable de texto HTML:
  {{turnos_lista}} (cada turno con viñeta "• ", líneas separadas por <br>).
  En el template hay que usar TRIPLE llave {{{turnos_lista}}} (no doble), porque
  EmailJS escapa el HTML de las variables por default; con doble llave el <span>
  y el <br> llegaban literales en vez de interpretarse (resuelto 2026-06-25).
  También se siguen mandando instructor_nombre/instructor_email/name/email y
  cantidad_turnos (esas sí pueden ir con doble llave, son texto plano).
"""

import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

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


def fb_patch(path, body_dict):
    url = "{}/{}.json".format(DB_URL, path)
    body = json.dumps(body_dict).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="PATCH",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "aeroclub-cron"})
    urllib.request.urlopen(req, timeout=30).read()


def fb_marcar_reserva_enviada(key):
    """Marca individual de auditoría (no se usa para el anti-duplicado real)."""
    fb_patch("reservas/{}".format(key), {
        "recordatorio_inst_enviado": True,
        "recordatorio_inst_ts": datetime.now(timezone.utc).isoformat(),
    })


def fb_marcar_dia_avisado(fecha, instructor_key):
    fb_patch("recordatorios_diarios/{}/{}".format(fecha, instructor_key), {
        "enviado": True,
        "ts": datetime.now(timezone.utc).isoformat(),
    })


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


def fb_key_seguro(s):
    """Saca caracteres prohibidos en paths de Firebase (. # $ [ ] /)."""
    out = s
    for ch in ".#$[]/":
        out = out.replace(ch, "_")
    return out.strip("_") or "x"


def construir_indice_instructores(instData):
    """nombre (normalizado) -> {user, nombre, email}. Solo con email cargado."""
    idx = {}
    for k, v in (instData or {}).items():
        if not isinstance(v, dict):
            continue
        nombre = (v.get("nombre") or "").strip()
        email = (v.get("email") or "").strip()
        if nombre and email:
            idx[nombre.lower()] = {"user": k, "nombre": nombre, "email": email}
    return idx


DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def fecha_es(fecha_str):
    """'2026-06-24' -> 'Miércoles, 24 de Junio de 2026'."""
    y, m, d = map(int, fecha_str.split("-"))
    dt = datetime(y, m, d)
    dia = DIAS_ES[dt.weekday()]
    mes = MESES_ES[m - 1].capitalize()
    return "{}, {} de {} de {}".format(dia, d, mes, y)


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

    ahora_local = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=TZ_OFFSET)
    print("== Recordatorio instructor (agrupado por día) ==")
    print("Ahora (local UTC{:+g}): {:%Y-%m-%d %H:%M}".format(TZ_OFFSET, ahora_local))
    print("Ventana: turno más temprano del día dentro de las próximas {:g} h".format(REMIND_HOURS))
    if DRY_RUN:
        print(">> DRY_RUN activo: no se envían mails ni se marcan reservas/días.")

    reservas = fb_get("reservas") or {}
    instructores = fb_get("instructores") or {}
    idxInst = construir_indice_instructores(instructores)
    diasAvisados = fb_get("recordatorios_diarios") or {}

    # ── 1) Agrupar por (instructor_user, fecha), solo turnos futuros ──────
    grupos = {}  # (instructor_user, fecha) -> {"inst":..., "turnos":[(fdt,key,r)]}
    revisados = saltados_sinmail = saltados_pasado = 0

    for key, r in reservas.items():
        if not isinstance(r, dict):
            continue
        if r.get("estado") != "aprobado":
            continue
        actor = (r.get("aprobado_por") or r.get("instructor") or "").strip()
        if not actor:
            continue  # aprobado automático (turismo) sin instructor: no aplica
        if not r.get("fecha"):
            continue

        revisados += 1
        try:
            fdt = flight_dt_local(r)
        except Exception as e:
            print("  ! reserva", key, "fecha/hora inválida:", e)
            continue

        if fdt <= ahora_local:
            saltados_pasado += 1
            continue  # ya pasó, no cuenta para el día

        inst = idxInst.get(actor.lower())
        if not inst:
            saltados_sinmail += 1
            print("  ! sin email para instructor '{}' — turno {} {} (se omite)".format(
                actor, r.get("fecha"), hora_texto(r)))
            continue

        gkey = (inst["user"], r["fecha"])
        g = grupos.setdefault(gkey, {"inst": inst, "fecha": r["fecha"], "turnos": []})
        g["turnos"].append((fdt, key, r))

    # ── 2) Por cada grupo: ¿el más temprano entra en ventana? ─────────────
    enviados = saltados_yaavisado = saltados_fuera = errores = 0

    for (inst_user, fecha), g in grupos.items():
        inst = g["inst"]
        turnos = sorted(g["turnos"], key=lambda t: t[0])
        fdt_temprano = turnos[0][0]
        delta_h = (fdt_temprano - ahora_local).total_seconds() / 3600.0

        ya_avisado = bool((diasAvisados.get(fecha) or {}).get(inst_user, {}).get("enviado"))
        if ya_avisado:
            saltados_yaavisado += 1
            continue

        if not (0 < delta_h <= REMIND_HOURS):
            saltados_fuera += 1
            continue

        lineas = []
        for fdt, key, r in turnos:
            etiqueta = "piloto" if r.get("rol") == "piloto" else "alumno"
            color = "#0000cd" if etiqueta == "piloto" else "#006400"
            etiqueta_html = '<span style="color:{};font-weight:bold">{}</span>'.format(
                color, etiqueta)
            lineas.append("• {} hs — {} — {}: {}".format(
                hora_texto(r), r.get("avion", "LV-OAD"), etiqueta_html, r.get("nombre", "")))
        turnos_lista_html = "<br>".join(lineas)

        params = {
            "instructor_email": inst["email"],
            "instructor_nombre": inst["nombre"],
            "fecha_turnos": fecha_es(fecha),
            "cantidad_turnos": str(len(turnos)),
            "turnos_lista": turnos_lista_html,
            "name": FROM_NAME,
            "email": CLUB_EMAIL,
        }

        desc = "{} -> {} | {} | {} turno(s)".format(
            inst["nombre"], inst["email"], fecha, len(turnos))

        if DRY_RUN:
            print("  [DRY] enviaría:", desc)
            for fdt, key, r in turnos:
                print("        -", hora_texto(r), "—", r.get("nombre", ""))
            enviados += 1
            continue

        try:
            status, body = enviar_mail(params)
            if status == 200:
                fb_marcar_dia_avisado(fecha, inst_user)
                for fdt, key, r in turnos:
                    fb_marcar_reserva_enviada(key)
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
    print("Turnos revisados (aprobados c/instructor): {} | ya pasados: {} | sin email: {}".format(
        revisados, saltados_pasado, saltados_sinmail))
    print("Grupos (instructor+día) — mails enviados: {} | ya avisados: {} | "
          "fuera de ventana: {} | errores: {}".format(
              enviados, saltados_yaavisado, saltados_fuera, errores))
    if errores:
        sys.exit(2)


if __name__ == "__main__":
    main()
