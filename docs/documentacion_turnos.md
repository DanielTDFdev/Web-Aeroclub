# Documentación técnica — Sistema de Turnos (turnos.html)

**Aeroclub Río Grande (SAWE) — Tierra del Fuego, Argentina**
Versión documentada: **turnos.html v5.94** · **fpl.html v3.16** · Fecha: 2026-06-23

> Documento de referencia: describe qué hace cada parte del sistema. Mantener actualizado cuando se agreguen funciones.
> Además de la app web (`turnos.html`) hay un generador de planes de vuelo (`fpl.html`, §22) y **dos procesos server-side** en GitHub Actions: el recordatorio a instructores (§20) y el vencimiento de pendientes + purga de borradores FPL (§21).

---

## 1. Resumen general

Aplicación web de página única (SPA) en un solo archivo HTML, para reservar las aeronaves del aeroclub. Maneja registro y login de usuarios, solicitud y aprobación de turnos, disponibilidad de instructores, configuración de horarios por avión, auditoría y backup. Sin frameworks: HTML/CSS/JS vanilla. Backend: Firebase Realtime Database (acceso directo desde el navegador). Email transaccional: EmailJS. Autenticación: en transición de login propio (password en texto plano) a Firebase Auth (ver §7).

## 2. Stack, hosting y despliegue

- **Hosting:** GitHub Pages (`danieltdfdev/Web-Aeroclub`), servido vía Cloudflare (CDN + DNS) en `aeroclubriogrande.com.ar`.
- **Backend:** Firebase Realtime Database, SDK v10.12.0 (módulos `firebase-app`, `firebase-database`, `firebase-auth`) importados por CDN.
- **Email:** EmailJS SDK v4 (CDN).
- **Despliegue:** subir el archivo a GitHub → verificar la versión en pantalla. (`Purge Everything` en Cloudflare sigue siendo buena práctica para assets cacheados en el borde.)
- **Caché del navegador (clave):** Cloudflare **no cachea HTML en el borde** por defecto, así que purgar nunca afectó al `turnos.html` — el problema de "no se ve el cambio" era la **caché del navegador de cada usuario**. Resuelto (2026-06-17) con una **Cache Rule** que matchea `.html` con *Browser TTL: Bypass cache*, lo que agrega `no-store` a la respuesta del HTML. Ahora cada visita trae el HTML fresco y los deploys se ven al instante en todos los navegadores, sin depender de purgas ni recargas forzadas.
- **Verificación de despliegue:** la versión visible en el `.hero-sub` ("RESERVA DE AERONAVES vX.XX") debe coincidir con la subida.

## 3. Modelo de datos (Firebase Realtime Database)

> Reglas actuales: `{".read":true, ".write":true}` (abiertas — ver §7 y limitaciones). No hay queries tipo SQL: todo el filtrado es del lado del cliente.

### `/alumnos/{emailKey}`
Clave = email con `.`→`_` y `@`→`__at__` (función `ek()`).
- `nombre`, `email`, `username` (único, minúsculas, 4–10 alfanumérico), `tel`
- `pass` — contraseña en texto plano (se eliminará al final de la migración a Auth)
- `rol` — `alumno` | `piloto`
- `estado` — `pendiente_aprobacion` | `aprobado`
- `estado_login` — `normal` | `suspendido` (suspendido bloquea el ingreso)
- `pass_temporal` — `true` fuerza cambio de clave en el próximo login
- `authMigrado` — `true` si ya existe la cuenta en Firebase Auth
- `ts` — fecha de alta (ISO)

### `/instructores/{user}`
Clave = nombre de usuario.
- `user`, `nombre`, `pass`
- `email` — mail real del instructor; lo usa el recordatorio automático (§20). Editable por el admin (modal de instructor) o por el propio instructor (Mi Perfil). Las cuentas sin email se saltean en el recordatorio y se marcan con "⚠ sin mail" en la lista.
- `cel` — celular de contacto (opcional, informativo)
- `vacaciones` — `true` suspende su disponibilidad sin borrar datos
- `soloUsuarios` — `true` restringe el acceso a la gestión de usuarios (lo usa `administrador`)
- `pass_temporal`, `authMigrado` — igual que en alumnos
- Cuentas especiales: `admin` (acceso de emergencia, clave fija `admin123`, nombre "Instructor") y `administrador` (clave en Firebase, `soloUsuarios:true`). Ninguna de las dos se migra a Auth ni puede aprobar turnos.

### `/reservas/{pushKey}`
- `nombre`, `email`, `rol`
- `fecha` (`YYYY-MM-DD`), `hora` (`HH:MM`)
- `horaInicio`, `horaFin` — solo para reservas de rango (piloto en aviones que no son LV-OAD)
- `avion` — `LV-OAD` | `LV-ART` | `LV-MPH`
- `obs` — observaciones del usuario
- `estado` — `pendiente` | `aprobado` | `cancelado` | `vencido`
- `sinInstructor` — `true` solo en turnos de **piloto en LV-OAD** cargados un día sin instructor con horario disponible (v5.94). Marca de origen: se calcula al confirmar (fresco) y solo se escribe si es `true`. Afecta el color en la UI mientras el turno está `pendiente` (rosa/fucsia); no condiciona la lógica de aprobación.
- `instructor` — nombre del instructor que actuó (aprobó/canceló)
- `aprobado_por` — nombre de quien aprobó (para Seguimiento)
- `cancelado_por` — nombre de quien canceló
- `obs_cancelacion` — motivo de cancelación
- `recordatorio_inst_enviado` — `true` si el recordatorio automático al instructor ya se envió para este turno (evita reenvíos). Lo escribe el cron (§20). Se limpia al liberar el turno.
- `recordatorio_inst_ts` — timestamp ISO del envío del recordatorio.
- `obs_post` — observación post-vuelo cargada por el instructor que aprobó (o admin) sobre un turno ya pasado (ej. "no se voló por meteo"). Editable desde el modal; se audita como `obs_post_turno` (v5.87/v5.88).
- `ts` — fecha de solicitud (ISO)

