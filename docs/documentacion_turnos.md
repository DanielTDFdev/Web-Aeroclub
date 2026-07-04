# Documentación técnica — Sistema de Turnos (turnos.html)

**Aeroclub Río Grande (SAWE) — Tierra del Fuego, Argentina**
Versión documentada: **turnos.html v6.52** · **fpl.html v3.22** · **portal-alumno.html v1.13** · **peso-balance.html v1.4** · Fecha: 2026-07-03

> Documento de referencia: describe qué hace cada parte del sistema. Mantener actualizado cuando se agreguen funciones.
> Además de la app web (`turnos.html`) hay un generador de planes de vuelo (`fpl.html`, §22), un **portal de alumno** (`portal-alumno.html`, §23), una **calculadora de peso y balance** (`peso-balance.html`, §24) y **dos procesos server-side** en GitHub Actions: el recordatorio a instructores (§20) y el vencimiento de pendientes + purga de borradores FPL (§21).

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
- `cancelado_por` — nombre de quien canceló. **Desde v6.27** se graba explícitamente también cuando cancela el propio alumno (`cancelarTurnoAlumno`) — antes ese flujo solo guardaba `cancelado_rol:'alumno'` sin tocar este campo, y el modal/Seguimiento terminaban mostrando como "canceló" al instructor que había *aprobado* el turno originalmente (el campo `instructor` quedaba sin actualizar). Para turnos cancelados por el alumno **antes** de v6.27, el modal usa como fallback `r.nombre` cuando `cancelado_rol==='alumno'` (no hace falta migrar datos viejos).
- `aprobado_por` — nombre de quien aprobó (para Seguimiento y para la fila APROBÓ del modal, §16). Solo existe si el turno se aprobó **después** del 2026-06-15 (v5.52, cuando se agregó como campo explícito) por una vía que lo escribe (aprobación normal desde el modal, o "Cargar Turno" con instructor seleccionado). Turnos más viejos, o aprobados por una vía que no lo setea, no lo tienen — no hay forma confiable de reconstruirlo retroactivamente si además el turno fue cancelado después (el campo `instructor` se sobreescribe en la cancelación).
- `cancelado_rol` (v6.19) — `'alumno'` | `'instructor'`, explícito y estructurado, escrito en los 4 lugares donde un turno pasa a `cancelado` (el propio usuario, los 2 botones del instructor, y la cancelación automática al desmarcar disponibilidad con turno aprobado en conflicto). Pensado para poder cuantificar cancelaciones sin depender de parsear el texto libre de `obs_cancelacion`. **No es retroactivo**: los turnos cancelados antes de v6.19 no lo tienen — se migraron a mano con un script one-shot (ver nota más abajo), pero cualquier reserva cancelada *después* de esta documentación y *antes* de subir v6.19 al repo seguiría sin el campo hasta que se vuelva a correr una migración.
- `obs_cancelacion` — motivo de cancelación, texto libre con prefijo fijo del sistema (`"Cancelado por el usuario"` o `"Cancelado por el instructor — {motivo}"`). Desde v6.18 el motivo del instructor sale de un **select tabulado** (ver §8) en vez de ser 100% libre, aunque el campo en sí sigue siendo string.
- `recordatorio_inst_enviado` — `true` si el recordatorio automático al instructor ya se envió para este turno (evita reenvíos). Lo escribe el cron (§20). Se limpia al liberar el turno.
- `recordatorio_inst_ts` — timestamp ISO del envío del recordatorio.
- `obs_post` — observación post-vuelo cargada por el instructor que aprobó (o admin) sobre un turno ya pasado (ej. "no se voló por meteo"). Editable desde el modal; se audita como `obs_post_turno` (v5.87/v5.88).
- `cargado_por` (v6.08) — presente solo si el turno se creó con la herramienta "Cargar Turno" (staff, no el propio alumno): nombre de quien lo cargó. Distingue una carga manual de una reserva hecha por el propio alumno/piloto.
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

### `/manuales/{pushKey}`
Catálogo de manuales del portal de alumno (§23): `{titulo,categoria,tipo:'archivo'|'link',url,descripcion,fecha,autor}`. Para `tipo:'archivo'`, `url` es la ruta relativa dentro de `/manuales/` del repo (no una URL completa).

### `/quizzes/{quizKey}`
Cuestionarios del portal de alumno (§23): `{titulo,categoria,activo,obligatorio,preguntas:[{enunciado,opciones:[...],correcta}],autor,creado}`.

### `/intentos_quiz/{userKey}/{quizKey}/{pushKey}`
Cada intento de un alumno en un cuestionario: `{respuestas,detalle:[{enunciado,elegida,correcta_texto,acierto}],puntaje,total,fecha,nombre}`.

### `/notas_alumno/{emailKey}`
Nota libre del instructor/admin sobre un alumno (visible para el alumno, solo lectura): `{texto,autor,fecha}`.

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
- **Vencimiento:** un turno `pendiente` o `aprobado` pasa a `vencido` cuando su fecha/hora ya pasó (`vencerTurnosPendientes`, barrido **perezoso** del lado del cliente — corre en `suscribirReservas`/`onValue` para **cualquier** usuario logueado con la pantalla abierta, y **escribe** el cambio en Firebase, no es solo visual). **Además**, un proceso server-side (cron `vencimiento_turnos.py`, §21) vence los **pendientes** que nadie confirmó dentro de la ventana previa al vuelo (default 6 h antes) y le avisa al alumno por mail, sin depender de que haya alguien con la app abierta. Ojo con la ambigüedad del término: para el cron, `vencido` = pendiente auto-expirado; en el uso coloquial "vencido" suele referirse a un aprobado cuya hora ya pasó (esos llevan editor de observación post-vuelo, §8). Esta ambigüedad tuvo consecuencias reales en la cuota semanal (ver punto siguiente) hasta v6.52.
- **Cuota semanal de turnos (v6.35, fix de conteo en v6.52):** un **alumno** (no piloto, no instructor) no puede tener más de **3 turnos activos por semana calendario (lunes a domingo)**. Función `turnosRestantesSemana(ds, data, email)` (extraída a standalone en v6.50): dado un email y una fecha `ds`, calcula el lunes/domingo de esa semana (`diffLunes = dow===0 ? -6 : 1-dow`, o sea el domingo cae en la MISMA semana que el lunes-sábado que lo precede, no en la semana siguiente) y cuenta cuántos turnos de ese email caen en ese rango.
  - **Cuentan:** `pendiente`, `aprobado`, `cancelado` con `cancelado_rol==='alumno'` (o el campo ausente — evita el loop de pedir→cancelar→pedir), y `vencido` **con** `aprobado_por` seteado (turno que fue aprobado y luego venció solo por el paso del tiempo — es un vuelo real, cuenta igual que un aprobado).
  - **No cuentan:** `cancelado` con `cancelado_rol==='instructor'` (no es decisión del alumno, no se lo penaliza), y `vencido` **sin** `aprobado_por` (nunca fue aprobado — es el caso del cron, genuinamente no voló).
  - **Bug de conteo real (encontrado 2026-07-03, corregido en v6.52):** el filtro original (desde v6.35) excluía **todo** `estado==='vencido'` sin mirar `aprobado_por`. Como `vencerTurnosPendientes` vuelve `vencido` cualquier turno aprobado apenas pasa su horario (ver punto anterior), un alumno con vuelos ya aprobados y volados en la semana los veía "liberarse" de la cuota apenas alguien —cualquier usuario, no necesariamente el propio alumno— abría la app después de esa hora. Resultado confirmado en producción: un alumno con 5 turnos reales en una misma semana (3 `vencido`-con-aprobador + 1 `aprobado` + 1 `pendiente`), cuando el sistema solo "veía" 2 y dejaba pedir de más. Diagnosticado con un dump directo de `/reservas.json` filtrado por email (no hay forma de leer Firebase desde este entorno de trabajo, así que la verificación la hizo Daniel a mano vía consola del navegador). Corregido agregando el chequeo de `aprobado_por` al filtro.
  - **Chequeo atómico (v6.51):** el chequeo de cuota (y el de slot-tomado) vivía como un `get()` de `/reservas` seguido de un `fbPush` separado — dos pasos no atómicos. Un doble-submit (doble tap, dos pestañas, reintento de red) podía leer el mismo conteo en ambos pedidos antes de que cualquiera escribiera, y los dos pasaban el chequeo. `confirmarTurno` ahora usa `runTransaction()` de Firebase sobre `/reservas`: el chequeo y la escritura del turno nuevo son una sola operación atómica (el SDK reintenta el callback si el dato cambió durante el intento). Cubre el flujo normal de "Pedir Turno"; **no** cubre `guardarTurnoManual` ("+ CARGAR TURNO"), que no chequea cuota por diseño (§16), ni compensa que las reglas de Firebase sigan abiertas (`{".read":true,".write":true}`, §18) — una escritura directa a la REST API sin pasar por la UI sigue sin ninguna validación de servidor.
  - **Tooltip informativo (v6.50):** el `cal-block` de cada turno ya tomado, en LISTA y SEMANA, agrega al `title` (solo si el turno es de rol alumno) el saldo de esa semana: `— Saldo: X/3 turnos esta semana` o el aviso de límite alcanzado. Función `tooltipSaldo(r, todosLosDatos)`, reusa `turnosRestantesSemana` pasándole el email del alumno dueño del turno (no el del usuario logueado que está mirando el calendario).
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
   - **Carga manual por staff (v6.08/6.09/6.10, `abrirModalCargarTurno`/`guardarTurnoManual`):** botón **"+ CARGAR TURNO"** (verde) en la tab Reservas, para turnos coordinados por teléfono/en persona sin que el alumno tenga que loguearse. A diferencia de `confirmarTurno`: NO exige anticipación mínima ni que haya disponibilidad de instructor publicada para ese horario (es carga directa, no reserva contra horarios abiertos); SÍ sigue chequeando colisión de slot (`slotsTomados`). El estado lo decide un dropdown **"Aprobado por"** con instructores reales (excluye admin/administrador y de vacaciones) — primera opción **"Sin aprobación (queda pendiente)"** como default; si se elige un instructor, el turno queda `aprobado` con `instructor`/`aprobado_por` = ese nombre (no necesariamente el admin que está cargando) y dispara `mailAprob`. El registro queda con un campo extra `cargado_por` para distinguirlo de una reserva hecha por el propio alumno. Visible para instructor, admin y administrador (administrador es solo-lectura únicamente en los checkboxes de horarios, no en esto). **FIX v6.09:** el modal quedó anidado en el HTML dentro de `instructor-config` (display:none salvo esa sub-tab activa) — mismo bug de fondo que el modal de motivo de cancelación (v5.97); se rescata a `document.body` al cargar la página. **FIX de paso (v6.10):** `mailAprob()` tenía hardcodeado `session.nombre` como "instructor" del mail — incorrecto si el admin carga un turno a nombre de otro instructor; ahora acepta un instructor explícito como segundo argumento opcional.
