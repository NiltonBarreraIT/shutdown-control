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
- Acceso con autenticación básica, para utilizar exclusivamente detrás de HTTPS.

## Sincronización desde Power Automate

`POST /api/sync/forms` recibe exactamente el objeto producido por Excel Online (Business):

```json
{"body":{"value":[{"Id":"101","¿Cuál es tu área de trabajo?":"Filtros","Ingresa el número de OT (Filtros)":"450001"}]}}
```

Los nombres de las columnas se normalizan ignorando mayúsculas, tildes, signos, espacios normales y espacios especiales. El área se reconoce desde `¿Cuál es tu área de trabajo?`. Los campos operacionales se localizan por su significado y por el área entre paréntesis; no se usa el orden de las columnas. Esto contempla Filtros, Molibdeno, Tranque y Flotación, incluyendo encabezados como `Carga una fotografía del ANTES del trabajo (Filtros)`, `... DURANTE ...` y `... FINAL ...` para cada área. Cada fila debe incluir `Id`, área y OT. La API acepta hasta 2 MiB y 5.000 filas por solicitud por defecto. Estos límites pueden reducirse mediante `SYNC_MAX_BYTES` y `SYNC_MAX_RECORDS`.

La autenticación de esta ruta es independiente de la contraseña del dashboard. Genere un secreto aleatorio (por ejemplo, con `openssl rand -hex 32`), configúrelo sólo en el `.env` de cada entorno y envíelo desde Power Automate como encabezado:

```text
Authorization: Bearer <FORMS_SYNC_TOKEN>
Content-Type: application/json
```

No confirme el `.env` en Git ni incluya el token en URL, cuerpo, nombres de acciones o logs. La sincronización es atómica: si todas las filas son válidas responde HTTP `200` con `status: "success"`; si una o más filas fallan responde HTTP `422` con `status: "rejected"`, `atomic: true`, `upserted: 0` y errores por posición. En este último caso no se guarda ninguna fila del lote, por lo que Power Automate puede corregirlo y reintentarlo sin una pérdida parcial silenciosa. El dashboard muestra fecha UTC, fuente, cantidad confirmada y errores de la última sincronización.

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
4. Configure Nginx como proxy inverso hacia `http://127.0.0.1:8086` con certificado TLS (HTTPS) y restricción de acceso adecuada, idealmente VPN.
5. Ingrese al dominio configurado y seleccione **Actualizar Excel** para cargar el archivo de reportabilidad.

### Preparación previa del secreto en la VPS

Antes de fusionar o desplegar esta versión, genere un secreto de al menos 32 bytes fuera del repositorio y agregue `FORMS_SYNC_TOKEN=<secreto>` al archivo `/opt/shutdown-control/.env` existente, manteniendo permisos `600`. Compruebe sólo su presencia, sin imprimir su valor:

```bash
sudo grep -q '^FORMS_SYNC_TOKEN=..' /opt/shutdown-control/.env && echo 'FORMS_SYNC_TOKEN configurado'
```

Para compatibilidad con una VPS aún no preparada, Compose permite arrancar sin esa variable; en ese estado el dashboard y la importación manual siguen disponibles, pero `/api/sync/forms` falla de forma segura con HTTP `503`. No active el flujo de Power Automate hasta configurar el secreto en ambos extremos y contar con autorización ITS. Este cambio conserva el puerto interno `8080` y no modifica el puerto externo `2000` administrado por Nginx.

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

No exponga directamente el puerto 8086 a internet. El contenedor publica únicamente en localhost. La autenticación básica del MVP no proporciona permisos granulares ni SSO; para producción corporativa se recomienda Entra ID, VPN, controles de auditoría y políticas de retención.

## Integración OneDrive — etapa pendiente

Actualmente **no está conectada**. La cuenta corporativa con acceso al archivo no equivale a autorización para integrar una app. Solicitar a TI de ITS el registro/aprobación de una aplicación Microsoft Entra ID con permisos de lectura mínimos para el documento correspondiente y OAuth delegado (o acceso de aplicación controlado si lo aprueban). La sincronización futura consultará Microsoft Graph, descargará una copia del Excel autorizado y llamará a la función `import_xlsx`. No introducir usuarios ni contraseñas de Microsoft en scripts o `.env`.

Los reportes pueden contener datos personales, reseñas operacionales y fotografías de faena. Verifique la autorización de BHP/ITS para replicar esos datos fuera de Microsoft 365 antes de cargar información real a una VPS.

## Criterios de cálculo

- Una OT es identificada por su número. La última actualización se elige por fecha/hora de finalización, luego ID.
- El porcentaje mostrado es el registrado en el último reporte. El promedio de porcentajes de últimas OT, cuando exista, es **referencial**, no avance físico ponderado del proyecto.
- Los registros con porcentajes inválidos o incompletos quedan en bitácora sin entrar al promedio.
- La columna de horas se muestra conforme se reporta, sin asumir si corresponde a horas de ejecución u horas-hombre.
- Un comentario de bloqueo en avance 0 genera una alerta de revisión, pero no determina por sí solo su criticidad operacional.

## Próxima fase

Obtener URL del archivo original, responsable de TI y aprobación para Graph. Después: sincronización automática, roles/SSO, plan maestro de OT y ponderaciones, criticidad, fechas planificadas, alertas por SLA y reportes por turno.