### `/config/{avionKey}`
Configuración por avión.
- `horariosDia` — objeto con claves 0–6 (día de semana, 0=Dom) → arrays de `"HH:MM"` habilitados
- `diasBloqueados` — array de `"YYYY-MM-DD"`
- `activo` — `false` deshabilita el avión para reservas

### `/disponibilidad/{YYYY-MM-DD}/{username}`
Array de `"HH:MM"` que el instructor marcó como disponibles ese día. La clave es el `user` del instructor. **Solo se usa para LV-OAD.** Filtra los slots visibles al reservar.

### `/auditoria/{pushKey}`
- `accion`, `rol`, `resultado` (`exito`/`fallo`/`bloqueado`), `detalle`, `ts`
- Conviven dos tipos de registro: los de **turnos** (los que escribe `turnos.html`) y los de **FPL** (`rol:'fpl'`, `accion:'fpl_*'`, los escribe `fpl.html` al generar un PDF). La pantalla de auditoría de turnos **excluye** los `rol:'fpl'`; estos se ven en su propia sub-pestaña "Audit. FPL" (§10, §22).

### `/aeronaves/{MATRICULA}`
Flota del club (global, la lee `fpl.html`; auto-seed la primera vez). Datos OACI del avión para precargar el plan de vuelo (casillas 7/9/10/15/19A).

### `/aeronaves_usuario/{USUARIO}/{MATRICULA}`
Aeronaves **personales** de cada usuario registrado (no las del club). Las crea/edita/borra el dueño desde `fpl.html`; solo el dueño las ve. Incluyen datos FPL (7/9/10/15/18) y SPL (Item 19: R/ S/ J/ D/ A/ C/ N/).

### `/fpl/{USUARIO}/{pushKey}`
Borradores de plan de vuelo por usuario. `USUARIO` = username/clave estable del usuario logueado (ver §22).

### `/fpl/externo_{uuid}/{pushKey}`
Borradores de usuarios **no logueados**: cada sesión externa recibe un bucket efímero (`uuid` en `sessionStorage`). El cron de vencimiento (§21) purga los borradores externos con más de 1 h de antigüedad.

## 4. Roles y permisos

- **alumno** — solo reserva LV-OAD; cada turno requiere aprobación de instructor; anticipación mínima 12h; horizonte 7 días.
- **piloto** — reserva las tres aeronaves; en aviones que no son LV-OAD la aprobación es automática y puede reservar rangos de horas; en **LV-OAD reserva como alumno** (1 slot de 1h, queda pendiente de aprobación) con una diferencia clave (v5.94): **puede reservar aunque no haya instructor con horario cargado ese día** — solo se verifica que el avión esté disponible y el slot libre, sin exigir la combinación avión + instructor. Esos turnos se marcan `sinInstructor:true` y salen en color rosa hasta que un instructor los apruebe. El alumno, en cambio, sigue viendo únicamente slots con instructor disponible. Anticipación mínima 1h; horizonte 30 días.
- **pendiente_aprobacion** — usuario recién registrado, sin acceso hasta que un instructor lo apruebe (pantalla de espera).
- **instructor** (real) — aprueba/cancela/libera turnos, configura horarios, gestiona su disponibilidad y vacaciones. `esInstructorReal()` = sesión instructor que NO es `admin` ni `administrador`.
- **admin** — acceso de emergencia con clave fija; acceso completo incluida la Zona Peligrosa. **No puede aprobar turnos.**
- **administrador** — gestión completa salvo edición de horarios/disponibilidad (solo lectura) y Zona Peligrosa; `esAdminRO()` = true. **No puede aprobar turnos.**

> Regla clave (v5.65/v5.68): **solo los instructores reales aprueban turnos**, por cualquier vía (modal de detalle y modal de Consultas). admin/administrador solo pueden cancelar.
> Cancelación (v5.86): **cualquier instructor real (o admin) puede cancelar cualquier turno aprobado**, sea de LV-OAD o de otro avión, sin importar quién lo aprobó (antes estaba atado al aprobador, lo que impedía cancelar auto-aprobados de piloto en LV-ART/LV-MPH). **Liberar** un turno sí sigue restringido al instructor que aprobó o a admin.

## 5. Aeronaves y configuración por avión

Definidas en la constante `AVIONES`: **LV-OAD** (instrucción, Tomahawk PA-38-112), **LV-ART** (turismo, Archer II PA-28-181), **LV-MPH** (turismo, Lance II PA-32RT). Cada avión tiene su propia `/config` (horarios por día, días bloqueados, activo/inactivo). LV-OAD es el avión escuela: prioridad del alumno y control estricto del instructor por aprobación.

## 6. Reglas de negocio

- **Anticipación mínima:** alumno 12h (`ok12h`), piloto 1h (`ok1h`); se elige por rol en `okAnticipacion()`.
- **Horizonte máximo:** alumno 7 días, piloto 30 días (`getDays`).
- **Aprobación:** manual por instructor para LV-OAD y para todo alumno; automática solo para piloto en aviones que no son LV-OAD.
- **Disponibilidad de instructor para reservar LV-OAD:** el **alumno** solo puede elegir slots donde algún instructor (no de vacaciones) declaró disponibilidad ese día. El **piloto** (v5.94) no tiene esa restricción: puede cargar cualquier slot libre de LV-OAD aunque no haya instructor con horario ese día; el turno igual queda `pendiente` de aprobación y se marca `sinInstructor:true` (color rosa en selector y calendario, tooltip "SIN INSTRUCTOR"). El color rosa solo aplica mientras está `pendiente`; al aprobarse vuelve al verde normal.
- **Cancelación por el usuario:** piloto sin límite; alumno hasta 2h antes (`puedeAlumnoCancelar`).
- **Vencimiento:** un turno `pendiente` o `aprobado` pasa a `vencido` cuando su fecha/hora ya pasó (`vencerTurnosPendientes`, barrido **perezoso** del lado del cliente). **Además**, un proceso server-side (cron `vencimiento_turnos.py`, §21) vence los **pendientes** que nadie confirmó dentro de la ventana previa al vuelo (default 6 h antes) y le avisa al alumno por mail, sin depender de que haya alguien con la app abierta. Ojo con la ambigüedad del término: para el cron, `vencido` = pendiente auto-expirado; en el uso coloquial "vencido" suele referirse a un aprobado cuya hora ya pasó (esos llevan editor de observación post-vuelo, §8).
- **Bloqueo de slot:** `slotsTomados` considera ocupado todo lo que no esté `cancelado` ni `vencido` (es decir, pendiente y aprobado ocupan el slot).
- **Sesión:** timeout por inactividad de 10 minutos (`startSessionTimeout`/`resetSessionTimer`). La sesión se persiste en `sessionStorage` y se restaura al recargar (`restoreSession`).