2. **Aprobación** (modal de detalle, `abrirModal` → botón APROBAR): solo instructor real. Setea `aprobado_por`/`instructor`, audita y manda mail de confirmación (`mailAprob`). **Cartel ámbar de aviso** (proactivo, v5.78/v5.81, corregido en v5.96): al abrir un pendiente de LV-OAD, el modal muestra un cartel ámbar **antes** de aprobar solo si el instructor que aprueba **no** declaró disponibilidad en ese horario **y además otro instructor (no de vacaciones) sí lo declaró** — es decir, únicamente cuando realmente estaría pisando un turno que otro tenía previsto cubrir. Lee `/disponibilidad/{fecha}` completo y nombra al/los instructor(es) con ese horario. Si nadie más lo tiene (p. ej. un turno de piloto `sinInstructor:true`), no se muestra. Igual puede aprobar. Si el turno tiene `sinInstructor:true` (v5.94), el modal muestra junto al estado un badge rosa **"sin instructor"**.
3. **Cancelación**: por el usuario (`cancelarTurnoAlumno`, con motivo opcional) o por instructor/admin (modal, motivo obligatorio). Setea `cancelado_por`, `cancelado_rol`, `obs_cancelacion`, audita y manda mail (`mailCancel`). Cualquier instructor real (o admin) puede cancelar cualquier aprobado (v5.86). **v6.01**: como el motivo es opcional para el alumno, cerrar el modal de motivo (botón CANCELAR, Escape o click afuera) ya NO aborta la cancelación — antes sí lo hacía, y el alumno veía el turno seguir activo aunque había confirmado cancelarlo. Al terminar, `alert()` explícito de éxito ("Turno cancelado correctamente") o de error si falla la escritura en Firebase. **v6.02**: el botón "CANCELAR" del modal de motivo era ambiguo para el alumno (sugería volver atrás, pero ya no aborta nada desde v6.01) — ahora el botón y el texto del modal son dinámicos: instructor (motivo obligatorio) ve "CANCELAR" y el aviso de que se manda mail; alumno (motivo opcional) ve "OMITIR MOTIVO" y aclara que el turno se cancela igual. **v6.18**: para el **instructor** (motivo obligatorio), el modal agrega un **select de motivo tabulado** (Meteorología Adversa / Técnica de Aeronave / Reprogramación Turno / Solicitado por Alumno / Aeródromo Cerrado / Otros) — al elegir una categoría fija, el textarea se autocompleta y queda read-only; con "Otros" el textarea se habilita para texto libre como antes. El motivo del alumno sigue siendo textarea libre sin el select. **v6.19**: nuevo campo `cancelado_rol` (ver §3). **v6.20**: se invierte el orden de los pasos previos a cancelar (instructor): antes salía primero el `confirm()` genérico y recién después el modal de motivo, lo cual obligaba a comprometerse a cancelar antes de saber qué motivo poner; ahora sale primero el modal de motivo, y solo si se eligió uno válido aparece el `confirm()` final, que ahora incluye el motivo en el texto ("¿Cancelar el turno de X? Motivo: Y"). El flujo del alumno no cambió (ya tenía este orden, sin `confirm()` previo).
4. **Liberar turno** (solo en turnos aprobados futuros, `abrirModal`): devuelve el turno a `pendiente` sin cancelarlo, limpia `instructor`/`aprobado_por`, audita (`liberacion_turno`) y avisa al alumno (`mailLiberacion`). **Solo el instructor que aprobó, o admin/administrador, puede liberar.**
5. **Borrado directo (solo `admin`, v6.11/6.12):** botón **"🗑 BORRAR (admin)"** en el modal de detalle del turno, visible **únicamente** para el usuario literal `admin` (no instructor, no `administrador`). A diferencia de cancelar/liberar, borra el nodo de `/reservas` directo con `fbRemove`, sin importar su estado, y **a propósito sin dejar ningún registro en `/auditoria`** — pedido explícito para poder limpiar turnos de prueba sin ensuciar la auditoría real. `confirm()` previo; sin vuelta atrás (no hay forma de recuperar un turno borrado así). Visualmente diferenciado a propósito de CANCELAR: fila propia separada por un divisor punteado, caption de advertencia ("Zona de administrador — borrado definitivo, sin auditoría"), estilo sólido invertido (fondo rojo lleno, ancho completo) en vez del contorno que usa CANCELAR — para que sean inconfundibles a simple vista, dado que es el botón más peligroso del sistema.
6. **Vencimiento**: pasa a `vencido` al pasar su horario (barrido perezoso del cliente) y, para los **pendientes**, también por el cron server-side antes del vuelo (§6, §21).
7. **Observación post-vuelo** (v5.87/v5.88): en un turno ya pasado que tuvo aprobador (estado `aprobado` o `vencido` con `aprobado_por`/`instructor`), el modal muestra un editor para cargar `obs_post` (ej. "no se voló por meteo"). Editable por el instructor que aprobó o admin; se audita (`obs_post_turno`) y se ve en la fila OBS. Los `vencido` del cron (pendientes nunca aprobados, sin aprobador) **no** muestran editor.

> **Migración de `cancelado_rol` en registros viejos:** al sumar el campo (v6.19) se corrió una sola vez un script (`normalizar_cancelado_rol.py`, fuera del repo) que infirió el rol de las 24 reservas canceladas previas a partir de `cancelado_por` (vacío → alumno, con nombre → instructor). Una auditoría posterior (`auditar_cancelado_rol.py`, cruzando contra el prefijo de `obs_cancelacion`, señal más confiable) encontró 4 casos donde `cancelado_por` había quedado vacío en el registro original aunque la cancelación sí fue del instructor (versión vieja del código con esa inconsistencia) — se corrigieron con un tercer script (`corregir_cancelado_rol_sospechosas.py`) que usa `obs_cancelacion` como fuente de verdad cuando contradice a `cancelado_por`. Los tres scripts son ejecuciones puntuales, no forman parte del repo ni de ningún cron.

## 9. Pantallas y navegación

- **Login** (`switchLoginTab`): pestañas Ingresar / Registrarme / Inst-Admin / Olvidé.
- **Pendiente de aprobación**: pantalla de espera para usuarios nuevos.
- **Usuario (alumno/piloto)** — pestañas (`showAlumnoTab`):
  - *Mis Turnos*: historial con filtro por estado (`setHistFiltro`) y orden (`toggleHistOrden`); cancelar turnos propios.
  - *Pedir Turno*: selector de avión (pilotos), grilla de días (`renderDaysGrid`, con feriados), slots disponibles (`renderSlots`), confirmación. En LV-OAD los slots se filtran por disponibilidad de instructor **para alumnos**; el **piloto** ve además los slots sin instructor, marcados en rosa con tooltip aclaratorio, y puede reservarlos igual (v5.94). La fecha mostrada junto a "Horarios disponibles" sale en `dd/mm/aaaa` (v6.00).
  - *Mi Perfil*: datos personales (`guardarDatosPerfil`) y cambio de contraseña (`cambiarClave`).
  - **Link "🎓 Portal Alumno"** (v5.98/v5.99): pill en `.links-row`, arriba de la tab-bar, junto al de "✈ Plan de Vuelo". Visible para `rol:'alumno'`, instructor, admin y administrador; **oculto para `rol:'piloto'`** (vía `setNavUser`, id `al-portal-link`). Lleva a `portal-alumno.html` (§23), que valida su propio acceso leyendo la misma `sessionStorage 'lvoad-session'` — no requiere backend adicional en turnos.html más allá de este link.
- **Instructor** — pestañas (`showInstTab`):
  - *Reservas*: estadísticas + calendario (vista LISTA de 21 días y vista SEMANA). **Banner de turnos pendientes** (v5.71/v5.72): cartel ámbar pulsante a nivel de pantalla que avisa "HAY N TURNOS PENDIENTES DE APROBACIÓN"; aparece al ingresar y en tiempo real, **persiste mientras haya pendientes** (sin botón de cerrar), el botón VER TURNOS lleva a Reservas/LISTA (`irAPendientes`), y desaparece solo cuando no quedan pendientes.
  - *Seguimiento*: aprobaciones/cancelaciones por instructor (`renderSeguimiento`), **agrupadas por día** con encabezado de fecha en español largo (v5.73); selector de instructor para admin.
  - *Mi Disponibilidad*: grilla semanal de slots de LV-OAD + toggle de vacaciones.
  - *Configuración*: subpestañas (§10).
  - *Mi Perfil*: nombre y contraseña del instructor.

### Calendario de reservas
- **Vista LISTA** (`renderTodasReservas`): rango de **17 días** (3 pasados + 7 actuales + 7 futuros — acortado de 21 días en v6.15, sigue mostrando la semana actual completa más la próxima semana completa), filtro por avión, columnas con feriados, reservas pasadas tachadas.
- **Vista SEMANA** (`renderVistaSemana`): semana navegable, leyenda.
- **Color unificado por ESTADO** (v6.15/6.16): hasta v6.14, SEMANA coloreaba por **aeronave** (LV-OAD=cyan, LV-ART=ámbar, LV-MPH=verde) y excluía directamente los cancelados del filtro; LISTA coloreaba por **estado** (pendiente=ámbar, aprobado=verde, cancelado=rojo tachado, vencido=violeta tachado, sin instructor=rosa punteado, turno de piloto=azul) y sí mostraba los cancelados tachados — generaba ambigüedad porque los colores de aeronave se pisaban con los de estado. Desde v6.16 **ambas vistas usan exactamente el mismo esquema de color por estado** (mismo bloque de código), SEMANA ya no filtra cancelados, y la matrícula del avión sigue visible en chiquito dentro de cada bloque (suficiente para identificarlo sin necesitar color). Las clases CSS `.oad`/`.art`/`.mph`/`.pil-aprob` quedan en desuso (sin borrar del CSS).
- **Color rosa "sin instructor"** (v5.94): en ambas vistas, los turnos de piloto en LV-OAD con `sinInstructor:true` se muestran en rosa/fucsia (clase `.cal-block.sin-instructor`, variable `--pink`) **solo mientras están `pendiente`**; el tooltip agrega "SIN INSTRUCTOR". Al aprobarse pasan al color de aprobado.
- **Color azul "piloto aprobado"** (v5.95): los turnos `aprobado` de rol `piloto` (sea aprobación automática en aviones que no son LV-OAD, o manual por instructor en LV-OAD) se distinguen del verde de alumno. Tooltip agrega "turno de piloto". No afecta badges del lado usuario ni la lógica de aprobación.
- **Borde verde + estrella "coincide con tu disponibilidad"** (v6.13): cuando el instructor logueado tiene un turno **pendiente** que cae en un día/horario que coincide con su propia disponibilidad cargada (`/disponibilidad/{fecha}/{su_usuario}`), el recuadro muestra un borde verde brillante + ★ y el tooltip aclara "Coincide con tu disponibilidad". Solo para instructores reales (`esInstructorReal()`); puramente informativo, no cambia nada de la lógica de aprobación.
- **Tooltip "quién puede aprobar"** (v6.14): el tooltip de un turno **pendiente** agrega quiénes podrían aprobarlo *en este momento*, según la disponibilidad vigente de **todos** los instructores ese día/horario (excluye los de vacaciones) — "— Puede aprobar: Nicolas, Mariano" o "— Nadie con disponibilidad cargada para ese horario". Cálculo en vivo al momento de ver la pantalla; puede no coincidir con quién termine aprobando si la disponibilidad cambia entremedio. Helper `cargarDisponibilidadCompleta` (reemplaza al de v6.13) trae ambas cosas con una sola lectura de `/disponibilidad/{fecha}`.
- **Toggle "Ocultar cancelados"** (v6.21/6.22): switch tipo slider (clase `.cancel-toggle`, mismo patrón visual que el selector de tema) al lado de los botones LISTA/SEMANA. Filtra los turnos `estado:'cancelado'` en **ambas** vistas con un único control compartido. Estado persistido en `localStorage` (`lvoad-ocultar-cancelados`), igual patrón que el tema — queda activado entre sesiones en el mismo navegador. No afecta los contadores de arriba (Pendientes/Aprobadas/Total) ni ninguna otra pantalla (Seguimiento, Consultas, Mis Turnos del alumno siguen mostrando todo). v6.22 lo pasó de checkbox simple a slider por accesibilidad/usabilidad en mobile, manteniendo un `<input type="checkbox">` real oculto por dentro (no un `div` con `onclick`) para conservar foco de teclado y compatibilidad con lectores de pantalla.
- **Selector LISTA/SEMANA como toggle switch** (v6.45, fix v6.47): reemplaza los dos botones tipo tab que tenía antes (`.cal-vista-tab`, ahora en desuso — ver §19) por un toggle de dos posiciones (id `cal-vista-toggle`), mismo patrón visual que el de "Disponibilidad de Instructores" en Configuración (knob deslizante + labels que cambian de color, ver §10). `setCalVista(vista)` anima el knob y los labels; si se llama **sin argumento** calcula el toggle internamente (`calVista==='lista'?'semana':'lista'`). **v6.45 introdujo el mismo bug ya documentado para `admDispToggleVista` en v6.43:** el `onclick` inline leía la variable de módulo `calVista` directamente desde el HTML, y las variables `let`/`const` de un `script type="module"` no quedan expuestas al scope global que usan los atributos `onclick` (a diferencia de las funciones, expuestas vía `window.foo=`). v6.47 lo corrigió con el mismo patrón que ya se había aplicado en Disponibilidad: `onclick="setCalVista()"` sin argumento.
- **Segmented control con pastilla deslizante — Seguimiento (v6.46):** el selector TODOS/PRÓXIMOS/PASADOS (`setSegPeriodo`) pasó de tabs con clase `.active` independiente por botón a un contenedor `.seg-switch` con una pastilla (`.seg-switch-pill`) que se desliza detrás del botón activo. La posición se calcula en JS (`segSwitchUpdatePill`, vía `offsetLeft`/`offsetWidth` del botón activo, no porcentajes fijos — así "PRÓXIMOS", más ancho que los otros dos, no rompe el cálculo). Se recalcula al abrir la pestaña Seguimiento (`requestAnimationFrame`, porque el contenedor está `display:none` hasta ese momento y `offsetLeft` daría `0`) y en `window resize`.
- **Línea de hora actual** (`dibujarLineaHoraActual`): punto + hora; **fija (sticky)** al hacer scroll lateral (v5.66); se actualiza cada minuto. **Bug de dos causas (v6.48/6.49, 2026-07-03):** al refactorizar el toggle LISTA/SEMANA (v6.45/6.47) la línea dejó de aparecer en SEMANA. v6.48 agregó la llamada faltante a `dibujarLineaHoraActual()` al final de `renderVistaSemana()` pero no alcanzó — dos causas de fondo: (1) la función hacía `document.querySelector('.cal-wrap')`, que agarra siempre el **primer** `.cal-wrap` del DOM (el de LISTA) sin importar qué vista esté activa — corregido pasando el wrap correcto como parámetro desde cada caller; (2) el chequeo de "hoy está en el rango visible" usaba `getDays()` (helper de LISTA), pero SEMANA arma su rango con `getLunesDeSemana()+semanaOffset` — podía no encontrar "hoy" y no dibujar nada. Reemplazado por chequear la existencia de `th.today-col`, que existe en ambas vistas.
- **Botón HOY** (`centrarHoy`): recentra la grilla en el día de hoy con scroll suave (v5.66).

