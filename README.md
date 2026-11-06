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
- `success_with_errors`: HTTP `200`; las filas válidas se importaron y las filas con Id/área inválidos u otro problema estructural se omitieron. La respuesta informa `rejected` y el detalle en `errors`.
- `rejected`: HTTP `422`; todas las filas recibidas tienen errores estructurales y no existe ningún reporte o pendiente válido para guardar.

Si Power Automate envía el mismo `Id` más de una vez dentro del lote, la API conserva la primera aparición e ignora las siguientes aunque su contenido difiera. La respuesta informa la cantidad en `duplicates_ignored`; el ID no se duplica en SQLite y los demás reportes del lote se guardan normalmente.

Las filas completamente vacías que permanezcan dentro de la tabla de Excel después de borrar registros se ignoran y se informan en `blank_rows_ignored`. No se inventan ni renumeran IDs. Una fila que contiene datos pero no tiene `Id` se considera una inconsistencia real, se omite y aparece en `errors`; las demás filas válidas se conservan.

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

Excel debe incluir como mínimo las columnas `OT`, `OP` o `Actividad`, y `Nombre de tarea`. También reconoce WBS/EDT, área, inicio, término, fechas de línea base, duración, **HH planificadas**, peso, criticidad, hitos y predecesoras. Project XML puede obtener esos campos desde atributos personalizados; si OT y OP no están disponibles, intenta reconocer textos como `OT 419433450 - OP 020` en el nombre de la tarea.

El módulo importa también las actividades que no tienen OT u OP, porque siguen formando parte del trabajo planificado. Las identifica con una advertencia de calidad de datos y permite filtrarlas; no pueden vincularse automáticamente hasta contar con ambos identificadores. Para las demás, acumula todos los incrementos válidos que tengan la misma combinación `OT + OP`.

La primera fase de control operacional clasifica las actividades según la hora local del navegador en **vencidas**, **en ejecución**, **sin reporte iniciado**, **próximas 12 horas**, **programadas** y **completadas**. Incluye KPI y filtros por estado, área, turno, fecha y texto. Los estados se calculan usando inicio, término y avance real; una actividad completada requiere 100% reportado.

La segunda fase agrega una estimación de **avance planificado versus real**, desviación en puntos porcentuales y horas de duración equivalentes, semáforo del programa, proyección lineal de término y clasificación de actividades adelantadas, atrasadas o en línea. El avance global se pondera por la duración de cada actividad. Como el Excel operativo actual no contiene línea base, pesos ni calendarios, estos indicadores se presentan explícitamente como estimaciones lineales; una exportación XML completa de Microsoft Project permitirá refinar el cálculo en una fase posterior.

La tercera fase incorpora una **Curva S** que reconstruye el avance real histórico desde los reportes de terreno y lo compara con la curva planificada. También presenta SPI estimado, área con mayor desviación, lookahead de 12 horas y una tabla de desempeño por área. El historial se obtiene directamente de los reportes asociados a las OT/OP de la versión activa, sin modificar la fuente importada.

### Fórmulas compartidas de avance

- `Avance OP original = Σ incrementos válidos`. El valor original se conserva; el indicador visible se limita a 100%.
- `Avance OT = Σ(peso OP × avance OP) / Σ(pesos OP)`.
- `Avance global = Σ(peso OP × avance OP) / Σ(pesos OP)`.
- La ponderación usa una sola metodología coherente por conjunto: HH planificadas si todas las OP las poseen; en caso contrario, duración si está completa; finalmente ponderación uniforme.
- `SPI = avance real / avance planificado` cuando el programado es mayor que cero.
- `HH ejecutadas estimadas = Σ(trabajadores × horas reportadas)`; nunca se utilizan como peso planificado.
- Cada corte histórico excluye reportes posteriores. La proyección base usa el promedio de incrementos positivos de los últimos tres cortes; los escenarios optimista y conservador aplican ±20% al ritmo base.
- La zona horaria de cálculo es `America/Santiago`.
- El botón **Corte BHP · 06-11 08:00** aplica exactamente la referencia del 6 de noviembre de 2026 a las 08:00 de Chile. Las versiones Gantt importadas antes de incorporar `planned_hh` deben reimportarse una vez para poblar la columna HH; la migración no inventa ese dato.

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

## Criterios de auditoría

- El Id de Microsoft Forms es la identidad del incremento. Reimportar un Id idéntico no duplica avance.
- Los filtros `Requieren revisión` y `Sin avance` evalúan el último reporte de cada combinación OT + OP. Las alertas antiguas permanecen en el historial para auditoría, pero dejan de representar el estado vigente cuando un reporte posterior válido resuelve la actividad.
- El mismo Id con contenido diferente conserva una auditoría en `report_conflicts` y queda informado para revisión.
- Una suma por OP superior a 100% se registra en `operation_anomalies`; la interfaz muestra máximo 100% sin perder el valor recibido.
- Registros con OT/OP que no existen en la planificación activa se conservan con alerta de revisión.
- Un comentario de bloqueo con avance 0 genera una alerta, pero no determina por sí solo criticidad operacional.
- La auditoría técnica previa a esta corrección está documentada en `docs/AUDITORIA_MODELO_OT_OP_2026-11-06.md`.