## 7. Autenticación y acceso

### Login de alumno (`loginAlumno`)
Acepta email o nombre de usuario (autodetecta por la `@`). Verificación: primero **Firebase Auth** si la cuenta está migrada (`authMigrado`), con **fallback a la password en texto plano**. Si no está migrada y la clave plana coincide, crea la cuenta de Auth automáticamente (migración perezosa, `authMigrarAlumno`). Luego: chequea `estado_login` (suspendido bloquea), `estado` (pendiente_aprobacion → pantalla de espera) y `pass_temporal` (fuerza cambio de clave).

### Login de instructor (`loginInstructor`)
Busca por usuario; verifica contra Firebase Auth (email sintético `usuario@instructores.aeroclubriogrande.com.ar`) con fallback a clave plana y migración perezosa (`authMigrarInstructor`). `admin`/`administrador` quedan en clave plana (no migran). Respeta `pass_temporal`. Acceso de emergencia: `admin`/`admin123` siempre.

### Registro (`registrarAlumno`)
Crea el usuario en `/alumnos` (estado `pendiente_aprobacion`, rol `alumno`) y la cuenta de Auth (best-effort). Exige clave de mínimo 6 caracteres. Envía mails de registro y bienvenida.

### Cambio forzado de clave (`forzarCambioClave`)
Modal obligatorio cuando `pass_temporal:true`. Generalizado para alumnos e instructores (recibe el path de DB). Actualiza la clave plana y **sincroniza con Firebase Auth** (`updatePassword`) si hay sesión de Auth activa. El admin puede disparar este flag desde los modales de edición (campo de clave + check "Exigir cambio en el próximo ingreso"); dejar la clave vacía y tildar el check **no borra la clave actual** (fbUpdate hace merge).

### Olvidé mi contraseña (`olvideClave`)
Genera una clave temporal, la guarda con `pass_temporal:true` y la envía por mail. *(En la Fase 3 de seguridad este flujo migrará al reset propio de Firebase.)*

### Suspensión de acceso
`estado_login:'suspendido'` (toggle en el modal de editar usuario) bloquea el login con aviso de contactar administración.

### Estado de la migración a Firebase Auth
Modo **sombra**: Auth corre en paralelo, la clave plana es la red de seguridad y nadie puede quedar bloqueado.
- **Fase 1 ✅** — alumnos en modo sombra + migración perezosa (v5.67).
- **Fase 2 ✅** — instructores en modo sombra (v5.68); admin/administrador no migran.
- **Fase 3 (pendiente)** — flujos de contraseña (reset/cambio) sincronizados con Auth.
- **Fase 4 (pendiente)** — roles sobre `auth.uid` en `/users`.
- **Fase 5 (pendiente)** — endurecer reglas de Firebase.
- **Fase 6 (pendiente)** — eliminar `pass` en texto plano.
- **Prerrequisito hecho:** proveedor Email/Password habilitado en la consola.
- Nota: cuentas con clave <6 no migran (Auth exige 6); se resuelven con un reset.

## 8. Ciclo de vida de una reserva

1. **Solicitud** (`confirmarTurno`): el usuario elige avión, día y horario; se valida anticipación y colisión de slot. Queda `pendiente` (o `aprobado` si es piloto en avión que no es LV-OAD).
2. **Aprobación** (modal de detalle, `abrirModal` → botón APROBAR): solo instructor real. Setea `aprobado_por`/`instructor`, audita y manda mail de confirmación (`mailAprob`). Si el instructor abre un pendiente de LV-OAD para un horario en el que **no** declaró disponibilidad, el modal muestra un cartel ámbar de aviso **antes** de aprobar (proactivo, v5.78/v5.81), para no pisar la disponibilidad de otro instructor; igual puede aprobar. Si el turno tiene `sinInstructor:true` (piloto cargó un día sin instructor disponible, v5.94), el modal muestra junto al estado un badge rosa **"sin instructor"**.
3. **Cancelación**: por el usuario (`cancelarTurnoAlumno`, con motivo opcional) o por instructor/admin (modal, motivo obligatorio). Setea `cancelado_por`, `obs_cancelacion`, audita y manda mail (`mailCancel`). Cualquier instructor real (o admin) puede cancelar cualquier aprobado (v5.86).
4. **Liberar turno** (solo en turnos aprobados futuros, `abrirModal`): devuelve el turno a `pendiente` sin cancelarlo, limpia `instructor`/`aprobado_por`, audita (`liberacion_turno`) y avisa al alumno (`mailLiberacion`). **Solo el instructor que aprobó, o admin/administrador, puede liberar.**
5. **Vencimiento**: pasa a `vencido` al pasar su horario (barrido perezoso del cliente) y, para los **pendientes**, también por el cron server-side antes del vuelo (§6, §21).
6. **Observación post-vuelo** (v5.87/v5.88): en un turno ya pasado que tuvo aprobador (estado `aprobado` o `vencido` con `aprobado_por`/`instructor`), el modal muestra un editor para cargar `obs_post` (ej. "no se voló por meteo"). Editable por el instructor que aprobó o admin; se audita (`obs_post_turno`) y se ve en la fila OBS. Los `vencido` del cron (pendientes nunca aprobados, sin aprobador) **no** muestran editor.