### Botón SALIR
Cierra sesión (`logout`): borra `sessionStorage 'lvoad-session'` y vuelve a la pantalla de login. Desde v6.17 lleva el ícono **⏻** en el `top-nav` (estética que se implementó primero en `portal-alumno.html` v1.13 y de ahí se llevó a turnos.html, mismo comportamiento en los dos lugares: cierra sesión y redirige).

## 10. Configuración (subpestañas, `showCfgTab`)

- **Horarios**: por avión y día de semana (`renderHorariosConfig`, `toggleHorario`, `marcarTodas`); toggle de avión activo/inactivo (`toggleActivoAvion`). administrador = solo lectura. Incluye vista de disponibilidad de instructores (solo admin, solo lectura): selector por instructor + grilla de 30 días paginada (`renderAdmDisp`).
- **Bloq.Aviones**: calendario mensual por avión para bloquear días (`renderCalMes`, `toggleBloqueoDia`); muestra feriados.
- **Instructores**: alta (`agregarInstructor`, exige clave ≥6), edición (`abrirModalInst`/`guardarInstructor`, con check de cambio forzado), baja, y toggle de vacaciones por instructor para admin (`toggleVacacionesAdmin`). El alta y la edición incluyen **email** (para el recordatorio, §20) y **celular**; la lista marca "⚠ sin mail" a quienes no tengan email. El propio instructor también puede cargar/editar su email y celular desde Mi Perfil (`guardarNombreInstructor`).
- **Usuarios**: lista con filtros (texto/rol/estado); aprobar (`aprobarUsuario`, asigna rol) o rechazar (`rechazarUsuario`) pendientes; editar (`abrirModalAlumno`/`guardarAlumno`: nombre, tel, rol, estado_login, reseteo de clave + cambio forzado); eliminar.
- **Consultas**: búsqueda de turnos por usuario/fechas/avión/estado (`ejecutarConsulta`), estadísticas, exportar CSV (`exportarCSV`), y edición de reserva (`abrirModalCqReserva`/`guardarCqReserva`, solo admin; la opción "Aprobado" está deshabilitada para no-instructores).
- **Auditoría**: tabla paginada con filtros (`cargarAuditoria`/`filtrarAuditoria`/`renderAuditoria`); borrado por fila solo para `admin` (`borrarAuditoria`). **Excluye** los registros `rol:'fpl'`.
- **Audit. FPL** (solo `admin`, no `administrador`, v5.84): lista los registros que escribe `fpl.html` al generar un PDF (`rol:'fpl'`), con columnas fecha del vuelo (dd/mm/aaaa, v5.85)/origen/destino/hora/tiempo de vuelo/matrícula/comandante (+ registrado y usuario). Separada de la auditoría de turnos: nada se mezcla. **Borrado por fila** (v6.25, `borrarAuditoriaFpl`): mismo patrón visual y de `confirm()` que `borrarAuditoria`, pensado para limpiar registros de prueba sin tocar Firebase a mano. Reusa la gate de admin que ya tenía la función.
- **Sistema** (solo admin/administrador): Backup (`ejecutarBackup`) y Restore (`iniciarRestore`/`confirmarRestore`) de la base en JSON; **Zona Peligrosa** (solo `admin`): borrado masivo de turnos por estado y rango (`previewBorrar`/`ejecutarBorrar`).

## 11. Disponibilidad de instructores y vacaciones

- **Mi Disponibilidad** (`renderDisponibilidad`): grilla semanal navegable (hasta 30 días adelante) de los slots de LV-OAD; el instructor tilda los horarios en que puede dar vuelo (`dispToggle`, guardado inmediato en `/disponibilidad`). Si desmarca un slot con turno aprobado, ese turno se cancela y avisa al alumno. Solo los slots marcados quedan visibles para reserva en LV-OAD **para alumnos**; los **pilotos** pueden reservar slots sin instructor igualmente (v5.94, ver §4 y §6). La disponibilidad se carga **hora por hora** (no por rango) dentro de cada día — el nodo resultante es un array de strings de hora por fecha/usuario.
- **Vacaciones** (`toggleVacaciones` / `renderToggleVacaciones`): suspende la disponibilidad del instructor sin borrar los datos; el admin puede gestionar la de otros (`toggleVacacionesAdmin`). Los instructores en vacaciones se excluyen del filtro de slots visibles.
- **Ayuda visual en el calendario (v6.13/6.14):** el calendario de Reservas (vistas LISTA y SEMANA) enriquece los bloques de turnos **pendientes** con dos fuentes de info:
  - **★ borde verde** (`mi-disp`): si el turno pendiente cae en un día/horario que el instructor **logueado** tiene marcado como disponible, el bloque muestra un borde verde brillante + estrellita (★). Solo aplica a `esInstructorReal()`, no a admin/administrador.
  - **Tooltip "Puede aprobar: ..."** (`tipQuienesPueden`): para cualquier turno pendiente, el tooltip lista los nombres de pila de **todos** los instructores con disponibilidad cargada ese día/horario, excluyendo los que están de vacaciones. Si no hay ninguno dice "Nadie con disponibilidad cargada para ese horario". Es el estado de disponibilidad **en vivo al momento de ver la pantalla** — no refleja quién terminó aprobando si la disponibilidad cambió después.
  - Implementado en `cargarDisponibilidadCompleta(fechas)`: hace **una sola lectura** de `/disponibilidad/{fecha}` por fecha y de `/instructores` una vez, y sirve simultáneamente la info propia (para ★) y la de todos (para el tooltip).

## 12. Auditoría

Registro de eventos (`registrarAuditoria`): login (éxito/fallo/bloqueado), registro, alta/aprobación/cancelación/liberación de turnos, vencimiento automático (`vencimiento_turno`), observación post-vuelo (`obs_post_turno`), habilitar/deshabilitar avión, bloquear/desbloquear día, aprobación/rechazo de usuario. El detalle de un `alta_turno` de piloto en LV-OAD sin instructor disponible incluye el sufijo "— SIN INSTRUCTOR" (v5.94). Patrón fetch-al-abrir (no tiempo real). Filtros por tipo, texto y fecha. El desplegable "Tipo de evento" incluye liberación de turnos, bloqueo/desbloqueo de días (v5.74) y vencimiento de turnos (v5.82); el mapa `ACCION_LABEL` traduce las acciones a etiquetas legibles. La pantalla de turnos **excluye** los registros `rol:'fpl'` (v5.83), que tienen su propia vista "Audit. FPL" (§10, §22).

## 13. Email (EmailJS)

**Dos cuentas EmailJS, separadas a propósito (no por límite de templates):**

- **Cuenta A** — public key **`0IoRm88tZQepkHAdK`**, servicio **`service_8yqlptz`** ("Admin Aeroclub"), Gmail Connect real `aeroclubgra@gmail.com` (no `dcamargo70@gmail.com` — el *Service ID* del dashboard es un campo de texto libre/renombrable, no refleja qué Gmail está conectado; hay que mirar el campo "Gmail Connect" del servicio para saber con certeza). **Usada SOLO por las funciones que corren desde el navegador** (`turnos.html`): cancelación (`mailCancel`), reset (`mailReset`), registro + bienvenida (`mailNuevoRegistro`), confirmación de turno (`mailAprob`), liberar turno (`mailLiberacion`).

- **Cuenta B** — misma cuenta EmailJS que `dcamargo70@gmail.com`, public key `TzbSjqDNPjTGSGdzN`. Servicio activo: **`service_48kzt1s`** ("Instructores ACRG"), Gmail Connect: `instructoresacrg@gmail.com` (cuenta nueva creada el 2026-06-29 para separar los envíos automáticos del Gmail personal de Daniel). Antes usaba `service_yeb4aqb` conectado a `dcamargo70@gmail.com` directamente. **Usada SOLO por los dos crons server-side** (§20 y §21): template "Recordatorio Instructor" (`template_8awr1zd`) y "Turno Caído" (`template_4wja6rq`). Los templates viven en esta cuenta — **no en la Cuenta A**. La separación es **deliberada**: así las copias de los mails automáticos del cron no quedan mezcladas en la carpeta de Enviados del Gmail personal de Daniel.

> **Episodio de confusión (2026-06-28 a 2026-06-29) — revertido:** el 2026-06-28 se "consolidó" por error el `service_id`+`public_key` de los dos workflows de cron a la Cuenta A, pensando que era una migración pendiente. Pero los templates `template_8awr1zd` y `template_4wja6rq` **nunca existieron en la Cuenta A** — viven solo en la Cuenta B. Resultado: el cron tiraba `HTTP 400 — template ID not found`. Antes de eso, además, las dos cuentas tenían el toggle **"Allow EmailJS API for non-browser applications"** apagado (default de EmailJS), lo que daba `HTTP 403` independientemente del template — ese fue el primer síntoma reportado ("no salió el mail al instructor"). **Diagnóstico final (2026-06-29):** se activó el toggle en ambas cuentas (Account → Security) y se revirtieron los dos `.yml` a `service_yeb4aqb` + `TzbSjqDNPjTGSGdzN`, que es la config correcta y definitiva. Confirmado con envío real exitoso el 2026-06-29 09:22.
>
> **Regla para no repetir esto:** las **plantillas viven a nivel de cuenta** de EmailJS, no por servicio — no alcanza con cambiar el `service_id` en un `.yml`, el template tiene que existir en la cuenta de ese `service_id` o tira 400. Si en algún momento se decide migrar de verdad los templates de cron a la Cuenta A, hay que **recrearlos a mano** ahí (no se "mueven" solos) y recién después cambiar `EMAILJS_SERVICE_ID`/`EMAILJS_PUBLIC_KEY`/`EMAILJS_TEMPLATE_ID` juntos, los tres a la vez. El secret `EMAILJS_PRIVATE_KEY` en GitHub es por cuenta: hoy tiene que ser la private key de la **Cuenta B** (Account → API Keys, logueado con `dcamargo70@gmail.com`).

