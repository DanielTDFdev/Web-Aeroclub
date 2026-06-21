#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vencimiento_turnos.py — Vencimiento automático de turnos PENDIENTES.

Corre desde GitHub Actions (cron). En cada ejecución:
  1. Lee /reservas de Firebase (REST, reglas de lectura abiertas).
  2. Busca turnos PENDIENTES cuyo vuelo cae dentro de las próximas EXPIRE_HOURS
     horas (o ya pasó) y que TUVIERON ventana de confirmación (creados antes del
     mojón de vencimiento). Esos turnos "se cayeron": ningún instructor los
     confirmó a tiempo.
  3. Marca la reserva como estado:'vencido' (la app ya entiende este estado y
     libera el slot automáticamente).
  4. Si el vuelo todavía es futuro, avisa al ALUMNO por mail (EmailJS REST,
     server-side, con private key). Si el vuelo ya pasó, solo limpia (no manda
     mail: no tiene sentido avisar de un turno viejo).
  5. Registra el vencimiento en /auditoria.
  6. Purga borradores FPL de externos: borra de los buckets /fpl/externo* los
     borradores con más de FPL_PURGE_HOURS de creados (fecha_creacion). Cada externo
     no logueado tiene su propio bucket efímero por sesión; esto limpia los que quedan.

Solo usa la librería estándar de Python (urllib, json, datetime). No requiere pip.

Por qué EXPIRE_HOURS debe ser < 12:
  El alumno puede reservar hasta 12 h antes del vuelo. Si el vencimiento dispara
  a las 12 h (o más) antes, un turno pedido en el límite nacería ya vencido. Con
  6 h queda una ventana de 6 h (desde las 12 h-antes hasta las 6 h-antes) para que
  un instructor confirme. Más chico el número = más tarde se cae = más ventana.

Configuración por variables de entorno (las setea el workflow):
  FIREBASE_DB_URL      URL de la Realtime DB (sin barra final)
  EMAILJS_SERVICE_ID   service_xxx de la cuenta server-side (dcamargo70)
  EMAILJS_TEMPLATE_ID  template del aviso de vencimiento al alumno (NUEVO)
  EMAILJS_PUBLIC_KEY   public key (user_id) de esa cuenta
  EMAILJS_PRIVATE_KEY  private key (accessToken) — SECRET, viene de GitHub Secrets
  CLUB_EMAIL           mail del club (va como Reply-To, variable {{email}})
  EXPIRE_HOURS         ventana de vencimiento en horas (default 6, DEBE ser < 12)
  TZ_OFFSET            offset horario local respecto de UTC (default -3, Argentina)
  FPL_PURGE_HOURS      antigüedad (h) para borrar borradores FPL externos (default 1)
  DRY_RUN              "1" para probar sin enviar, sin marcar, sin auditar (default "0")
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
EXPIRE_HOURS = float(os.environ.get("EXPIRE_HOURS", "6"))
TZ_OFFSET    = float(os.environ.get("TZ_OFFSET", "-3"))
FPL_PURGE_HOURS = float(os.environ.get("FPL_PURGE_HOURS", "1"))
DRY_RUN      = os.environ.get("DRY_RUN", "0") == "1"

EMAILJS_URL = "https://api.emailjs.com/api/v1.0/email/send"
FROM_NAME   = "Aeroclub Río Grande"   # variable {{name}} del template

MOTIVO_VENC = ("No se confirmó un instructor para tu turno dentro del plazo, "
               "así que el turno quedó sin efecto y el horario volvió a estar "
               "disponible. Podés volver a reservar cuando quieras.")

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


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