## 9. Pantallas y navegación

- **Login** (`switchLoginTab`): pestañas Ingresar / Registrarme / Inst-Admin / Olvidé.
- **Pendiente de aprobación**: pantalla de espera para usuarios nuevos.
- **Usuario (alumno/piloto)** — pestañas (`showAlumnoTab`):
  - *Mis Turnos*: historial con filtro por estado (`setHistFiltro`) y orden (`toggleHistOrden`); cancelar turnos propios.
  - *Pedir Turno*: selector de avión (pilotos), grilla de días (`renderDaysGrid`, con feriados), slots disponibles (`renderSlots`), confirmación. En LV-OAD los slots se filtran por disponibilidad de instructor **para alumnos**; el **piloto** ve además los slots sin instructor, marcados en rosa con tooltip aclaratorio, y puede reservarlos igual (v5.94).
  - *Mi Perfil*: datos personales (`guardarDatosPerfil`) y cambio de contraseña (`cambiarClave`).
- **Instructor** — pestañas (`showInstTab`):
  - *Reservas*: estadísticas + calendario (vista LISTA de 21 días y vista SEMANA). **Banner de turnos pendientes** (v5.71/v5.72): cartel ámbar pulsante a nivel de pantalla que avisa "HAY N TURNOS PENDIENTES DE APROBACIÓN"; aparece al ingresar y en tiempo real, **persiste mientras haya pendientes** (sin botón de cerrar), el botón VER TURNOS lleva a Reservas/LISTA (`irAPendientes`), y desaparece solo cuando no quedan pendientes.
  - *Seguimiento*: aprobaciones/cancelaciones por instructor (`renderSeguimiento`), **agrupadas por día** con encabezado de fecha en español largo (v5.73); selector de instructor para admin.
  - *Mi Disponibilidad*: grilla semanal de slots de LV-OAD + toggle de vacaciones.
  - *Configuración*: subpestañas (§10).
  - *Mi Perfil*: nombre y contraseña del instructor.

### Calendario de reservas
- **Vista LISTA** (`renderTodasReservas`): 21 días (7 pasados + 7 actuales + 7 futuros), filtro por avión, columnas con feriados, reservas pasadas tachadas.
- **Vista SEMANA** (`renderVistaSemana`): semana navegable, color por aeronave, leyenda.
- **Color rosa "sin instructor"** (v5.94): en ambas vistas, los turnos de piloto en LV-OAD con `sinInstructor:true` se muestran en rosa/fucsia (clase `.cal-block.sin-instructor`, variable `--pink`) **solo mientras están `pendiente`**; el tooltip agrega "SIN INSTRUCTOR". Al aprobarse pasan al verde de aprobado normal.
- **Línea de hora actual** (`dibujarLineaHoraActual`): punto + hora; **fija (sticky)** al hacer scroll lateral (v5.66); se actualiza cada minuto.
- **Botón HOY** (`centrarHoy`): recentra la grilla en el día de hoy con scroll suave (v5.66).

## 10. Configuración (subpestañas, `showCfgTab`)

- **Horarios**: por avión y día de semana (`renderHorariosConfig`, `toggleHorario`, `marcarTodas`); toggle de avión activo/inactivo (`toggleActivoAvion`). administrador = solo lectura. Incluye vista de disponibilidad de instructores (solo admin, solo lectura): selector por instructor + grilla de 30 días paginada (`renderAdmDisp`).
- **Bloq.Aviones**: calendario mensual por avión para bloquear días (`renderCalMes`, `toggleBloqueoDia`); muestra feriados.
- **Instructores**: alta (`agregarInstructor`, exige clave ≥6), edición (`abrirModalInst`/`guardarInstructor`, con check de cambio forzado), baja, y toggle de vacaciones por instructor para admin (`toggleVacacionesAdmin`). El alta y la edición incluyen **email** (para el recordatorio, §20) y **celular**; la lista marca "⚠ sin mail" a quienes no tengan email. El propio instructor también puede cargar/editar su email y celular desde Mi Perfil (`guardarNombreInstructor`).
- **Usuarios**: lista con filtros (texto/rol/estado); aprobar (`aprobarUsuario`, asigna rol) o rechazar (`rechazarUsuario`) pendientes; editar (`abrirModalAlumno`/`guardarAlumno`: nombre, tel, rol, estado_login, reseteo de clave + cambio forzado); eliminar.
- **Consultas**: búsqueda de turnos por usuario/fechas/avión/estado (`ejecutarConsulta`), estadísticas, exportar CSV (`exportarCSV`), y edición de reserva (`abrirModalCqReserva`/`guardarCqReserva`, solo admin; la opción "Aprobado" está deshabilitada para no-instructores).
- **Auditoría**: tabla paginada con filtros (`cargarAuditoria`/`filtrarAuditoria`/`renderAuditoria`); borrado por fila solo para `admin` (`borrarAuditoria`). **Excluye** los registros `rol:'fpl'`.
- **Audit. FPL** (solo `admin`, no `administrador`, v5.84): lista los registros que escribe `fpl.html` al generar un PDF (`rol:'fpl'`), con columnas fecha del vuelo (dd/mm/aaaa, v5.85)/origen/destino/hora/tiempo de vuelo/matrícula/comandante (+ registrado y usuario). Separada de la auditoría de turnos: nada se mezcla.
- **Sistema** (solo admin/administrador): Backup (`ejecutarBackup`) y Restore (`iniciarRestore`/`confirmarRestore`) de la base en JSON; **Zona Peligrosa** (solo `admin`): borrado masivo de turnos por estado y rango (`previewBorrar`/`ejecutarBorrar`).

## 11. Disponibilidad de instructores y vacaciones