> **CRÍTICO (navegador):** `emailjs.createInstance()` NO existe en SDK v4 — usar `emailjs.init(key)` directo antes de cada `send()`.
> **CRÍTICO (server-side):** para enviar desde el cron (no-browser) hay que usar la **API REST** de EmailJS con la **private key** (accessToken) y tener habilitada en esa cuenta puntual la opción **"Allow EmailJS API for non-browser applications"** (Account → Security) — viene **apagada por default** y rompe el envío con 403 sin avisar de otra forma más que ese mensaje en el log. Ver §20.

> **Incidente nuevo (2026-06-30) — token OAuth de Gmail vencido en Cuenta A:** dejó de llegar el mail de confirmación de turno (`mailAprob`/`template_4nsseoo`). El log de EmailJS mostraba `Service Error — Gmail_API: Invalid grant. Please reconnect your Gmail account` para `service_8yqlptz`. **No era un problema de código, template ni variables** — el token OAuth que EmailJS usa para mandar como `aeroclubgra@gmail.com` había caducado/sido revocado (Google invalida estos tokens por motivos varios: cambio de contraseña de la cuenta Gmail, revocación manual de apps de terceros, o expiración por inactividad). **Solución:** EmailJS dashboard → Email Services → editar `service_8yqlptz` → **Disconnect** (sí, hay que desconectar primero, no alcanza con "reconectar" sobre la conexión rota) → Connect Gmail de nuevo → en la pantalla de consentimiento de Google, **tildar explícitamente el checkbox del scope de envío** (si no se tilda a propósito, Google a veces deja avanzar igual pero sin el permiso, y EmailJS tira `412 — Request had insufficient authentication scopes` recién al mandar el mail de prueba). Resuelto reconectando una segunda vez prestando atención al checkbox. **Este es un modo de falla que puede repetirse** — los tokens OAuth de Gmail son frágiles por naturaleza; si vuelve a pasar, revisar primero el log de EmailJS (dice `Gmail_API: Invalid grant` explícitamente, no hay que adivinar) antes de tocar código.

## 14. Integraciones externas

- **API de feriados argentinos:** `https://api.argentinadatos.com/v1/feriados/{año}` (`getFeriados`, con caché). Marca feriados en grillas y calendarios.
- (La meteorología METAR/TAF de SAWE vive en el archivo aparte `fpl.html`, no en turnos.html.)

## 15. Helpers y convenciones de código

- **Firebase:** `fbGet`, `fbSet`, `fbUpdate` (merge), `fbPush`, `fbRemove`; `ek(email)` convierte email a clave; `rArr(data)` convierte objeto Firebase a array con `key`.
- **Fechas/horas:** siempre en hora local (`new Date(y, m-1, d, ...)`), nunca parseo de ISO, para evitar el corrimiento de zona horaria (Argentina UTC-3). `fmtFechaLinda` (fecha larga en español), `fmtDate`, `horasParaDia`.
- **UI:** `showScreen`, `showErr`/`showOk`/`hideEl`, `val`, `clearVal`, `setLoading`.
- **Modales:** patrón con clase CSS `.open`; **deben estar a nivel raíz**, fuera de los contenedores de pantalla (`#alumno-screen`, `#instructor-screen`, etc.), o se vuelven invisibles cuando esa pantalla está oculta. **Caso real (v5.97):** `#modal-motivo-cancel` estaba anidado dentro de `#instructor-screen` en el HTML; funcionaba para instructor/admin (esa pantalla está visible) pero la cancelación de turno por parte del **alumno** no hacía nada visible — el modal se abría (clase `open` puesta) pero nunca se pintaba, porque un descendiente de un ancestro `display:none` no se renderiza aunque su propio `computedStyle` siga diciendo `flex`. Se resolvió "rescatando" el nodo al `<body>` con `appendChild` al cargar la página, en vez de reordenar el HTML grande. Si aparece un bug de "el modal no se ve pero tampoco tira error", revisar primero en qué `screen` está anidado en el HTML.
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
- `initAvionSelector`, `seleccionarAvion`, `renderDaysGrid`, `selectDay`, `renderSlots`, `slotsConInstructorParaDia` (helper que devuelve el Set de horas con instructor disponible un día; reusado por `renderSlots`, `selectSlot` y `confirmarTurno`, v5.94), `slotsTomados`, `turnosRestantesSemana(ds, data, email)` (v6.35, extraída a standalone en v6.50, fix de conteo en v6.52 — ver §6, Cuota semanal), `selectSlot`, `actualizarLabelRango`, `cancelarSeleccion`, `confirmarTurno` (v6.51: usa `runTransaction` en vez de leer-y-después-escribir).
- `showAlumnoTab`, `renderMisTurnos`, `setHistFiltro`, `toggleHistOrden`, `cancelarTurnoAlumno`, `pedirMotivoCancelacion`, `guardarDatosPerfil`, `cambiarClave`.

### Reserva (lado instructor) / calendario
- `renderTodasReservas`, `buildCalAvionTabs`, `setCalFiltroAvion`, `setCalVista`, `semanaNav`, `renderVistaSemana`, `scrollCalAHoy`, `centrarHoy`, `dibujarLineaHoraActual`.
- `irAPendientes` — botón del banner de turnos pendientes: lleva a Reservas/LISTA (el banner persiste hasta que no queden pendientes).
- `abrirModal`/`cerrarModal` — modal de detalle del turno (aprobar/cancelar/liberar). Cuando `estado==='cancelado'` (v6.27/6.31/6.32/6.33), muestra **las dos puntas** de la historia del turno: fila **CANCELÓ** (prioriza `cancelado_por`; fallback a `r.nombre` si `cancelado_rol==='alumno'` y es un registro previo a v6.27) y fila **APROBÓ** (vía `segAprobadorDe`, mismo helper que usa Seguimiento) — se muestran **siempre que haya dato**, aunque sea la misma persona en ambas puntas (decisión explícita de Daniel: prefiere ver las dos filas siempre antes que inferir "no dice nada, debe ser el mismo"). Cada fila lleva un ícono de color (👍 verde junto a APROBÓ, 👎 rojo junto a CANCELÓ) con el mismo lenguaje visual de las clases CSS verde/rojo del resto de la app.
- `tooltipAccion(r)` — sufijo del tooltip del calendario con quién aprobó/canceló. `tooltipSaldo(r, todosLosDatos)` (v6.50) — sufijo adicional con el saldo de cuota semanal del alumno dueño del turno (solo si `r.rol==='alumno'`), reusa `turnosRestantesSemana`.
- `abrirModalCargarTurno`/`cerrarModalCargarTurno`/`ctActualizarRango`/`guardarTurnoManual` (v6.08-6.10) — herramienta "Cargar Turno" (ver §8).
- `vencerTurnosPendientes` — vencimiento perezoso. `suscribirReservas`/`suscribirPendientes` — listeners.

### Seguimiento
- `renderSeguimiento` — lista de movimientos **agrupada por día** (encabezado de fecha en español largo, sin columna FECHA por fila). **v6.28:** antes un turno aprobado y luego cancelado generaba **dos filas** (una "APROBÓ", otra "CANCELÓ") con el mismo avión/hora/alumno, dando la falsa impresión de un turno duplicado. Ahora muestra **una fila por turno**, con la última acción relevante (cancelación si la hay, aprobación si no) — las estadísticas de arriba (cantidad de aprobaciones/cancelaciones por instructor) siguen contando ambas acciones por separado, sin cambios, porque ese cálculo se separó del armado de filas. **v6.29:** se sacó la columna ACCIÓN aparte (chip APROBÓ/CANCELÓ) por ser redundante con ESTADO ACTUAL en cancelaciones. **v6.30:** la nota inline que reemplazó a esa columna ahora nombra al **actor complementario real** (`_otroActor`: quien aprobó si la fila es de cancelación, o quien canceló si es de aprobación) — `"· aprobado por {Instructor}"` / `"· cancelado por {Instructor}"` — y no se muestra nada si la misma persona hizo las dos cosas. `setSegPeriodo`, `poblarSelectorSeg`, `segAprobadorDe`, `segCanceladorDe` (sin cambios de firma; `segCanceladorDe` ahora también devuelve cancelaciones del alumno, no solo de instructor, porque desde v6.27 `cancelado_por` se graba siempre).

### Configuración
- `loadConfig`, `saveConfigAvion`, `renderConfig`, `showInstTab`, `showCfgTab`, `buildCfgAvionTabs`.
- Horarios: `selectCfgAvion`, `selectDowTab`, `renderHorariosConfig`, `toggleHorario`, `marcarTodas`, `renderToggleAvion`, `toggleActivoAvion`.
- Días: `selectDiasAvion`, `renderCalMes`, `mesAnterior`, `mesSiguiente`, `toggleBloqueoDia`.
- Instructores: `renderInstructoresList`, `agregarInstructor`, `eliminarInstructor`, `abrirModalInst`/`cerrarModalInst`/`guardarInstructor`, `toggleVacacionesAdmin`, `guardarNombreInstructor`, `cambiarClaveInstructor`.
- Usuarios: `renderAlumnosList`, `filtrarAlumnosList`, `aprobarUsuario`, `rechazarUsuario`, `abrirModalAlumno`/`cerrarModalAlumno`/`guardarAlumno`, `renderEstadoLoginToggle`, `toggleEstadoLoginModal`, `eliminarAlumno`.
- Consultas: `ejecutarConsulta`, `limpiarConsulta`, `cqFmtFecha`, `abrirModalCqReserva`/`cerrarModalCqReserva`/`guardarCqReserva`/`eliminarCqReserva`, `exportarCSV`.
- Auditoría: `cargarAuditoria`, `filtrarAuditoria`, `limpiarFiltrosAuditoria`, `auditGoPage`, `renderAuditoria`, `borrarAuditoria`. Audit. FPL: `cargarAuditoriaFpl`, `borrarAuditoriaFpl` (v6.25).
- Sistema: `ejecutarBackup`, `iniciarRestore`, `confirmarRestore`, `cancelarRestore`, `getBorrarFiltro`, `filtrarParaBorrar`, `previewBorrar`, `ejecutarBorrar`.

### Disponibilidad
- `renderDisponibilidad`, `dispNavSemana`, `dispToggle`, `mostrarDispOk`, `renderToggleVacaciones`, `toggleVacaciones`.
- `renderAdmDispSelector`, `admDispSelectInst`, `admDispNav`, `renderAdmDisp` — vista admin de disponibilidad.

### Email
- `mailCancel`, `mailReset`, `mailNuevoRegistro`, `mailAprob(r, instructorNombre?)` (v6.10: segundo argumento opcional, default `session.nombre` — necesario para que el dropdown "Aprobado por" de Cargar Turno mande el mail con el instructor correcto, no con quien está logueado), `mailLiberacion`.

### Tema
- `toggleTheme`.

## 17. Versionado (regla crítica)

**Siempre incrementar la versión en cada modificación, en todos los archivos del sistema** (`turnos.html`, `fpl.html`, `portal-alumno.html`, `peso-balance.html`). En `turnos.html` se actualiza en **dos lugares**: (1) el bloque comentario del header, (2) el string `.hero-sub`. Los otros tres archivos usan el span `<span class="ver">vX.Y</span>` además del comentario del header. El `_meta.version` del backup (`ejecutarBackup`, solo en turnos.html) es la versión del esquema de backup (independiente del archivo, no se confunde).

## 18. Estado actual y trabajo pendiente

