# Shutdown Control — BHP Spence

Primera versión operativa para seguimiento de órdenes de trabajo a partir del formulario Excel de reportabilidad de BHP Spence. Funciona en VPS Ubuntu con Docker y no modifica el archivo original.

## Funciones disponibles

- Importación manual de `.xlsx` (hasta 15 MB), sin enviar archivos a servicios externos.
- Consolidación por número de OT: última actualización e historial completo de reportes.
- Filtros por área, turno y estado; búsqueda por OT, actividad, responsable y comentario.
- Visualización de reseñas técnicas y enlaces a fotografías (dependen de permisos Microsoft 365).
- Alertas de datos inconsistentes y registros sin avance.
- Persistencia SQLite y actualización por ID de formulario (reimportar no duplica reportes).
- API REST para Power Automate con UPSERT por Id, validación y estado de última sincronización.
- Login visual con contraseña protegida mediante PBKDF2, sesiones con expiración, bloqueo temporal por intentos fallidos y permisos por rol.

## Sincronización desde Power Automate

`POST /api/sync/forms` recibe exactamente el objeto producido por Excel Online (Business):

```json
{"body":{"value":[{"Id":"101","¿Cuál es tu área de trabajo?":"Filtros","Ingresa el número de OT (Filtros)":"450001"}]}}
```

Los nombres de las columnas se normalizan ignorando mayúsculas, tildes, signos, espacios normales y espacios especiales. El área se reconoce desde `¿Cuál es tu área de trabajo?`. Los campos operacionales se localizan por su significado y por el área entre paréntesis; no se usa el orden de las columnas. Esto contempla Filtros, Molibdeno, Tranque y Flotación, incluyendo encabezados como `Carga una fotografía del ANTES del trabajo (Filtros)`, `... DURANTE ...` y `... FINAL ...` para cada área. Cada fila debe incluir un `Id` y área válidos. La API acepta hasta 2 MiB y 5.000 filas por solicitud por defecto. Estos límites pueden reducirse mediante `SYNC_MAX_BYTES` y `SYNC_MAX_RECORDS`.

La autenticación de esta ruta es independiente de la contraseña del dashboard. Genere un secreto aleatorio (por ejemplo, con `openssl rand -hex 32`), configúrelo sólo en el `.env` de cada entorno y envíelo desde Power Automate como encabezado:

```text
Authorization: Bearer <FORMS_SYNC_TOKEN>
Content-Type: application/json
```

No confirme el `.env` en Git ni incluya el token en URL, cuerpo, nombres de acciones o logs. La política de resultados distingue tres estados:

- `success`: HTTP `200`; todas las respuestas fueron importadas.
- `success_with_pending`: HTTP `200`; las respuestas con OT fueron importadas y las que aún no tienen OT quedaron trazadas por Id en `pending_reports`. La respuesta informa `imported` y `pending`. No se inventan números de OT.
- `success_with_review`: HTTP `200`; los demás campos fueron actualizados, pero una OT recibida no coincide con la OT original e inmutable. La respuesta informa `discrepancies` y el reporte queda marcado para revisión.
- `rejected`: HTTP `422`; existe al menos un error estructural real, como Id/área inválidos, tipo no permitido o Id duplicado dentro del lote. La transacción completa se rechaza con `upserted: 0`.

La primera OT válida asociada a un Id de Forms es inmutable. El sistema nunca genera, incrementa, renumera ni sustituye ese identificador. Si el mismo Id llega con otra OT, conserva la original, actualiza los demás campos, añade la alerta `OT discrepante en origen: revisar` y registra ambas OT en `ot_discrepancies`. Una respuesta pendiente se elimina de `pending_reports` y registra su primera OT mediante UPSERT cuando el mismo Id llega posteriormente con una OT válida.