- **Mi Disponibilidad** (`renderDisponibilidad`): grilla semanal navegable (hasta 30 días adelante) de los slots de LV-OAD; el instructor tilda los horarios en que puede dar vuelo (`dispToggle`, guardado inmediato en `/disponibilidad`). Si desmarca un slot con turno aprobado, ese turno se cancela y avisa al alumno. Solo los slots marcados quedan visibles para reserva en LV-OAD **para alumnos**; los **pilotos** pueden reservar slots sin instructor igualmente (v5.94, ver §4 y §6).
- **Vacaciones** (`toggleVacaciones` / `renderToggleVacaciones`): suspende la disponibilidad del instructor sin borrar los datos; el admin puede gestionar la de otros (`toggleVacacionesAdmin`). Los instructores en vacaciones se excluyen del filtro de slots visibles.

## 12. Auditoría

Registro de eventos (`registrarAuditoria`): login (éxito/fallo/bloqueado), registro, alta/aprobación/cancelación/liberación de turnos, vencimiento automático (`vencimiento_turno`), observación post-vuelo (`obs_post_turno`), habilitar/deshabilitar avión, bloquear/desbloquear día, aprobación/rechazo de usuario. El detalle de un `alta_turno` de piloto en LV-OAD sin instructor disponible incluye el sufijo "— SIN INSTRUCTOR" (v5.94). Patrón fetch-al-abrir (no tiempo real). Filtros por tipo, texto y fecha. El desplegable "Tipo de evento" incluye liberación de turnos, bloqueo/desbloqueo de días (v5.74) y vencimiento de turnos (v5.82); el mapa `ACCION_LABEL` traduce las acciones a etiquetas legibles. La pantalla de turnos **excluye** los registros `rol:'fpl'` (v5.83), que tienen su propia vista "Audit. FPL" (§10, §22).

## 13. Email (EmailJS)

**Dos cuentas de EmailJS en juego:**

1. **Cuenta del club** (`service_8yqlptz`, una sola key/`init()`) — la usa la app web (navegador) para sus templates: cancelación (`mailCancel`), reset (`mailReset`), registro + bienvenida (`mailNuevoRegistro`), confirmación de turno (`mailAprob`), liberar turno (`mailLiberacion`). Está en el **límite de 6 templates** del plan gratuito.

2. **Cuenta vieja de Gmail** (`dcamargo70@gmail.com`; `service_yeb4aqb`, public key `TzbSjqDNPjTGSGdzN`) — hospeda el template **"Recordatorio Instructor"** (`template_8awr1zd`) que usa el cron server-side (§20), porque la cuenta del club ya no tenía cupo de templates. El recordatorio le llega al instructor **desde dcamargo70@gmail.com** (default email de esa cuenta). Vars del template: `instructor_email` (To), `instructor_nombre`, `alumno_nombre`, `turno_hora`, `matricula_avion`, `name` (From Name), `email` (Reply-To = mail del club).

> **CRÍTICO (navegador):** `emailjs.createInstance()` NO existe en SDK v4 — usar `emailjs.init(key)` directo antes de cada `send()`.
> **CRÍTICO (server-side):** para enviar desde el cron (no-browser) hay que usar la **API REST** de EmailJS con la **private key** (accessToken) y tener habilitada en esa cuenta la opción "API for non-browser applications". Ver §20.

## 14. Integraciones externas

- **API de feriados argentinos:** `https://api.argentinadatos.com/v1/feriados/{año}` (`getFeriados`, con caché). Marca feriados en grillas y calendarios.
- (La meteorología METAR/TAF de SAWE vive en el archivo aparte `fpl.html`, no en turnos.html.)

## 15. Helpers y convenciones de código

- **Firebase:** `fbGet`, `fbSet`, `fbUpdate` (merge), `fbPush`, `fbRemove`; `ek(email)` convierte email a clave; `rArr(data)` convierte objeto Firebase a array con `key`.
- **Fechas/horas:** siempre en hora local (`new Date(y, m-1, d, ...)`), nunca parseo de ISO, para evitar el corrimiento de zona horaria (Argentina UTC-3). `fmtFechaLinda` (fecha larga en español), `fmtDate`, `horasParaDia`.
- **UI:** `showScreen`, `showErr`/`showOk`/`hideEl`, `val`, `clearVal`, `setLoading`.
- **Modales:** patrón con clase CSS `.open`; **deben estar a nivel raíz**, fuera de los contenedores de pantalla, o se vuelven invisibles durante el login.
- **Caches locales** (se resetean al logout): `_reservasCache`, `_alumnosCache`, `_misTurnosData`, `_auditoriaCache`, `_cqResultados`, `_dispEstado`.
- **Suscripción en tiempo real:** `onValue` sobre `/reservas` (`suscribirReservas`); `/alumnos` para el banner de pendientes (`suscribirPendientes`).
- **Estilo:** fuentes Share Tech Mono + Rajdhani + Orbitron; tema oscuro/claro; colores cian `#00c8d4` y ámbar `#f0a500`.

## 16. Inventario de funciones (qué hace cada una)

### Núcleo / helpers
- `fbGet/fbSet/fbUpdate/fbPush/fbRemove` — acceso a Firebase RTDB.
- `ek` — email → clave de Firebase. `rArr` — objeto → array con key. `genPass` — clave aleatoria de 8.
- `showScreen/showErr/showOk/hideEl/val/clearVal/setLoading` — utilidades de UI.
- `sanitizarHoras` — filtra horas con formato válido `HH:MM`.
- `registrarAuditoria` — escribe un evento en `/auditoria`.

### Auth / login / sesión
- `loginAlumno`, `loginInstructor`, `registrarAlumno`, `olvideClave`, `logout`, `switchLoginTab`.
- `authEmailInstructor` — email sintético del instructor. `authMigrarAlumno`/`authMigrarInstructor` — crean/reusan la cuenta de Auth (migración perezosa).
- `forzarCambioClave` — modal obligatorio de cambio de clave + sync con Auth.
- `guardarSesion`/`restoreSession` — persistencia de sesión. `startSessionTimeout`/`stopSessionTimeout`/`resetSessionTimer` — timeout por inactividad.
- `setNavUser` — barra superior con usuario.
- `esInstructorReal`/`esAdminRO`/`esAdminROHorarios` — chequeos de permisos.