- **Seguridad / Auth:** Fases 1 y 2 hechas (modo sombra). Se dejó **decantar la migración** (los usuarios migran al entrar). Próximo paso de desarrollo: Fase 3 (flujos de contraseña a Auth), que destraba endurecer reglas (Fase 5) y sacar el texto plano (Fase 6). Las 3 cuentas de instructor que tenían clave <6 (`fherlein`, `scarrizo`, `sdelarminat`) **ya fueron reseteadas a 6+** (2026-06-17); no quedan claves cortas pendientes.
- **Caché que frenaba la migración (resuelto 2026-06-17):** varios usuarios entraban pero no aparecían en Auth porque su navegador servía una **copia vieja cacheada** del `turnos.html` (sin el código de migración) — no era un bug de la app (se verificó el código). Se resolvió con la Cache Rule `no-store` (ver §2): ahora cada navegador carga el HTML fresco, corre el código actual y se dispara la migración perezosa. Los ya pegados a una copia vieja se destraban con una recarga forzada o cuando su caché vence. La decantación continúa con esto resuelto.
- **Recordatorio automático a instructores (HECHO, 2026-06-19):** proceso server-side (GitHub Actions cron). Avisa al instructor por mail ~12 h antes del turno. Ver §20.
- **Auto-vencimiento de pendientes (HECHO, 2026-06-19):** proceso server-side (cron `vencimiento_turnos.py`, §21). Vence los pendientes que nadie confirmó dentro de la ventana previa al vuelo (6 h antes), libera el slot y avisa al alumno por mail. El mismo cron purga los borradores FPL de externos. Ver §21.
- **Portal de Alumno (HECHO, 2026-06-25/26):** página nueva `portal-alumno.html` con perfil, manuales, cuestionarios y flashcards. Ver §23. Banco de preguntas ANAC PPA cargado (318 preguntas) + mazo de 75 flashcards del LV-OAD.
- **Peso y Balance (HECHO, 2026-06-27):** página nueva `peso-balance.html`, calculadora con ábaco SVG, abierta a cualquier rol. Ver §24. **Pendiente:** confirmar peso vacío/brazo reales de LV-ART y LV-MPH (hoy son placeholders sin verificar).
- **Fix EmailJS crons (HECHO, 2026-06-29, confirmado con envío real):** proceso completo: (1) se detectó que el toggle "Allow non-browser apps" estaba apagado por default en ambas cuentas EmailJS → HTTP 403; (2) los workflows apuntaban al service id equivocado (Cuenta A en vez de Cuenta B) → HTTP 400 template not found; (3) se creó el servicio "Instructores ACRG" (`service_48kzt1s`, `instructoresacrg@gmail.com`) dentro de la Cuenta B para separar los envíos del cron del Gmail personal de Daniel; (4) ambos workflows actualizados a `service_48kzt1s`. Envío real confirmado el 2026-06-29 11:53. Ver §13/§20/§21.
- **Ayuda visual de disponibilidad en el calendario (HECHO, 2026-06-29, v6.13/6.14):** turnos pendientes que coinciden con la disponibilidad del instructor logueado muestran ★ borde verde; el tooltip de todos los pendientes lista los instructores que podrían aprobarlo en ese momento. Ver §11.
- **Vista LISTA: rango pasado reducido a 3 días (HECHO, 2026-06-29, v6.15):** antes mostraba 7 días pasados + 7 actuales + 7 futuros (21 días). Ahora 3 + 7 + 7 = 17 días.
- **Vista SEMANA: color por estado unificado con LISTA (HECHO, 2026-06-29, v6.16):** antes coloreaba por aeronave (cyan/ámbar/verde según LV-OAD/ART/MPH), lo que creaba ambigüedad con los colores de estado. Ahora ambas vistas usan el mismo esquema de color por estado. La matrícula del avión sigue visible en pequeño dentro de cada bloque. La leyenda al pie se actualizó a estados (incluyendo ★ Mi disponibilidad). Cancelados y vencidos ahora aparecen en SEMANA (antes se filtraban).
- **Botón SALIR con ícono ⏻ (HECHO, 2026-06-29/30, portal-alumno.html v1.13 → turnos.html v6.17):** estética implementada primero en el portal y llevada después a turnos.html, mismo botón en los dos lugares.
- **Botón BORRAR (admin) (HECHO, 2026-06-28, v6.11/6.12):** borrado directo de un turno (cualquier estado) sin pasar por auditoría, solo para el usuario `admin`, pensado para limpiar pruebas. Tratamiento visual a propósito opuesto a CANCELAR. Ver §8.
- **Motivo de cancelación tabulado + campo `cancelado_rol` (HECHO, 2026-06-30, v6.18/6.19/6.20):** el instructor ahora elige el motivo de un select con 6 categorías fijas (más "Otros" para texto libre), pensado para poder cuantificar cancelaciones a futuro. Se sumó el campo `cancelado_rol` (`alumno`/`instructor`) explícito en cada reserva cancelada, y se invirtió el orden del flujo de cancelación del instructor (motivo primero, confirmación después, con el motivo incluido en el texto de confirmación). Las 24 reservas canceladas previas a v6.19 se migraron con un script one-shot, con una corrección posterior de 4 casos donde la señal `cancelado_por` no era confiable (ver §8 y §3).
- **Toggle "Ocultar cancelados" (HECHO, 2026-06-30, v6.21/6.22):** switch tipo slider en la pantalla Reservas para filtrar visualmente los turnos cancelados del calendario (ambas vistas), persistido en `localStorage`. Ver §9.
- **Cargar Turno manual (HECHO, 2026-06-28):** herramienta para que instructor/admin cargue un turno a nombre de un alumno sin que se loguee (turnos.html v6.08-6.10). Ver §8.
- **Pasada mobile (HECHO, 2026-06-27/28):** `@media (max-width:680px)` aditivo en los cuatro archivos (turnos.html v6.06, fpl.html v3.21, portal-alumno.html v1.11) + red de seguridad `overflow-x:hidden` (turnos.html v6.07, fpl.html v3.22, portal-alumno.html v1.12, ver §19). **Pendiente de confirmación real en dispositivo** — sin acceso a un teléfono propio, todo esto se armó leyendo CSS; quedó pendiente verificar en mobile real tras cada entrega.
- **Backlog de features:** disponibilidad para LV-ART/LV-MPH (turnos.html); bitácora de horas de vuelo; asistencia/no-show; estado de mantenimiento de aeronaves; lista de espera; dashboard para la comisión; aviso al instructor de nuevas solicitudes; ampliar el banco de preguntas/flashcards del portal (otros temas además de PPA/LV-OAD); revisar las 66 preguntas de ANAC excluidas por depender de figuras; editor de mazos de flashcards para Archer/Lance; cargar peso vacío real de LV-ART/LV-MPH en peso-balance.html.
- **Auditoría FPL: borrado por fila (HECHO, 2026-06-30, v6.25):** botón "✕" por fila en la tabla de Audit. FPL, mismo patrón que la auditoría general, para limpiar registros de prueba.
- **FIX banner "versión vieja" — botón ACTUALIZAR no traía la versión nueva (HECHO, 2026-06-30, v6.26):** el botón hacía `location.reload()` a secas, que NO bypassea el caché de disco del navegador (a diferencia del `fetch` con `cache:'no-store'` que sí usa la *detección* de versión nueva). Resultado real reportado: había que apretar ACTUALIZAR 3-4 veces hasta que el caché local venciera solo. Ahora navega a `location.pathname+'?_v='+Date.now()` — el query string distinto fuerza al navegador a pedirlo de la red sí o sí.
- **FIX atribución de cancelación por el alumno (HECHO, 2026-06-30, v6.27):** `cancelarTurnoAlumno` no actualizaba ningún campo de "quién actuó" — el modal terminaba mostrando como "Canceló" al instructor que había *aprobado* el turno (el campo `instructor` quedaba intacto desde la aprobación). Ahora graba `cancelado_por` explícito con el nombre del alumno; el modal y `tooltipAccion` (calendario) lo priorizan, con fallback a `r.nombre` para turnos cancelados antes de este fix (no hace falta migrar datos).
- **Seguimiento de Turnos — una fila por turno + actor complementario nombrado (HECHO, 2026-06-30, v6.28/6.29/6.30):** ver detalle en §16 (Seguimiento). Reportado por Daniel como "turnos cancelados aparecen duplicados".
- **Modal de detalle — fila APROBÓ + íconos (HECHO, 2026-06-30, v6.31/6.32/6.33):** ver detalle en §16 (`abrirModal`).
- **Peso y Balance — disclaimer general reemplaza el cartel rojo condicional (HECHO, 2026-06-30, v1.3/v1.4):** antes solo se avisaba (cartel rojo) cuando `verificado:false` (LV-ART/LV-MPH); LV-OAD, al tener datos reales, no mostraba ningún aviso y daba la impresión de ser el cálculo "oficial". Ahora hay un disclaimer ámbar **siempre**, para las 3 aeronaves ("herramienta de planificación, no reemplaza el cálculo oficial"). El cartel rojo específico de "peso vacío sin verificar" se sacó del todo por pedido explícito (quedaba redundante); el flag `verificado` se mantiene intacto en `AERONAVES_WB` por si se necesita a futuro, solo dejó de disparar UI propia.
- **Incidente EmailJS — token OAuth de Gmail vencido en Cuenta A (RESUELTO, 2026-06-30):** ver §13 para el detalle completo. Causa raíz no relacionada con código: token OAuth de Google caducado, mensaje `Gmail_API: Invalid grant` en el log de EmailJS. Reconectado.
- **✓ Sincronización de versión confirmada al cierre de esta sesión (2026-06-30):** Daniel confirmó subido al repo turnos.html **v6.33**, fpl.html **v3.22** (sin cambios esta sesión), portal-alumno.html **v1.13** (sin cambios esta sesión), peso-balance.html **v1.4**. Coincide con la versión documentada en el header de este archivo — no queda ningún archivo entregado pendiente de deploy a la fecha.
- **Selector LISTA/SEMANA → toggle switch (HECHO, 2026-07-02, v6.45):** en la pantalla Reservas, reemplaza los tabs por un toggle de dos posiciones, mismo patrón visual que el de Disponibilidad de Instructores. Ver §9.
- **Línea de hora actual en SEMANA (HECHO, 2026-07-03, v6.48/6.49):** ver detalle en §9. Dos causas reales (querySelector agarraba siempre el wrap de LISTA; el chequeo de "hoy visible" usaba un helper que no aplica a SEMANA), no una sola.
- **Cuota semanal — tooltip de saldo + fix de conteo + transacción atómica (HECHO, 2026-07-03, v6.50/6.51/6.52):** sesión completa a partir de un pedido de Daniel ("agregar al tooltip cuántos turnos le quedan al alumno") que terminó destapando un bug de fondo real. Ver detalle completo en §6 (Cuota semanal de turnos). Resumen:
  - **v6.50:** tooltip `— Saldo: X/3 turnos esta semana` en el `cal-block` de turnos ya tomados (LISTA/SEMANA), no en los slots libres (primer intento, mal interpretado, revertido en la misma sesión).
  - **v6.51:** `confirmarTurno` pasa de leer-y-después-escribir a `runTransaction()` atómico — cierra un hueco de doble-submit/race condition (no confirmado como causa del caso real reportado, pero es una mejora real e independiente).
  - **v6.52 (el fix que importaba):** el conteo de cuota excluía **todo** `estado==='vencido'` sin distinguir. Como `vencerTurnosPendientes` marca `vencido` cualquier turno **aprobado** apenas pasa su horario (conservando `aprobado_por`), un alumno con vuelos ya volados y aprobados los veía "liberarse" de la cuota apenas alguien abría la app después de esa hora. **Caso real confirmado por Daniel** (dump de `/reservas.json` vía consola del navegador, no accesible desde este entorno de trabajo): un alumno con 5 turnos reales en una semana (3 `vencido`-con-`aprobado_por` + 1 `aprobado` + 1 `pendiente`) cuando el sistema, antes del fix, solo contaba 2. Corregido: `vencido` con `aprobado_por` ahora cuenta igual que `aprobado`; solo el `vencido` genuino del cron (sin `aprobado_por`) sigue sin contar.
  - **Nota de proceso de esta sesión:** las dos primeras hipótesis de diagnóstico (email vacío en turnos cargados por "+CARGAR TURNO"; el propio "+CARGAR TURNO" no chequea cuota) se tiraron sin datos reales y las dos fallaron — el diagnóstico correcto salió recién de pedirle a Daniel un dump real de Firebase filtrado por email. Para la próxima vez que aparezca algo parecido: pedir el dato crudo primero, no adivinar sobre el código solo.
  - **Pendiente, no resuelto en esta sesión:** `guardarTurnoManual` ("+ CARGAR TURNO") sigue sin chequear cuota (por diseño, sin confirmar si es intencional o un vacío a cerrar); las reglas de Firebase siguen abiertas (`{".read":true,".write":true}`) por lo que ninguna de estas validaciones de cuota corre del lado del servidor — una escritura directa a la REST API las esquiva completamente. Queda para cuando se aborde la Fase 5 de Auth (§18, punto de Seguridad/Auth).