Si un Id que ya tenía un reporte válido pierde su OT en el origen, el reporte histórico tampoco se elimina: conserva su última OT, recibe la alerta `OT eliminada en origen: revisar` y queda registrado como pendiente con un motivo diferenciado. El evento permanece auditado en `sync_log`. Cuando vuelve a tener la misma OT, el UPSERT actualiza el registro, limpia la alerta derivada y retira el pendiente. El dashboard muestra fecha UTC, fuente, cantidad importada, pendientes, discrepancias y errores de la última sincronización.

### Prueba local con datos ficticios

En PowerShell, sin modificar el Excel ni conectarse a Microsoft 365:

```powershell
$env:DASHBOARD_USER = 'local'
$env:DASHBOARD_PASSWORD = 'clave-local-no-productiva'
$env:FORMS_SYNC_TOKEN = 'token-local-largo-no-productivo'
$env:DATABASE_PATH = "$PWD/data/prueba-local.db"
python app.py
```

En otra consola:

```powershell
$headers = @{ Authorization = 'Bearer token-local-largo-no-productivo' }
$body = @{ body = @{ value = @(@{
  Id = 'TEST-101'; 'Hora de inicio' = '2026-11-05T08:00:00Z'
  'Hora de finalización' = '2026-11-05T08:10:00Z'; 'Correo electrónico' = 'prueba@example.invalid'
  Nombre = 'Persona ficticia'; '¿Cuál es tu área de trabajo?' = 'Filtros'
  '¿Cuál es tu turno? (Filtros)' = 'DIA'; '¿Qué fecha corresponde al reporte? (Filtros)' = '2026-11-05'
  'Ingresa el número de OT (Filtros)' = '450001'; 'Ingresa el número de actividad (Filtros)' = '10'
  '¿Cuál es el porcentaje de avance? (Filtros)' = '25'; '¿Cuántos trabajadores participaron? (Filtros)' = '4'
  '¿Cuántas horas fueron trabajadas? (Filtros)' = '6'; 'Ingresa una reseña del trabajo realizado (Filtros)' = 'Dato ficticio'
  'Carga una fotografía del ANTES del trabajo (Filtros)' = 'https://example.invalid/antes'
  'Carga una fotografía del DURANTE del trabajo (Filtros)' = 'https://example.invalid/durante'
  'Carga una fotografía del FINAL del trabajo (Filtros)' = 'https://example.invalid/final'
}) } } | ConvertTo-Json -Depth 5
Invoke-RestMethod http://127.0.0.1:8080/api/sync/forms -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

Ejecute las pruebas automatizadas con `python -m unittest discover -s tests -v`. Todas usan una base temporal y datos ficticios.

## Instalación VPS

1. Copie esta carpeta a la VPS.
2. Duplique `.env.example` como `.env` y establezca credenciales y `FORMS_SYNC_TOKEN` únicos y robustos. El token no debe ser la contraseña del dashboard ni una credencial Microsoft 365.
3. Ejecute `docker compose up -d --build`.
4. Configure Nginx como proxy inverso hacia `http://127.0.0.1:2000`, con certificado TLS (HTTPS) y restricción de acceso adecuada, idealmente VPN. Docker conserva exactamente el enlace local `127.0.0.1:2000:8080`: puerto local `2000` y puerto interno del contenedor `8080`.
5. Ingrese al dominio configurado y seleccione **Actualizar Excel** para cargar el archivo de reportabilidad.

### Preparación previa del secreto en la VPS

Antes de fusionar o desplegar esta versión, genere un secreto de al menos 32 bytes fuera del repositorio y agregue `FORMS_SYNC_TOKEN=<secreto>` al archivo `/opt/shutdown-control/.env` existente, manteniendo permisos `600`. Compruebe sólo su presencia, sin imprimir su valor:

```bash
sudo grep -q '^FORMS_SYNC_TOKEN=..' /opt/shutdown-control/.env && echo 'FORMS_SYNC_TOKEN configurado'
```