### Reglas / fechas
- `getDays`, `getDiasSemana`, `getLunesDeSemana`, `dispLunes`, `admDispBase` — rangos de días.
- `fmtDate`, `fmtLabel`, `fmtFechaLinda`, `dispFmtFecha`, `dispFmtDisplay`, `cqToISO`, `cqToDisplay` — formateo de fechas.
- `ok12h`, `ok1h`, `okAnticipacion`, `puedeAlumnoCancelar`, `turnoYaPaso` — reglas de tiempo.
- `horasParaDia`, `diasBloqueadosParaAvion`, `avionesParaRol` — config aplicada.
- `getFeriados` — feriados (API + caché).

### Reserva (lado usuario)
- `initAvionSelector`, `seleccionarAvion`, `renderDaysGrid`, `selectDay`, `renderSlots`, `slotsConInstructorParaDia` (helper que devuelve el Set de horas con instructor disponible un día; reusado por `renderSlots`, `selectSlot` y `confirmarTurno`, v5.94), `slotsTomados`, `selectSlot`, `actualizarLabelRango`, `cancelarSeleccion`, `confirmarTurno`.
- `showAlumnoTab`, `renderMisTurnos`, `setHistFiltro`, `toggleHistOrden`, `cancelarTurnoAlumno`, `pedirMotivoCancelacion`, `guardarDatosPerfil`, `cambiarClave`.

### Reserva (lado instructor) / calendario
- `renderTodasReservas`, `buildCalAvionTabs`, `setCalFiltroAvion`, `setCalVista`, `semanaNav`, `renderVistaSemana`, `scrollCalAHoy`, `centrarHoy`, `dibujarLineaHoraActual`.
- `irAPendientes` — botón del banner de turnos pendientes: lleva a Reservas/LISTA (el banner persiste hasta que no queden pendientes).
- `abrirModal`/`cerrarModal` — modal de detalle del turno (aprobar/cancelar/liberar).
- `vencerTurnosPendientes` — vencimiento perezoso. `suscribirReservas`/`suscribirPendientes` — listeners.

### Seguimiento
- `renderSeguimiento` — lista de aprobaciones/cancelaciones **agrupada por día** (encabezado de fecha en español largo, sin columna FECHA por fila). `setSegPeriodo`, `poblarSelectorSeg`, `segAprobadorDe`, `segCanceladorDe`.

### Configuración
- `loadConfig`, `saveConfigAvion`, `renderConfig`, `showInstTab`, `showCfgTab`, `buildCfgAvionTabs`.
- Horarios: `selectCfgAvion`, `selectDowTab`, `renderHorariosConfig`, `toggleHorario`, `marcarTodas`, `renderToggleAvion`, `toggleActivoAvion`.
- Días: `selectDiasAvion`, `renderCalMes`, `mesAnterior`, `mesSiguiente`, `toggleBloqueoDia`.
- Instructores: `renderInstructoresList`, `agregarInstructor`, `eliminarInstructor`, `abrirModalInst`/`cerrarModalInst`/`guardarInstructor`, `toggleVacacionesAdmin`, `guardarNombreInstructor`, `cambiarClaveInstructor`.
- Usuarios: `renderAlumnosList`, `filtrarAlumnosList`, `aprobarUsuario`, `rechazarUsuario`, `abrirModalAlumno`/`cerrarModalAlumno`/`guardarAlumno`, `renderEstadoLoginToggle`, `toggleEstadoLoginModal`, `eliminarAlumno`.
- Consultas: `ejecutarConsulta`, `limpiarConsulta`, `cqFmtFecha`, `abrirModalCqReserva`/`cerrarModalCqReserva`/`guardarCqReserva`/`eliminarCqReserva`, `exportarCSV`.
- Auditoría: `cargarAuditoria`, `filtrarAuditoria`, `limpiarFiltrosAuditoria`, `auditGoPage`, `renderAuditoria`, `borrarAuditoria`.
- Sistema: `ejecutarBackup`, `iniciarRestore`, `confirmarRestore`, `cancelarRestore`, `getBorrarFiltro`, `filtrarParaBorrar`, `previewBorrar`, `ejecutarBorrar`.

### Disponibilidad
- `renderDisponibilidad`, `dispNavSemana`, `dispToggle`, `mostrarDispOk`, `renderToggleVacaciones`, `toggleVacaciones`.
- `renderAdmDispSelector`, `admDispSelectInst`, `admDispNav`, `renderAdmDisp` — vista admin de disponibilidad.

### Email
- `mailCancel`, `mailReset`, `mailNuevoRegistro`, `mailAprob`, `mailLiberacion`.

### Tema
- `toggleTheme`.

## 17. Versionado (regla crítica)

**Siempre incrementar la versión en cada modificación.** En `turnos.html` se actualiza en **dos lugares**: (1) el bloque comentario del header, (2) el string `.hero-sub`. El `_meta.version` del backup (`ejecutarBackup`) es la versión del esquema de backup (independiente del archivo, no se confunde).

## 18. Estado actual y trabajo pendiente