- **Segmented control con pastilla deslizante — Seguimiento (HECHO, 2026-07-02, v6.46):** el selector TODOS/PRÓXIMOS/PASADOS pasa de tabs con `.active` estático a una pastilla que se desliza detrás del botón activo, posición calculada en JS. Ver §9.
- **FIX toggle LISTA/SEMANA no funcionaba (HECHO, 2026-07-02, v6.47):** v6.45 repitió el mismo bug de scope que ya se había corregido en `admDispToggleVista` (v6.43) — `onclick` inline leyendo una variable de módulo (`calVista`) no expuesta al scope global. Corregido con el mismo patrón: `setCalVista()` sin argumento calcula el toggle internamente. Ver §9. Segunda vez que aparece el mismo error de patrón — señal de que conviene evitar leer variables de módulo desde atributos `onclick` en general, no solo parchear caso por caso.
- **Pendiente (housekeeping, sin urgencia):** las clases CSS `.cal-vista-tab`/`.cal-vista-tabs` quedaron sin ningún uso en el HTML tras v6.45 (el otro selector que las usaba, TODOS/PRÓXIMOS/PASADOS, se migró a `.seg-switch` en v6.46). No se borraron para no mezclar con estos cambios funcionales — limpiar en una pasada de CSS aparte.
- **Versión de este documento vs. repo (2026-07-02):** turnos.html llegó a **v6.47** en esta sesión (entregado a Daniel, pendiente de que confirme subida al repo vía GitHub web UI). fpl.html, portal-alumno.html y peso-balance.html sin cambios esta sesión (siguen en v3.22/v1.13/v1.4).

## 19. Limitaciones conocidas

- **Reglas de Firebase abiertas** y **passwords en texto plano** (en proceso de resolución vía Auth). La `apiKey`/URL son públicas por diseño; la seguridad depende de Auth + reglas, no de ocultarlas.
- **Proceso de servidor:** la app web corre en el navegador y el barrido de vencimiento del cliente es **perezoso**. Hay **dos procesos server-side** programados (GitHub Actions): el recordatorio a instructores (§20) y el vencimiento de pendientes + purga FPL (§21). Las tareas que deben correr sin nadie online se resuelven por esa vía.
- **Sin queries SQL:** todo el filtrado es del lado del cliente. (Para consultas ad-hoc en SQL hay un script aparte que vuelca Firebase a SQLite — `fb_to_sqlite.py`.)
- **Caché:** Cloudflare no cachea HTML en el borde; la staleness era por caché del navegador, resuelta con la Cache Rule `no-store` sobre `.html` (§2). Con eso los deploys se propagan al instante; igual conviene verificar la versión en pantalla tras subir.
- **Pestaña ya abierta (resuelto turnos.html v6.03, fpl.html v3.19):** una pestaña que queda abierta por días nunca vuelve a pedir el HTML, así que corre el JS viejo para siempre aunque ya haya fixes subidos — la Cache Rule no ayuda porque no hay ninguna request nueva que interceptar. Caso real (2026-06-26): un alumno no podía cancelar un turno por tener abierta una versión de antes del fix v6.01. Solución: ambos archivos chequean cada 5 minutos (y al volver a la pestaña) si hay una versión más nueva publicada, comparando la versión en pantalla contra una copia recién pedida con cache-buster; si difiere, muestra un banner fijo con botón "ACTUALIZAR AHORA". **FIX v6.26 (turnos.html):** el botón hacía `location.reload()` a secas, que no fuerza bypass de caché del navegador (a diferencia del `fetch` con `cache:'no-store'` de la *detección*, que sí lo hace) — el usuario tenía que apretar varias veces hasta que el caché venciera solo. Ahora navega a una URL con cache-buster (`?_v=timestamp`), igual que la detección. **`fpl.html` (verificado, línea `btn-recargar-version`) sigue con el `location.reload()` sin cache-buster, mismo bug sin corregir. `portal-alumno.html` y `peso-balance.html` directamente no tienen el mecanismo de chequeo de versión** — pendiente aplicar el fix a `fpl.html` y, si se quiere el mismo blindaje, agregar el mecanismo completo a los otros dos.
- **Mobile — sin `@media` hasta 2026-06-27, fix parcial sin confirmar (turnos.html v6.06/6.07, fpl.html v3.21/3.22, portal-alumno.html v1.11/1.12):** ninguno de los cuatro archivos tenía breakpoints; se agregó una pasada `@media (max-width:680px)` cubriendo los puntos más pesados encontrados leyendo el CSS (headers sin `flex-wrap` que podían desbordar, tab-bars que envolvían en varias filas, campos de formulario con `min-width` inline). **Caso real sin resolver del todo (turnos.html, 2026-06-28):** después de la pasada mobile, en un iPhone seguía haciendo falta pinch-zoom-out para ver toda la página, con texto cortado simultáneamente en ambos bordes (patrón de pan/zoom real, no solo cosmético). Se agregó `overflow-x:hidden`+`max-width:100vw` en `html`/`body` como red de seguridad **sin haber identificado el elemento exacto que se escapa** (no se pudo cazar por lectura de código sin acceso a un dispositivo o inspector remoto). Se descartó que fuera el Zoom de Accesibilidad de iOS (estaba apagado) y se confirmó que en Android no pasa — es algo específico de cómo Safari/iOS calcula el viewport con este HTML/CSS puntual, sin diagnóstico cerrado. **Si vuelve a aparecer, el camino más rápido es Safari Web Inspector remoto (Mac + cable) en vez de seguir cazando a ciegas.**
- **`peso-balance.html`:** peso vacío/brazo de LV-ART y LV-MPH son estimaciones sin verificar contra la planilla de pesaje real de cada aeronave (ver §24).

## 20. Recordatorio automático a instructores (proceso server-side / cron)

Aviso por mail al instructor **~12 horas antes** del turno más temprano de cada día, con el listado de **todos** los turnos aprobados que tiene ese día (no un mail por turno). Corre fuera de la app web, como proceso programado, así funciona aunque no haya nadie con la página abierta.

### Archivos (en el repo)
- **`recordatorio_instructor.py`** (raíz del repo) — el script. Solo usa librería estándar de Python (urllib, json, datetime); no requiere pip.
- **`.github/workflows/recordatorio-instructor.yml`** — el workflow de GitHub Actions que lo dispara.

### Disparo
- `schedule: cron "*/15 * * * *"` — cada 15 min. GitHub corre en **UTC** y puede atrasarse varios minutos; no importa, lo cubre la ventana + la marca anti-duplicado.
- `workflow_dispatch` con input **`dry_run`** (`1` = prueba sin enviar ni marcar; `0` = real). Útil para probar desde la pestaña Actions.

### Lógica del script (agrupado por día desde 2026-06-24)
1. Lee `/reservas` e `/instructores` de Firebase por REST (reglas de lectura abiertas).
2. Calcula "ahora" en hora local (UTC-3 fijo; Argentina no usa horario de verano).
3. Filtra turnos `estado==aprobado` **con instructor asignado** (`aprobado_por`/`instructor`) que todavía no pasaron, y los **agrupa por (instructor, fecha)**.
4. Para cada grupo, toma el turno **más temprano todavía futuro** de ese día. Si cae dentro de las próximas `REMIND_HOURS` horas **y ese día no fue avisado todavía para ese instructor**, dispara un único mail con el listado completo (ordenado por hora) de los turnos del grupo.
5. Resuelve el email del instructor matcheando `nombre` (de `aprobado_por`) contra `/instructores`. Sin email cargado → se saltea (se loguea).
6. Envía el mail vía **API REST de EmailJS** (server-side, con private key).
7. Marca el día como avisado en `/recordatorios_diarios/{fecha}/{instructor_user}` (PATCH REST) — **ese es el anti-duplicado real**. También marca cada reserva incluida con `recordatorio_inst_enviado:true` + `recordatorio_inst_ts`, a fines de auditoría (ya no se usa para decidir si reenviar).
8. Si hubo errores de envío, el job termina en fallo (rojo) para que se note en Actions.

> **Límite conocido:** si después de mandado el mail del día se aprueba un turno nuevo para ese mismo instructor el mismo día, no genera un aviso adicional — el día ya quedó marcado como avisado. Si se quiere que un turno nuevo "reabra" el aviso del día, hay que borrar `/recordatorios_diarios/{fecha}/{instructor_user}` (por ejemplo al aprobar/liberar un turno de ese día), cosa que **todavía no está implementada**.
>
> Al **liberar** un turno se sigue limpiando `recordatorio_inst_enviado/ts` de esa reserva (v5.76), pero ya es solo informativo — no afecta el envío del recordatorio diario.

### Template EmailJS (cambio de variables)
El template `template_8awr1zd` pasa de variables de **un solo turno** (`alumno_nombre`, `turno_hora`, `matricula_avion`) a una variable de **listado**: `{{turnos_lista}}` (líneas tipo `HH:MM hs — MATRÍCULA — alumno: NOMBRE`, separadas por `<br>`, pensado para template HTML), más `{{fecha_turnos}}` y `{{cantidad_turnos}}`. Las variables `instructor_email`, `instructor_nombre`, `name`, `email` se mantienen igual. **Hay que editar el template en EmailJS a mano** para usar `{{turnos_lista}}` en vez de los campos viejos.

### EmailJS (Cuenta B — ver §13. Confirmado con envío real el 2026-06-29 11:53)
- Service **`service_48kzt1s`** ("Instructores ACRG", `instructoresacrg@gmail.com`), template `template_8awr1zd` ("Recordatorio Instructor"), public key `TzbSjqDNPjTGSGdzN`.
- Requiere tener habilitada en **esta** cuenta la opción **"Allow EmailJS API for non-browser applications"** (Account → Security, logueado con `dcamargo70@gmail.com`) y usar la **private key** de esta cuenta como `accessToken` en la llamada REST.
- El mail sale **desde `instructoresacrg@gmail.com`**. Reply-To = mail del club (`CLUB_EMAIL`). **Confirmado con envío real** el 2026-06-29 11:53.

### Configuración (variables `env:` del workflow)
- Públicas (en el `.yml`): `FIREBASE_DB_URL`, `EMAILJS_SERVICE_ID` (**`service_48kzt1s`**), `EMAILJS_TEMPLATE_ID` (`template_8awr1zd`), `EMAILJS_PUBLIC_KEY` (`TzbSjqDNPjTGSGdzN`), `CLUB_EMAIL` (`administracion@aeroclubriogrande.com`), `REMIND_HOURS` (**12**), `TZ_OFFSET` (`-3`), `DRY_RUN`.
- **Secreta** (GitHub → Settings → Secrets and variables → Actions): `EMAILJS_PRIVATE_KEY` = private key de la **Cuenta B** (logueado con `dcamargo70@gmail.com`, que tiene `service_48kzt1s`/`TzbSjqDNPjTGSGdzN`). **Nunca** va en el repo.
- **`REMIND_HOURS` se ajusta editando esa línea del `.yml`** (no toca el `.py`): cambiar el número y commitear.

