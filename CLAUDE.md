# Aeroclub Río Grande — Web-Aeroclub

Sistema de gestión de vuelos, reservas e instrucción del Aeroclub Río Grande (Tierra del Fuego). Deploy: GitHub → Cloudflare (cache manual, ver abajo). Sin build, sin bundler — HTML/CSS/JS vanilla con ES modules.

## Flota y stack

- LV-OAD (Tomahawk PA-38-112, instrucción exclusiva salvo excepción en `vuelo-piloto.html`), LV-ART (Archer II), LV-MPH (Lance II).
- Firebase Realtime Database (`turnos-lv-oad-default-rtdb.firebaseio.com`) — **sin `.indexOn`**: cualquier consulta trae el nodo completo y filtra client-side. No asumas que existe un índice.
- EmailJS (dos cuentas separadas, no consolidar — ver `documentacion_turnos.md` si hace falta tocar mails).
- PWA compartida (`sw.js` en la raíz), manifests separados por app.

## REGLA #1 — Versionado (nunca te la saltees)

Cada archivo principal tiene su propio número de versión, y **vive en más de un lugar**. Antes de cualquier cambio, releé el archivo real desde el repo (nunca asumas la versión de una sesión anterior). Después de cualquier cambio:

- **`turnos.html`**: versión en EXACTAMENTE DOS lugares — comentario del header (arriba del todo) y el string `.hero-sub` (`<div class="hero-sub">▸ RESERVA DE AERONAVES vX.XX</div>`). El `_meta.version` de `ejecutarBackup()` es la versión del esquema de backup, **no la toques**, es independiente.
- **`vuelo.html` / `vuelo-piloto.html`**: comentario del header + constante `const APP_VERSION = 'X.X'`.
- **`vor-trainer.html`**: comentario del header + `<span class="ver">vX.XX</span>`.
- **`fpl.html`, `portal-alumno.html`, `portal-socio.html`, `tesoreria.html`, `peso-balance.html`, `reporte.html`**: comentario del header + `.ver` span.

Bump SIEMPRE que se toque el archivo, aunque sea un cambio chico. Agregá una entrada de changelog en el comentario del header (español, quién lo pidió — "Pedido de Daniel: ...", qué cambió, por qué).

## REGLA #2 — Validación antes de dar por terminado

- **Sintaxis JS**: extraé el bloque `<script type="module">...</script>` y corré `node --check` sobre él. Nunca asumas que compila.
- **Balance de `<div>`**: contá `<div` vs `</div`. Baseline: **`turnos.html` da +1** (no es bug, es así desde siempre). El resto de los archivos (`vuelo.html`, `vuelo-piloto.html`, etc.) tiene que dar 0 exacto.
- Si `str_replace`/edición falla por backticks o template literals complejos, usá `sed` con número de línea exacto o un script Python — no reescribas el archivo entero a mano.

## REGLA #3 — `documentacion_turnos.md`

Documento vivo, grande (~1300 líneas), en `docs/documentacion_turnos.md`. **Solo se actualiza cuando el usuario lo pide explícitamente** — no lo toques por iniciativa propia después de cada cambio de código. Cuando SÍ te lo pidan actualizar:
- Actualizá la línea de cabecera "Versión documentada" con las versiones reales.
- Agregá contenido en la sección correspondiente (`## N. Nombre del archivo`) sin borrar el historial de entradas viejas — son registro de época, se dejan como están aunque describan una arquitectura ya reemplazada (marcá "retirado en vX.X" si corresponde, no la borres).
- Agregá una línea nueva en el historial "Versión de este documento vs. repo" al final del archivo.
- Si corregís algo que el doc tenía mal, dejá una nota tipo "(corregido AAAA-MM-DD, confirmado por Daniel)" en vez de borrar el error silenciosamente.

## Gotchas reales, ya pisados una vez — no los repitas