def fb_get_shallow(path):
    """GET con ?shallow=true: trae solo las claves de primer nivel ({clave: true}),
    sin descargar el subárbol. Útil para listar buckets sin traer todo /fpl."""
    url = "{}/{}.json?shallow=true".format(DB_URL, path)
    req = urllib.request.Request(url, headers={"User-Agent": "aeroclub-cron"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if raw and raw != "null" else None


def fb_delete(path):
    """DELETE de una clave (reglas de escritura abiertas)."""
    url = "{}/{}.json".format(DB_URL, path)
    req = urllib.request.Request(url, method="DELETE",
                                 headers={"User-Agent": "aeroclub-cron"})
    urllib.request.urlopen(req, timeout=30).read()


def fb_marcar_vencido(key):
    """PATCH la reserva a estado:'vencido' (reglas de escritura abiertas)."""
    url = "{}/reservas/{}.json".format(DB_URL, key)
    body = json.dumps({
        "estado": "vencido",
        "vencido_auto": True,
        "vencido_ts": datetime.now(timezone.utc).isoformat(),
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="PATCH",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "aeroclub-cron"})
    urllib.request.urlopen(req, timeout=30).read()


def _fb_post_auditoria(accion, rol, detalle):
    """POST genérico a /auditoria, mismo esquema que registrarAuditoria() de la app."""
    url = "{}/auditoria.json".format(DB_URL)
    body = json.dumps({
        "accion": accion,
        "rol": rol,
        "resultado": "exito",
        "detalle": detalle,
        "ts": datetime.now(timezone.utc).isoformat(),
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "aeroclub-cron"})
    urllib.request.urlopen(req, timeout=30).read()


def fb_auditar(detalle):
    """Registra el vencimiento de un turno en /auditoria."""
    _fb_post_auditoria("vencimiento_turno", "sistema", detalle)


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


def hora_texto(r):
    if r.get("horaInicio") and r.get("horaFin"):
        return r["horaInicio"] + " a " + r["horaFin"]
    return r.get("hora", "")


def fecha_linda(ds):
    """YYYY-MM-DD -> '18 de junio de 2026' (sin día de semana, simple)."""
    try:
        y, m, d = map(int, ds.split("-"))
        return "{} de {} de {}".format(d, MESES[m - 1], y)
    except Exception:
        return ds


def flight_dt_local(r):
    """datetime naive en hora local de la reserva."""
    y, m, d = map(int, r["fecha"].split("-"))
    hh, mm = map(int, (r.get("hora") or "00:00").split(":"))
    return datetime(y, m, d, hh, mm, 0)


def ts_local(r):
    """ts de creación (ISO UTC con 'Z') -> datetime naive en hora local. None si falta/ inválido."""
    ts = r.get("ts")
    if not ts:
        return None
    try:
        # toISOString() siempre termina en 'Z' (UTC). fromisoformat no come la 'Z'.
        s = ts.replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(s)
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        return dt_utc.astimezone(timezone.utc).replace(tzinfo=None) + timedelta(hours=TZ_OFFSET)
    except Exception:
        return None


def _parse_iso_utc(s):
    """ISO (con o sin 'Z') -> datetime aware en UTC. None si no parsea."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fpl_purga_externos(ahora_utc):
    """Borra borradores FPL de buckets externos (/fpl/externo*) con más de
    FPL_PURGE_HOURS de creados (campo fecha_creacion, ISO UTC). Un borrador SIN
    fecha_creacion legible se considera viejo y se purga. Devuelve cantidad de errores."""
    print("--")
    print("== Purga de borradores FPL externos (> {:g} h de creados) ==".format(FPL_PURGE_HOURS))
    try:
        top = fb_get_shallow("fpl") or {}
    except Exception as e:
        print("  ! no se pudo listar /fpl, se omite la purga:", e)
        return 1

    ext_keys = [k for k in top.keys() if k == "externo" or k.startswith("externo_")]
    if not ext_keys:
        print("  (sin buckets externos)")
        return 0

    limite = ahora_utc - timedelta(hours=FPL_PURGE_HOURS)
    borrados = errores = 0

    for bk in ext_keys:
        try:
            bucket = fb_get("fpl/" + bk)
        except Exception as e:
            print("  ! no se pudo leer fpl/{}: {}".format(bk, e))
            errores += 1
            continue
        if not isinstance(bucket, dict):
            continue
        for dk, draft in bucket.items():
            if not isinstance(draft, dict):
                continue
            dt = _parse_iso_utc(draft.get("fecha_creacion"))
            viejo = (dt is None) or (dt < limite)   # sin fecha legible => purgar
            if not viejo:
                continue
            etiqueta = "fpl/{}/{} (creado {})".format(bk, dk, draft.get("fecha_creacion") or "sin fecha")
            if DRY_RUN:
                print("  [DRY] borraría", etiqueta)
                borrados += 1
                continue
            try:
                fb_delete("fpl/{}/{}".format(bk, dk))
                borrados += 1
                print("  ✓ borrado", etiqueta)
            except Exception as e:
                errores += 1
                print("  ✗ no se pudo borrar {}: {}".format(etiqueta, e))

    print("  borradores FPL externos borrados: {} | errores: {}".format(borrados, errores))
    if borrados and not DRY_RUN:
        try:
            _fb_post_auditoria("purga_fpl_externos", "sistema",
                               "Purga de {} borrador(es) FPL externo(s) con más de {:g} h".format(
                                   borrados, FPL_PURGE_HOURS))
        except Exception as e:
            print("  ! no se pudo auditar la purga (sigue):", e)
    return errores


def main():
    faltan = faltan_config()
    if faltan:
        print("ERROR: faltan variables de entorno:", ", ".join(faltan))
        sys.exit(1)

    if EXPIRE_HOURS >= 12:
        print("ERROR: EXPIRE_HOURS={:g} es >= 12. Debe ser MENOR que la anticipación "
              "mínima del alumno (12 h), si no los turnos nacen vencidos. Abortando."
              .format(EXPIRE_HOURS))
        sys.exit(1)

    ahora_local = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=TZ_OFFSET)
    print("== Vencimiento de turnos pendientes ==")
    print("Ahora (local UTC{:+g}): {:%Y-%m-%d %H:%M}".format(TZ_OFFSET, ahora_local))
    print("Vence si el vuelo está dentro de las próximas {:g} h y sigue pendiente."
          .format(EXPIRE_HOURS))
    if DRY_RUN:
        print(">> DRY_RUN activo: no se marca, no se envía mail, no se audita.")

    reservas = fb_get("reservas") or {}

    revisados = vencidos = mails = saltados_sinventana = saltados_fuera = errores = 0

    for key, r in reservas.items():
        if not isinstance(r, dict):
            continue
        if r.get("estado") != "pendiente":
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

        # Solo cae si el vuelo está dentro de la ventana (faltan <= EXPIRE_HOURS),
        # incluido el caso de un vuelo que ya pasó (delta_h <= 0).
        if delta_h > EXPIRE_HOURS:
            saltados_fuera += 1
            continue

        # Guard del borde: solo vence si TUVO ventana de confirmación, es decir si
        # fue creado ANTES del mojón de vencimiento (vuelo - EXPIRE_HOURS). Los
        # turnos nacidos ya dentro de la ventana (p. ej. piloto LV-OAD a 1 h) nunca
        # tuvieron chance de aprobarse: no se los vence acá.
        mojon = fdt - timedelta(hours=EXPIRE_HOURS)
        tsl = ts_local(r)
        if tsl is not None and tsl >= mojon:
            saltados_sinventana += 1
            print("  ~ sin ventana (creado dentro del plazo), se omite: {} {} — {}".format(
                r.get("fecha"), hora_texto(r), r.get("nombre", "")))
            continue

        desc = "{} {} | {} | alumno: {} <{}>".format(
            r.get("fecha"), hora_texto(r), r.get("avion", "LV-OAD"),
            r.get("nombre", ""), r.get("email", ""))

        # vuelo ya pasado: limpiar en silencio, sin mail
        vuelo_pasado = delta_h <= 0

        if DRY_RUN:
            print("  [DRY] vencería{}: {}".format(
                " (vuelo pasado, sin mail)" if vuelo_pasado else " + mail", desc))
            vencidos += 1
            if not vuelo_pasado:
                mails += 1
            continue

        # 1) marcar vencido (libera el slot)
        try:
            fb_marcar_vencido(key)
            vencidos += 1
        except Exception as e:
            errores += 1
            print("  ✗ no se pudo marcar vencido:", e, "—", desc)
            continue

        # 2) auditar
        try:
            fb_auditar("Vencimiento automático — {} — {} {} hs — alumno: {}".format(
                r.get("avion", "LV-OAD"), r.get("fecha"), r.get("hora", ""), r.get("nombre", "")))
        except Exception as e:
            print("  ! no se pudo auditar (sigue):", e)

        # 3) avisar al alumno (solo si el vuelo es futuro y hay email)
        if vuelo_pasado:
            print("  ✓ vencido (vuelo pasado, sin mail):", desc)
            continue
        if not r.get("email"):
            print("  ✓ vencido (alumno sin email, sin mail):", desc)
            continue

        params = {
            "alumno_email": r.get("email", ""),
            "alumno_nombre": r.get("nombre", ""),
            "fecha": fecha_linda(r.get("fecha", "")),
            "hora": hora_texto(r),
            "avion": r.get("avion", "LV-OAD"),
            "motivo": MOTIVO_VENC,
            "name": FROM_NAME,
            "email": CLUB_EMAIL,
        }
        try:
            status, body = enviar_mail(params)
            if status == 200:
                mails += 1
                print("  ✓ vencido + mail:", desc)
            else:
                errores += 1
                print("  ✗ vencido OK pero EmailJS status", status, body, "—", desc)
        except urllib.error.HTTPError as e:
            errores += 1
            print("  ✗ vencido OK pero HTTP", e.code,
                  e.read().decode("utf-8", "replace"), "—", desc)
        except Exception as e:
            errores += 1
            print("  ✗ vencido OK pero error de mail:", e, "—", desc)

    print("--")
    print("Pendientes revisados: {} | vencidos: {} | mails: {} | sin ventana: {} | "
          "fuera de ventana: {} | errores: {}".format(
              revisados, vencidos, mails, saltados_sinventana, saltados_fuera, errores))

    # Purga de borradores FPL externos (concern aparte, mismo cron por pedido).
    err_fpl = fpl_purga_externos(datetime.now(timezone.utc))

    if errores or err_fpl:
        sys.exit(2)


if __name__ == "__main__":
    main()