### Prerrequisito operativo
Cada instructor debe tener su **`email` cargado** (Mi Perfil, o el admin desde el modal de instructor). Los que no lo tengan se saltean sin aviso; en la lista de instructores aparecen marcados con **"⚠ sin mail"**. `admin`/`administrador` no necesitan email (no aprueban turnos).

Además, en la cuenta EmailJS usada por el cron (Cuenta B) tiene que estar **activado** el toggle **Account → Security → "Allow EmailJS API for non-browser applications"**. Viene **apagado por default** en EmailJS y no tiene nada que ver con las keys/templates — si está apagado, el script tira `HTTP 403` sin importar que el resto de la config esté perfecta (pasó el 2026-06-29, fue la causa original de "no salió el mail al instructor").

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
- Públicas: `FIREBASE_DB_URL`, `EMAILJS_SERVICE_ID` (**`service_48kzt1s`**, Cuenta B/`instructoresacrg@gmail.com` — ver §13), `EMAILJS_TEMPLATE_ID` (`template_4wja6rq`, template del aviso de vencimiento al alumno), `EMAILJS_PUBLIC_KEY` (`TzbSjqDNPjTGSGdzN`), `CLUB_EMAIL` (Reply-To), `EXPIRE_HOURS` (default **6**, debe ser <12), `TZ_OFFSET` (`-3`), `FPL_PURGE_HOURS` (default **1**), `DRY_RUN`.
- **Secreta:** `EMAILJS_PRIVATE_KEY` (accessToken de la Cuenta B, en GitHub Secrets). Mismo secreto que usa §20 (un solo secret para los dos workflows).

> **Guard "sin ventana" probado en producción (2026-06-28):** el paso 2 de la lógica no solo chequea que el vuelo esté dentro de `EXPIRE_HOURS` — también exige que el turno **haya sido creado ANTES del mojón de vencimiento** (`vuelo − EXPIRE_HOURS`). Un turno creado, por ejemplo, 41 minutos antes del vuelo (mucho menos que las 6 h de margen) se salta con el log `~ sin ventana (creado dentro del plazo)`: nunca tuvo chance real de ser confirmado a tiempo, así que sería injusto vencerlo. Para una prueba real hay que cargar el turno con **más de `EXPIRE_HOURS` de anticipación** y esperar a que el reloj real lo meta dentro de la ventana — no hay forma de simularlo con `dry_run` ni de apurarlo de otro modo.

## 22. Generador de planes de vuelo (`fpl.html`)

Página aparte (no es turnos.html) para armar **planes de vuelo OACI** (casillas 7 a 19) y generar el PDF para presentar. Comparte la misma Firebase (`turnos-lv-oad`) vía REST.

### Funcionamiento
- **PDF:** estampa los valores por coordenadas (`RECTS`) sobre una base **rasterizada** del formulario oficial argentino, así renderiza idéntico en cualquier visor sin depender de fuentes embebidas. Incrusta la firma a mano alzada. Texto del usuario en **MAYÚSCULAS**.
- **Horas:** se cargan en **local (UTC-3)** y el sistema convierte/muestra **UTC** (casilla 13); `DOF` (18) se calcula en UTC (maneja el cambio de día). El comandante (19 C/) es un campo explícito, separado de quien arma el plan.
- **Presentado por / Filed by (v3.17):** casillero administrativo al pie del formulario oficial (no es parte del ítem 19, no se transmite). El `RECTS` ya tenía la coordenada (`Filed`) pero nunca se llenaba. Ahora hay un input dedicado, se autocompleta con el usuario logueado (editable), se imprime en el PDF (individual y combinado) y se guarda/restaura en los borradores (`presentado_por`). Aviso no bloqueante si queda vacío.
- **OACI deprecados (`OACI_DEPRECADOS={SAWO}`, v3.16):** los aeródromos ya no vigentes se fuerzan a `ZZZZ` + detalle en el ítem 18 (DEP/ DEST/ ALTN/). Caso EAU (Est. Aeronaval Ushuaia): venía como SAWO y se estampaba mal. La lista vive en `fpl.html` (sobrevive a la regeneración del JSON de aeródromos). La sustitución IATA→OACI vigente (EZE→SAEZ, etc.) no cambia.

### Borradores y usuario
- Borradores por usuario en `/fpl/{USUARIO}`. Resuelve el usuario leyendo la sesión de turnos (`sessionStorage 'lvoad-session'`) **en la misma pestaña**. Si no hay sesión (link externo) usa un bucket efímero `/fpl/externo_{uuid}` por sesión, que el cron (§21) purga al cabo de 1 h.
- **FIX v3.18 (bug histórico desde v3.12):** `resolverUsuario()` (la clave real de guardado) nunca miraba `session.username`, solo `user`/`emailKey`/`nombre` — a diferencia de `resolverUsuarioLabel()` (lo que se muestra en pantalla/auditoría), que sí lo hacía desde v3.12. Para instructor/admin no se notaba (siempre traen `user`), pero para alumno/piloto el borrador terminaba guardado bajo el `emailKey` sanitizado mientras la pantalla mostraba el `username` real — dos identidades distintas para la misma persona. Caso real: un piloto (username `tony`) decía haber armado planes; buscando `/fpl/tony` no había nada porque en realidad (de haber existido) hubiera quedado en `/fpl/{su_emailKey}`. Se confirmó además que no había generado ningún plan (tampoco bajo esa clave) — el bug era real pero en este caso puntual no era la causa. Corregido insertando `username` en la misma posición relativa que tiene en el Label.
- **PDF combinado (v3.10):** se tildan varios borradores y se genera **un** PDF con todos (una hoja por plan, en orden cronológico). La descarga la hace el usuario; el sistema no envía.

### Aeronaves
- El selector **mergea** las 3 del club (`/aeronaves`, global) + las **personales** del usuario (`/aeronaves_usuario/{USUARIO}`, solo el dueño las ve). Al elegir una personal precarga todo (FPL + SPL/comandante/remarks).
- Editor "EDIT AIRCRAFT" (v3.14) con tabs **FPL** (7/9/10/15/18) y **SPL** (Item 19: R/ S/ J/ D/ A/ C/ N/), incluido **D/ botes neumáticos**. Externos no guardan aeronaves.

### Auditoría FPL
- Cada PDF generado escribe un registro en `/auditoria` con `rol:'fpl'` (`accion: 'fpl_*'`). `fpl.html` **solo escribe**; la **vista** está en turnos.html (sub-pestaña "Audit. FPL", solo admin — §10). La auditoría de turnos excluye estos registros.
- **FIX v3.18:** antes el `POST` a `/auditoria` era "fire and forget" — si fallaba (sin red, navegador suspendiendo la pestaña al disparar la descarga, etc.) el error se tragaba en consola sin avisar a nadie; el usuario veía "PDF generado" igual aunque la auditoría nunca llegara. Ahora `generarPDF()`/`generarPDFCombinado()` esperan (`await`) el resultado; si falla, el registro se guarda en una **cola en `localStorage`** (`fpl-audit-queue`) que se reintenta sola en cada carga de página y antes de cada auditoría nueva. Si después de generar el PDF sigue sin poder entrar, se avisa con un `alert()` explícito (no un toast que se pueda pisar con el mensaje siguiente).
- **FIX v3.20 (caso real: 5 planes combinados desde celular, ninguno quedó auditado, con v3.18 ya confirmada en pantalla):** en `generarPDF()`/`generarPDFCombinado()` el `link.click()` (dispara la descarga) se ejecutaba **antes** de esperar la auditoría. En mobile, descargar/compartir un blob suele abrir la hoja nativa de guardado del sistema operativo, lo que pone la pestaña en segundo plano — el navegador puede congelar el JS a mitad del `await` (sobre todo en el loop de varios planes del combinado). Si se congela ahí, la auditoría nunca llega a ejecutarse NI a fallar formalmente: no cae ni en la cola de reintento de v3.18. Fix: la auditoría ahora corre **antes** del `link.click()`, mientras la pestaña sigue en foreground.
- **v3.19:** banner global de "versión vieja" (mismo mecanismo que turnos.html, ver §19), que esta página no tenía.

### Mobile (v3.21/v3.22)
- **v3.21:** primera pasada `@media (max-width:680px)`. `header.bar` tenía 6 elementos en una fila sin `flex-wrap` (punto, título, badge de versión, link a turnero, reloj de 2 líneas, botón de tema) — desbordaba en celular angosto. Pasa a envolver (título más chico, reloj/badge/link a una segunda fila vía `order`). Lista "Mis Planes" (`.item`) también sin `flex-wrap` — ahora envuelve. Casilleros del FPL (`.row>.field`) más compactos. `.cdato` (contacto ARO/AIS) con `flex-wrap` para que un email largo no desborde.
- **v3.22:** `overflow-x:hidden` + `max-width:100vw` en `html`/`body` — misma red de seguridad preventiva que turnos.html v6.07 (ver §19), por si el mismo síntoma de pinch-zoom-out en celular afecta también a esta página.

### Integración con turnos.html
- Botón **"Plan de Vuelo"** (clase `.fpl-link`, pill ámbar) en las tab-bars de alumno e instructor (v5.89/v5.90).
- Meteorología **METAR/TAF de SAWE** (CheckWX) vive en `fpl.html`, no en turnos.html.

## 23. Portal de Alumno (`portal-alumno.html`)

Página aparte, nueva (construida 2026-06-25/26), con cuatro pestañas: **PERFIL**, **MANUALES**, **CUESTIONARIOS** (renombrado de "QUIZ" en v1.9 — solo el texto del tab, los textos internos del panel no cambiaron) y **FLASHCARDS** (v1.10). Comparte la misma Firebase (`turnos-lv-oad`) vía SDK modular (no REST como `fpl.html`).

### Acceso (gate de sesión)
Valida contra la misma `sessionStorage 'lvoad-session'` que pone `turnos.html` al loguearse, **en la misma pestaña** (no hay persistencia entre pestañas, igual que en `fpl.html`). Acceso permitido:
- `rol:'alumno'` (alumno) — entra como **esAlumno**.
- `tipo:'instructor'` (cubre instructor real, admin y administrador) — entra como **esStaff**.
- **`rol:'piloto'` NO tiene acceso** (igual que el link de turnos.html, que oculta el botón "Portal Alumno" para pilotos).

Si no hay sesión válida, muestra "VALIDANDO SESIÓN…" y redirige sola a `turnos.html` en ~900ms.

### Tema
Hereda el tema (claro/oscuro) elegido en `turnos.html` o `index.html`: lee `localStorage.getItem('lvoad-theme')` al cargar (misma clave que esos dos archivos). Desde v1.9 tiene su **propio selector** (🌙/☀️ en el header, idéntico visualmente al de turnos.html) para cambiarlo sin volver al turnero; al cambiarlo, también actualiza esa misma clave compartida.

### Botón SALIR (v1.13)
Botón **⏻ SALIR** en el header (estilo rojo translúcido, para diferenciarlo del botón "← TURNERO"): borra la sesión de `sessionStorage` y redirige a `turnos.html`. Mismo comportamiento que "Cerrar sesión" en turnos.html. Esta estética del ícono ⏻ se implementó primero acá y de ahí se llevó al botón SALIR de turnos.html (v6.17).

### Tab PERFIL
- **Alumno:** ve su propia ficha (de `/alumnos/{emailKey}`: nombre, email, teléfono, usuario, rol) y la nota del instructor (`/notas_alumno/{emailKey}`), ambas **solo lectura**.
- **Staff:** selector de alumno (filtra `rol==='alumno'` de `/alumnos`) y un textarea para escribir/editar la nota de ese alumno (`{texto,autor,fecha}`).