Para compatibilidad con una VPS aún no preparada, Compose permite arrancar sin esa variable; en ese estado el dashboard y la importación manual siguen disponibles, pero `/api/sync/forms` falla de forma segura con HTTP `503`. No active el flujo de Power Automate hasta configurar el secreto en ambos extremos y contar con autorización ITS. Este cambio conserva el puerto interno `8080` y el enlace local `127.0.0.1:2000` usado por Nginx.

## GitHub, trazabilidad y despliegue automático

GitHub es la fuente única del código. Cada `push` a `main` despliega **ese commit exacto** en la VPS mediante `.github/workflows/deploy-vps.yml`. La base SQLite permanece en el volumen Docker `shutdown_data` y nunca se guarda en Git.

### Configuración inicial local

```powershell
git init -b main
git add .
git commit -m "chore: version inicial"
git remote add origin https://github.com/ORGANIZACION/REPOSITORIO.git
git push -u origin main
```

Antes de empezar una modificación, ejecute:

```powershell
.\scripts\sync-before-work.ps1
```

El comando exige un árbol limpio y usa `pull --ff-only`: nunca mezcla ni sobrescribe cambios silenciosamente. Trabaje en una rama, abra un Pull Request y fusione a `main`; esa fusión dispara el despliegue.

### Preparación única de la VPS

```bash
sudo git clone git@github.com:ORGANIZACION/REPOSITORIO.git /opt/shutdown-control
cd /opt/shutdown-control
sudo cp .env.example .env
sudo chmod 600 .env
# Edite .env, luego haga el primer arranque:
sudo docker compose up -d --build
```

El usuario usado por GitHub Actions debe poder leer `/opt/shutdown-control`, ejecutar Docker y conectarse por llave SSH. En GitHub, cree el environment `production` y estos secretos:

- `VPS_HOST`: dominio o IP de la VPS.
- `VPS_PORT`: puerto SSH (normalmente `22`).
- `VPS_USER`: usuario de despliegue, sin contraseña interactiva.
- `VPS_SSH_KEY`: llave privada exclusiva para despliegue.
- `VPS_KNOWN_HOSTS`: salida verificada de `ssh-keyscan -H DOMINIO_O_IP`.

No use `StrictHostKeyChecking=no` ni guarde `.env`, contraseñas o llaves en el repositorio. `deploy-history.log` en la VPS registra fecha y commit de cada despliegue; el historial de Actions conserva además actor, resultado y logs. Si la VPS contiene cambios manuales, el despliegue se detiene para proteger la trazabilidad.

No exponga directamente el puerto interno del contenedor ni el enlace local `127.0.0.1:2000` a internet; el acceso público debe permanecer administrado por Nginx. El login local todavía no proporciona permisos granulares ni SSO; para producción corporativa se recomienda Entra ID, VPN, controles de auditoría y políticas de retención.

## Login de la plataforma

En el primer arranque, `DASHBOARD_USER` y `DASHBOARD_PASSWORD` crean la cuenta administradora inicial. La contraseña se almacena en SQLite únicamente como una derivación PBKDF2 con salt aleatoria. En los reinicios posteriores, cambiar `DASHBOARD_PASSWORD` en `.env` actualiza la contraseña de esa cuenta y revoca sus sesiones anteriores.

El login usa una cookie `HttpOnly` y `SameSite=Lax`, con expiración renovable por inactividad. En producción `SESSION_COOKIE_SECURE` debe permanecer en `true` y el acceso debe realizarse exclusivamente mediante HTTPS. Para una prueba local por HTTP se puede establecer temporalmente `SESSION_COOKIE_SECURE=false`; no utilice ese valor en la VPS.

- `SESSION_IDLE_MINUTES`: duración de inactividad permitida; predeterminado `30`.
- `LOGIN_MAX_ATTEMPTS`: intentos fallidos antes del bloqueo; predeterminado `5`.
- `LOGIN_LOCK_MINUTES`: duración del bloqueo; predeterminado `15`.

El token de Power Automate continúa siendo independiente y no utiliza las sesiones del dashboard.