- **Seguridad / Auth:** Fases 1 y 2 hechas (modo sombra). Se dejó **decantar la migración** (los usuarios migran al entrar). Próximo paso de desarrollo: Fase 3 (flujos de contraseña a Auth), que destraba endurecer reglas (Fase 5) y sacar el texto plano (Fase 6). Las 3 cuentas de instructor que tenían clave <6 (`fherlein`, `scarrizo`, `sdelarminat`) **ya fueron reseteadas a 6+** (2026-06-17); no quedan claves cortas pendientes.
- **Caché que frenaba la migración (resuelto 2026-06-17):** varios usuarios entraban pero no aparecían en Auth porque su navegador servía una **copia vieja cacheada** del `turnos.html` (sin el código de migración) — no era un bug de la app (se verificó el código). Se resolvió con la Cache Rule `no-store` (ver §2): ahora cada navegador carga el HTML fresco, corre el código actual y se dispara la migración perezosa. Los ya pegados a una copia vieja se destraban con una recarga forzada o cuando su caché vence. La decantación continúa con esto resuelto.
- **Recordatorio automático a instructores (HECHO, 2026-06-19):** proceso server-side (GitHub Actions cron). Avisa al instructor por mail ~12 h antes del turno. Ver §20.
- **Auto-vencimiento de pendientes (HECHO, 2026-06-19):** proceso server-side (cron `vencimiento_turnos.py`, §21). Vence los pendientes que nadie confirmó dentro de la ventana previa al vuelo (6 h antes), libera el slot y avisa al alumno por mail. El mismo cron purga los borradores FPL de externos. Ver §21.
- **Backlog de features:** disponibilidad para LV-ART/LV-MPH; bitácora de horas de vuelo; asistencia/no-show; estado de mantenimiento de aeronaves; lista de espera; dashboard para la comisión; aviso al instructor de nuevas solicitudes.

## 19. Limitaciones conocidas

- **Reglas de Firebase abiertas** y **passwords en texto plano** (en proceso de resolución vía Auth). La `apiKey`/URL son públicas por diseño; la seguridad depende de Auth + reglas, no de ocultarlas.
- **Proceso de servidor:** la app web corre en el navegador y el barrido de vencimiento del cliente es **perezoso**. Hay **dos procesos server-side** programados (GitHub Actions): el recordatorio a instructores (§20) y el vencimiento de pendientes + purga FPL (§21). Las tareas que deben correr sin nadie online se resuelven por esa vía.
- **Sin queries SQL:** todo el filtrado es del lado del cliente. (Para consultas ad-hoc en SQL hay un script aparte que vuelca Firebase a SQLite — `fb_to_sqlite.py`.)
- **Caché:** Cloudflare no cachea HTML en el borde; la staleness era por caché del navegador, resuelta con la Cache Rule `no-store` sobre `.html` (§2). Con eso los deploys se propagan al instante; igual conviene verificar la versión en pantalla tras subir. (Nota: una pestaña ya abierta no se actualiza sola; loguear/desloguear es client-side y no recarga el HTML — hay que recargar la página.)

## 20. Recordatorio automático a instructores (proceso server-side / cron)

Aviso por mail al instructor **~12 horas antes** de cada turno aprobado, para que organice el día. Corre fuera de la app web, como proceso programado, así funciona aunque no haya nadie con la página abierta.

### Archivos (en el repo)
- **`recordatorio_instructor.py`** (raíz del repo) — el script. Solo usa librería estándar de Python (urllib, json, datetime); no requiere pip.
- **`.github/workflows/recordatorio-instructor.yml`** — el workflow de GitHub Actions que lo dispara.

### Disparo
- `schedule: cron "*/15 * * * *"` — cada 15 min. GitHub corre en **UTC** y puede atrasarse varios minutos; no importa, lo cubre la ventana + la marca anti-duplicado.
- `workflow_dispatch` con input **`dry_run`** (`1` = prueba sin enviar ni marcar; `0` = real). Útil para probar desde la pestaña Actions.

### Lógica del script
1. Lee `/reservas` e `/instructores` de Firebase por REST (reglas de lectura abiertas).
2. Calcula "ahora" en hora local (UTC-3 fijo; Argentina no usa horario de verano).
3. Selecciona turnos `estado==aprobado` **con instructor asignado** (`aprobado_por`/`instructor`) cuyo vuelo cae dentro de las próximas `REMIND_HOURS` horas y que **no** tengan `recordatorio_inst_enviado`.
4. Resuelve el email del instructor: matchea su `nombre` (de `aprobado_por`) contra `/instructores` y toma el campo `email`. Si no tiene email, lo **saltea** (lo loguea).
5. Envía el mail vía **API REST de EmailJS** (server-side, con private key).
6. Marca la reserva con `recordatorio_inst_enviado:true` + `recordatorio_inst_ts` (PATCH REST) para no repetir.
7. Si hubo errores de envío, el job termina en fallo (rojo) para que se note en Actions.

> La marca anti-duplicado hace que salga **un solo mail por turno**. Al **liberar** un turno se limpia la marca (v5.76), así un turno reaprobado por otro instructor vuelve a generar aviso.

### EmailJS (cuenta dcamargo70 — ver §13)
- Service `service_yeb4aqb`, template `template_8awr1zd` ("Recordatorio Instructor"), public key `TzbSjqDNPjTGSGdzN`.
- Requiere tener habilitada en esa cuenta la opción **"API for non-browser applications"** (Account → Security) y usar la **private key** como `accessToken` en la llamada REST.
- El mail sale **desde dcamargo70@gmail.com** (default email de la cuenta). Reply-To = mail del club (`CLUB_EMAIL`).

### Configuración (variables `env:` del workflow)
- Públicas (en el `.yml`): `FIREBASE_DB_URL`, `EMAILJS_SERVICE_ID` (`service_yeb4aqb`), `EMAILJS_TEMPLATE_ID` (`template_8awr1zd`), `EMAILJS_PUBLIC_KEY` (`TzbSjqDNPjTGSGdzN`), `CLUB_EMAIL` (`administracion@aeroclubriogrande.com`), `REMIND_HOURS` (**12**), `TZ_OFFSET` (`-3`), `DRY_RUN`.
- **Secreta** (GitHub → Settings → Secrets and variables → Actions): `EMAILJS_PRIVATE_KEY` = private key de la cuenta dcamargo70. **Nunca** va en el repo.
- **`REMIND_HOURS` se ajusta editando esa línea del `.yml`** (no toca el `.py`): cambiar el número y commitear.