- **Temporal Dead Zone (TDZ):** cualquier `let`/`const` usada por código que corre SINCRÓNICAMENTE al cargar el módulo (ej. sesión auto-restaurada que dispara `abrirApp()` sin que el usuario toque nada) tiene que estar declarada *antes* de ese punto de ejecución en el archivo. Las `function` declarations hoistean, las `let`/`const` no. Esto ya causó bugs reales de "pantalla negra" dos veces (`vuelo-piloto.html` con `FLOTA`/`NOMBRE_AVION`, y con las variables del mini-calendario).
- **`emailjs.createInstance()` no existe** en el SDK v4 — usar `emailjs.init(key)` antes de cada `.send()`.
- **`toLocaleTimeString` con `timeZone:'UTC'` sin `hour12:false` explícito** puede caer en formato AM/PM según navegador — siempre forzar `hour12:false`, o mejor: usar `getUTCHours()`/`getUTCMinutes()` a mano y evitar el locale por completo.
- **`confirm()` nativo es SINCRÓNICO y bloquea todo el hilo de JS** — si hay un GPS `watchPosition` corriendo, se pierden puntos mientras el diálogo está abierto. No usar `confirm()` para nada que pueda solaparse con tracking GPS activo; usar un modal propio (HTML/CSS, no bloqueante) en su lugar.
- **Modal anidado dentro de un contenedor oculto nunca abre** — los modales deben vivir sueltos en el HTML (hijos directos de `<body>` o fuera de cualquier `display:none` ancestro), nunca anidados dentro de una pestaña/tab que pueda estar oculta.
- **Firebase `.write:true` sin auth** — cualquier campo que venga de `/alumnos`/`/reservas` (nombre, avión, etc.) es XSS-able si se inserta con `innerHTML`/`onclick="..."` sin escapar. Usar `textContent` o escapar comillas simples y dobles.
- **`update()` vs `set()` en Firebase**: `update()` mergea (no borra campos que no toca), `set()` reemplaza el nodo entero. Para escribir sobre una reserva ya existente usar siempre `update()`, salvo que la intención sea reemplazar todo el nodo.
- **GPS/mapa está TRIPLICADO, no compartido**: `turnos.html` (`abrirMapaVuelo`), `vuelo.html` (`abrirMapa`) y `vuelo-piloto.html` (`abrirMapa`) tienen cada uno su propia copia completa del visor Leaflet. Cualquier fix de mapa/GPX (zoom, huecos, unidades) hay que aplicarlo **tres veces**, a mano, en los tres archivos.
- **`isolation:isolate`** hace falta en el contenedor de Leaflet si hay elementos hermanos (headers, botones flotantes) que deben quedar SIEMPRE por encima del mapa — sin eso, los z-index internos de Leaflet (hasta 700) se escapan y tapan cualquier overlay externo con z-index más bajo.
- **Cloudflare no cachea HTML por defecto**, pero en la práctica a veces sí sirve una copia vieja — el deploy real es: subir a GitHub → Cloudflare "Purge Everything" a mano. Nada de esto es automatizable desde acá (Claude Code no tiene acceso a la cuenta de Cloudflare de Daniel).
- **iOS PWA instalada** puede quedarse con una copia vieja del archivo pese a purgar Cloudflare — el caché vive en el dispositivo (WKWebView), no en la red. Única solución: desinstalar + borrar datos del sitio en Safari + reinstalar.

## Cómo se llama cada rol (no lo inventes distinto)

- `admin` — registro `/instructores/admin` (desde turnos v8.84 ya no hay fallback hardcodeado admin/admin123). Desde v8.83 admin/administrador migran a Firebase Auth con email sintético `user@instructores.aeroclubriogrande.com.ar`, igual que los instructores; los permisos de `/cc_*` salen de `/cc_staff/{emailKey}` (no de `/instructores`, que es escribible por cualquiera).
- `administrador` — sí viene de Firebase, acceso restringido (`esAdminRO()`).
- `instructor`, `alumno`, `piloto` — roles de `/alumnos`, determinan qué ve cada uno. `alumno` NO puede iniciar un vuelo sin turno previo y solo trackea GPS (el instructor graba tiempo/horarios); `piloto` tiene acceso completo. Ambos roles ven Libro y Reporte por igual en `vuelo-piloto.html`.
- `cancelado_rol` en una reserva SOLO puede ser `'alumno'` o `'instructor'` (binario, no hay un tercer valor) — afecta cálculos reales de cuota semanal (`turnosRestantesSemana()`). Una cancelación automática/de sistema que no debería penalizar al dueño del turno va con `cancelado_rol:'instructor'` (mismo bucket que usa el sistema para bajas por meteorología/mantenimiento), nunca inventar un valor nuevo.

## Estilo de respuesta esperado

Directo, sin vueltas, en español. No hace falta preámbulo largo antes de mostrar el resultado. Si hay una decisión de producto/UX que no está clara (no una duda técnica de implementación), preguntar antes de asumir — pero no preguntar por cosas que ya están claramente definidas en este archivo.