## Planificación y Carta Gantt

El módulo independiente **Planificación y Gantt** (`/plan`) recibe una Carta Gantt en Excel `.xlsx` o en XML exportado desde Microsoft Project. Cada importación crea una versión nueva y conserva las anteriores en SQLite; sólo la última queda activa. La carga está restringida a administradores y planificadores, mientras que los demás roles pueden consultar la versión activa.

Excel debe incluir como mínimo las columnas `OT`, `OP` o `Actividad`, y `Nombre de tarea`. También reconoce WBS/EDT, área, inicio, término, fechas de línea base, duración, peso, criticidad, hitos y predecesoras. Project XML puede obtener OT, OP, área y peso desde campos personalizados con esos alias; si OT y OP no están en campos personalizados, intenta reconocer textos como `OT 419433450 - OP 020` en el nombre de la tarea.

El módulo importa también las actividades que no tienen OT u OP y las muestra con estado **Sin OT/OP**, porque siguen formando parte del trabajo planificado. Esas actividades no pueden vincularse automáticamente hasta contar con ambos identificadores. Para las demás, vincula cada tarea con el último reporte real que tenga la misma combinación `OT + OP`. No calcula todavía avance planificado, desviación ni proyección de término: esos cálculos se habilitarán después de validar la estructura y calendarios de una exportación real de Microsoft Project.

### Usuarios y permisos

El menú principal agrupa Vista general, Órdenes de trabajo y Bitácora dentro de **Centro operacional** (`/`). La cuenta administradora dispone además del módulo independiente **Usuarios** (`/users`), desde donde puede crear cuentas identificadas por correo electrónico, cambiar roles, activar o bloquear accesos y restablecer contraseñas. Los correos se normalizan en minúsculas y no pueden repetirse. Este módulo no forma parte del dashboard operacional y el servidor impide acceder a su página, JavaScript y API sin rol de administrador. Una cuenta bloqueada pierde inmediatamente todas sus sesiones. El sistema impide desactivar o degradar la propia cuenta administradora y también impide dejar la plataforma sin administradores activos.

| Rol | Acceso |
| --- | --- |
| Administrador | Dashboard, importación manual y administración de usuarios. |
| Planificador | Dashboard e importación manual. |
| Supervisor | Consulta completa del dashboard. |
| Visualizador | Consulta de solo lectura. |

La contraseña de la cuenta administradora inicial se cambia exclusivamente mediante `DASHBOARD_PASSWORD` en `.env`. Las contraseñas de las demás cuentas pueden restablecerse desde la sección Usuarios; al hacerlo, sus sesiones abiertas se revocan.

## Integración oficial mediante Power Automate

Power Automate es el mecanismo oficial de sincronización con el archivo de reportabilidad. El flujo obtiene las filas mediante Excel Online (Business) y las envía a `POST /api/sync/forms` usando el token independiente descrito anteriormente.

Los reportes pueden contener datos personales, reseñas operacionales y fotografías de faena. Verifique la autorización de BHP/ITS para procesar y replicar esos datos en la VPS antes de activar el flujo con información real.

## Criterios de cálculo

- Una OT es identificada por su número. La última actualización se elige por fecha/hora de finalización, luego ID.
- El porcentaje mostrado es el registrado en el último reporte. El promedio de porcentajes de últimas OT, cuando exista, es **referencial**, no avance físico ponderado del proyecto.
- Los registros con porcentajes inválidos o incompletos quedan en bitácora sin entrar al promedio.
- La columna de horas se muestra conforme se reporta, sin asumir si corresponde a horas de ejecución u horas-hombre.
- Un comentario de bloqueo en avance 0 genera una alerta de revisión, pero no determina por sí solo su criticidad operacional.

## Próxima fase

Incorporar el plan maestro de OT y sus ponderaciones para calcular avance físico planificado versus reportado. Después: criticidad, fechas planificadas, alertas por SLA y reportes por turno.