### Prerrequisito operativo
Cada instructor debe tener su **`email` cargado** (Mi Perfil, o el admin desde el modal de instructor). Los que no lo tengan se saltean sin aviso; en la lista de instructores aparecen marcados con **"⚠ sin mail"**. `admin`/`administrador` no necesitan email (no aprueban turnos).

## 21. Vencimiento de pendientes + purga FPL (proceso server-side / cron)

Segundo proceso server-side, independiente del recordatorio (§20). Hace caer los turnos **pendientes** que nadie confirmó a tiempo y, de paso, limpia los borradores FPL de externos.

### Archivos (en el repo)
- **`vencimiento_turnos.py`** (raíz del repo) — solo librería estándar de Python (urllib, json, datetime); no requiere pip.
- **`.github/workflows/vencimiento-turnos.yml`** — el workflow que lo dispara (cron periódico + `workflow_dispatch` con input `dry_run`).

### Lógica del script (cada corrida)
1. Lee `/reservas` por REST.
2. Busca **pendientes** cuyo vuelo cae dentro de las próximas `EXPIRE_HOURS` (o ya pasó) y que **tuvieron ventana de confirmación** (creados antes del mojón de vencimiento). Esos "se cayeron": ningún instructor los confirmó a tiempo.
3. Los marca `estado:'vencido'` (la app ya entiende ese estado y libera el slot).
4. Si el vuelo todavía es **futuro**, avisa al **alumno** por mail (EmailJS REST server-side, private key). Si el vuelo ya pasó, solo limpia (no manda mail).
5. Registra el vencimiento en `/auditoria` (`vencimiento_turno`).
6. **Purga FPL:** borra de los buckets `/fpl/externo*` los borradores con más de `FPL_PURGE_HOURS` de creados (`fecha_creacion`).

> **Por qué `EXPIRE_HOURS` debe ser < 12:** el alumno puede reservar hasta 12 h antes del vuelo. Si el vencimiento disparara a las 12 h (o más) antes, un turno pedido en el límite nacería ya vencido. Con 6 h queda una ventana (de las 12 h-antes a las 6 h-antes) para que un instructor confirme. Cuanto más chico el número, más tarde cae y más ventana hay.

### Configuración (variables `env:` del workflow)
- Públicas: `FIREBASE_DB_URL`, `EMAILJS_SERVICE_ID`, `EMAILJS_TEMPLATE_ID` (template del aviso de vencimiento al alumno, cuenta dcamargo70), `EMAILJS_PUBLIC_KEY`, `CLUB_EMAIL` (Reply-To), `EXPIRE_HOURS` (default **6**, debe ser <12), `TZ_OFFSET` (`-3`), `FPL_PURGE_HOURS` (default **1**), `DRY_RUN`.
- **Secreta:** `EMAILJS_PRIVATE_KEY` (accessToken de la cuenta dcamargo70, en GitHub Secrets).

## 22. Generador de planes de vuelo (`fpl.html`)

Página aparte (no es turnos.html) para armar **planes de vuelo OACI** (casillas 7 a 19) y generar el PDF para presentar. Comparte la misma Firebase (`turnos-lv-oad`) vía REST.

### Funcionamiento
- **PDF:** estampa los valores por coordenadas (`RECTS`) sobre una base **rasterizada** del formulario oficial argentino, así renderiza idéntico en cualquier visor sin depender de fuentes embebidas. Incrusta la firma a mano alzada. Texto del usuario en **MAYÚSCULAS**.
- **Horas:** se cargan en **local (UTC-3)** y el sistema convierte/muestra **UTC** (casilla 13); `DOF` (18) se calcula en UTC (maneja el cambio de día). El comandante (19 C/) es un campo explícito, separado de quien arma el plan.
- **OACI deprecados (`OACI_DEPRECADOS={SAWO}`, v3.16):** los aeródromos ya no vigentes se fuerzan a `ZZZZ` + detalle en el ítem 18 (DEP/ DEST/ ALTN/). Caso EAU (Est. Aeronaval Ushuaia): venía como SAWO y se estampaba mal. La lista vive en `fpl.html` (sobrevive a la regeneración del JSON de aeródromos). La sustitución IATA→OACI vigente (EZE→SAEZ, etc.) no cambia.

### Borradores y usuario
- Borradores por usuario en `/fpl/{USUARIO}`. Resuelve el usuario leyendo la sesión de turnos (`sessionStorage 'lvoad-session'`) **en la misma pestaña**; usa el `username` real (v3.12, requiere que turnos incluya `username` en la sesión — v5.91). Si no hay sesión (link externo) usa un bucket efímero `/fpl/externo_{uuid}` por sesión, que el cron (§21) purga al cabo de 1 h.
- **PDF combinado (v3.10):** se tildan varios borradores y se genera **un** PDF con todos (una hoja por plan, en orden cronológico). La descarga la hace el usuario; el sistema no envía.

### Aeronaves
- El selector **mergea** las 3 del club (`/aeronaves`, global) + las **personales** del usuario (`/aeronaves_usuario/{USUARIO}`, solo el dueño las ve). Al elegir una personal precarga todo (FPL + SPL/comandante/remarks).
- Editor "EDIT AIRCRAFT" (v3.14) con tabs **FPL** (7/9/10/15/18) y **SPL** (Item 19: R/ S/ J/ D/ A/ C/ N/), incluido **D/ botes neumáticos**. Externos no guardan aeronaves.

### Auditoría FPL
- Cada PDF generado escribe un registro en `/auditoria` con `rol:'fpl'` (`accion: 'fpl_*'`). `fpl.html` **solo escribe**; la **vista** está en turnos.html (sub-pestaña "Audit. FPL", solo admin — §10). La auditoría de turnos excluye estos registros.

### Integración con turnos.html
- Botón **"Plan de Vuelo"** (clase `.fpl-link`, pill ámbar) en las tab-bars de alumno e instructor (v5.89/v5.90).
- Meteorología **METAR/TAF de SAWE** (CheckWX) vive en `fpl.html`, no en turnos.html.