### Tab MANUALES
Dos fuentes combinadas:
1. **"ARCHIVOS EN /manuales (REPOSITORIO)"** — listado automático del contenido real de la carpeta `/manuales` del repo vía **GitHub Contents API** (`https://api.github.com/repos/danieltdfdev/Web-Aeroclub/contents/manuales`, sin autenticar, repo público). Cada archivo es un link de descarga directo (`download_url`). No depende de Firebase ni de que el staff registre nada a mano: subís el PDF a esa carpeta del repo y aparece solo en el próximo refresh. **Límite conocido:** la API de GitHub sin autenticar permite 60 req/hora por IP — no debería ser problema con el tráfico de un aeroclub, salvo que varios alumnos abran la página casi a la vez desde la misma red (mismo IP, mismo contador).
2. **"CATÁLOGO / LINKS"** (`/manuales` en Firebase) — para links externos o para ponerle nombre/categoría prolijos a un archivo del repo en vez de su nombre de archivo tal cual. Alta/edición/borrado solo staff.

### Tab CUESTIONARIOS (quiz)
- **Alumno:** ve la lista de quizzes `activo`. Si ya rindió, ve directamente su **puntaje del último intento** en el ítem (sin tener que abrir nada); si el quiz **no** es obligatorio, además del puntaje aparece un botón REINTENTAR. Si nunca rindió, botón RENDIR.
- **Banco de preguntas (`QZ_BANCO_SIZE=50`):** si el quiz tiene 50 o más preguntas cargadas, al rendir se sortean 50 al azar (Fisher-Yates) **para ese intento puntual** — sorteo distinto por alumno y por intento, fijo durante todo el intento (no se vuelve a mezclar al cambiar de página). Aviso visible al alumno ("⚠ Banco de N preguntas — te tocaron 50 al azar"). El **banco real cargado a la fecha de este doc es el de ANAC PPA, con 318 preguntas** (parseadas del PDF oficial de ANAC, *Preguntas según RAAC 61.105*, descartando ~66 que dependían de figuras/ilustraciones no disponibles, y 1 pregunta rota en el propio PDF fuente).
- **Paginado al rendir:** 10 preguntas por página (`QZ_PAGE_SIZE`), navegación Anterior/Siguiente, contador "Respondidas: X/Y". Las respuestas se guardan en un array fuera del render (no se pierden al cambiar de página). Si se envía con preguntas sin responder, salta directo a la página de la primera pendiente.
- **Quiz obligatorio** (checkbox en el editor): el alumno tiene **un solo intento permitido**, re-chequeado contra Firebase tanto al abrir el formulario como al enviar (cubre el caso de doble pestaña). El intento guarda, además de puntaje, un **detalle por pregunta** (`{enunciado,elegida,correcta_texto,acierto}`) para que el staff revise exactamente qué contestó mal cada alumno.
- **Staff:** botón "+ NUEVO QUIZ"; editor con título, categoría, checkbox obligatorio, preguntas (enunciado + 4 opciones + radio de cuál es correcta), y un **importador de JSON** que tolera fences de markdown (` ```json `) y CRLF pegados al copiar — agrega las preguntas pegadas a las que ya estén cargadas (no las reemplaza). Esquema esperado: `[{pregunta, opciones:["A) ...","B) ...","C) ..."], respuesta_correcta:"A"}]`.
  - En la lista de quizzes, junto a la categoría y cantidad de preguntas, ve un resumen agregado de **todos** los alumnos: "N intento(s) · Promedio X%", o "Sin intentos todavía".
  - Botón INTENTOS: detalle por alumno (fecha, puntaje, botón VER DETALLE con el desglose pregunta por pregunta). Si el quiz es obligatorio, además muestra el **roster de pendientes** (alumnos de `/alumnos` con `rol==='alumno'` que todavía no lo rindieron).
  - Botones ACTIVAR/DESACTIVAR, EDITAR, BORRAR (el borrado del quiz **no borra los intentos asociados en Firebase** — quedan huérfanos en `/intentos_quiz`, solo desaparecen de la vista; es dato suelto que ocupa espacio, sin impacto funcional).
- **Mi Historial** (solo alumno): lista de todos sus intentos pasados (fecha + puntaje), de cualquier quiz.

### Tab FLASHCARDS (v1.10)
Mazo de **75 preguntas/respuestas** sobre el LV-OAD (PA-38-112), con flip 3D pregunta/respuesta (CSS, sin librerías). Mecánica: se mezclan al azar al entrar a la tab; "La sé" sacar la carta del mazo de esa vuelta, "No la sé" la manda al final para que vuelva a aparecer antes de terminar. Al vaciar el mazo, pantalla de cierre con cuántas se dominaron y botón para repasar de nuevo. Contenido **estático curado a mano** (constante `FLASHCARDS_DATA` en el propio archivo) — no usa Firebase, no es editable desde la UI.

- **Dato del origen de los datos:** el JSON que se cargó tenía un formato inusual — cada ítem del array codificaba **dos** preguntas distintas cruzadas, como `{Q1:Q2, A1:A2}` en vez de `{Q1:A1, Q2:A2}` (las dos *claves* del objeto formaban una tarjeta real, los dos *valores* formaban otra). De 74 ítems salieron 75 tarjetas únicas tras destrabar el cruce y deduplicar. También traía notación LaTeX cruda (`$112$`, `V_{NO}`, `^\circ`, `\pm`) que se limpió a texto plano (`Vno`, `Vfe`, `°`, `±`, `²`/`³`) antes de cargar el banco.
- Si más adelante se quiere un editor para que el staff cargue más mazos (p. ej. para Archer/Lance) o más tarjetas del Tomahawk, queda pendiente como mejora — hoy es contenido fijo.

### Mobile (v1.11/v1.12)
- **v1.11:** primera pasada `@media (max-width:680px)`. `header.bar .sp` (theme-toggle + nombre + versión + volver) sin `flex-wrap` podía desbordar en celular con nombre largo — mismo patrón corregido en turnos.html y fpl.html (ver §19). `nav.tabs` (ya con `flex-wrap`, pero envolver 4 tabs en 2 filas se veía abarrotado) pasa a scroll horizontal de una sola fila. Flashcards con texto y padding más compactos.
- **v1.12:** `overflow-x:hidden` + `max-width:100vw` en `html`/`body` — misma red de seguridad preventiva que turnos.html v6.07.

### Bug crítico corregido (v1.6)
`renderPerfil()` y `renderManuales()` nunca se ejecutaban: en cada paso de construcción incremental (v1.0→v1.1→...) se usó la línea de invocación del paso anterior como ancla de edición, pisándola en vez de conservarla, hasta que de las tres llamadas finales solo sobrevivió `renderQuiz()`. Resultado: las tabs PERFIL y MANUALES quedaban completamente vacías. Lección aplicada desde entonces: al hacer ediciones incrementales sobre líneas de invocación al final de un módulo, verificar explícitamente que las llamadas anteriores sigan presentes antes de entregar.

### Esquema de Firebase
Ver §3: `/manuales`, `/quizzes`, `/intentos_quiz`, `/notas_alumno`. También lee (solo lectura desde el portal) `/alumnos` e `/instructores`.

## 24. Calculadora de Peso y Balance (`peso-balance.html`)

Página aparte (construida 2026-06-27), réplica de la planilla Excel del club, con un **ábaco** (gráfico de envolvente + punto de CG) dibujado en SVG a mano, sin librerías. **No usa Firebase para nada** — no guarda ni lee datos, cada uno tipea los pesos a mano cada vez (decisión explícita).

### Acceso
A diferencia de `portal-alumno.html`, valida la misma `sessionStorage 'lvoad-session'` pero **deja entrar cualquier rol** (alumno, piloto, instructor, admin, administrador) — la herramienta aplica a todo el que vuela, no solo a alumnos en instrucción. Sin sesión, redirige a `turnos.html`.

### Esquema de datos por avión (`AERONAVES_WB`)
Cada avión declara su propia lista de **`estaciones`** de carga (filas de pasajeros, compartimentos de equipaje, combustible), cada una con su propio brazo (pulgadas) — necesario porque Archer (4 plazas) y Lance (6 plazas, 3 filas, 2 compartimentos de equipaje) no entran en el molde de una sola fila del Tomahawk. El formulario y el cálculo se generan dinámicamente a partir de esa lista; agregar un avión nuevo es sumar una entrada al objeto, sin tocar el resto del código.

- **LV-OAD (Tomahawk PA-38-112):** datos **verificados** (`verificado:true`), de la planilla original del club. Envolvente, brazos, MTOW 1670 lbs, consumo 22 L/h.
- **LV-ART (Archer II PA-28-181):** brazos/envolvente/MTOW de **TCDS+POH** del modelo (fuentes públicas, cruzadas entre 3 sitios independientes). `peso_vacio`/`brazo_vacio` son un **placeholder de catálogo SIN verificar** (`verificado:false`) — no hay dato real de pesaje de *esta* aeronave puntual.
- **LV-MPH (Lance II PA-32RT-300):** brazos/envolvente/MTOW de la **TCDS A3SO de la FAA** (Modelo V, fuente primaria). `peso_vacio` estimado a partir de la carga útil ya publicada en `flota.html` (514 kg / 1134 lbs ⇒ MTOW 3600 − 1134 = 2466 lbs) — **tampoco es un dato de pesaje confirmado** (`verificado:false`).
- **Disclaimer general, siempre visible (v1.3/v1.4, 2026-06-30):** cuadro ámbar fijo arriba del formulario, para **las 3 aeronaves por igual** ("herramienta de planificación, no reemplaza el cálculo oficial — verificá siempre contra la planilla de pesaje vigente"). Reemplaza al esquema anterior, donde solo se avisaba (cartel rojo) cuando `verificado:false` — LV-OAD, al tener datos reales, no mostraba ningún aviso y daba la impresión de ser el cálculo "oficial" de la app, que es justo lo que se quiere evitar. El **cartel rojo específico** de "peso vacío sin verificar" se sacó del todo en v1.4, por pedido explícito (quedaba redundante con el disclaimer general). El flag `verificado` **se mantiene intacto** en `AERONAVES_WB` por si se necesita a futuro — solo dejó de disparar su propio cartel; el sufijo "(DATOS SIN VERIFICAR)" dentro del cuadro de resultado ok/bad tampoco se tocó y sigue apareciendo cuando corresponde. **Pendiente:** Daniel tiene que pasar el peso vacío + brazo reales (de la planilla de pesaje de cada aeronave) para reemplazar los placeholders de LV-ART/LV-MPH.

### Cálculo
- Inputs en **kg** (personas, equipaje) y **litros** (combustible) — unidades habituales en Argentina; se convierte todo internamente a lbs/pulgadas (unidades del POH) con `kg_a_lb` (2.2) y `fuel_kg_por_litro` (0.72).
- Validación: ray-casting (punto dentro del polígono de la envolvente) **además** de chequear MTOW — más estricto que la planilla Excel original, que solo comparaba contra MTOW y podía dar "OK" con el CG fuera de los límites delantero/trasero.
- **Bug de la planilla original NO replicado:** la celda "Equipaje OK" del Excel comparaba el número fijo `14` contra `100` (siempre daba OK, sin mirar el peso real de equipaje). Acá, en cambio, cada estación de equipaje puede declarar un `max_kg` propio (100 lbs por compartimento en el Lance, 200 lbs en el Archer) que se chequea aparte de la envolvente.
- Tooltips (v1.1) con la conversión a kg en todos los valores que se muestran en lbs (Peso total, MTOW, Peso disponible, ticks del eje Y y etiqueta MTOW del ábaco).

### Integración con turnos.html
Botón **"⚖ Peso y Balance"** (clase `.wb-link`, pill verde) en las `.links-row` de alumno e instructor (turnos.html v6.05) — visible para **cualquier rol** (a diferencia de "Portal Alumnos", que se esconde para piloto), porque la herramienta le aplica a todo el que vuela.
