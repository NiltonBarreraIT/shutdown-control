# Shutdown Control — BHP Spence

Primera versión operativa para seguimiento de órdenes de trabajo a partir del formulario Excel de reportabilidad de BHP Spence. Funciona en VPS Ubuntu con Docker y no modifica el archivo original.

## Funciones disponibles

- Importación manual de `.xlsx` (hasta 15 MB), sin enviar archivos a servicios externos.
- Consolidación por número de OT: última actualización e historial completo de reportes.
- Filtros por área, turno y estado; búsqueda por OT, actividad, responsable y comentario.
- Visualización de reseñas técnicas y enlaces a fotografías (dependen de permisos Microsoft 365).
- Alertas de datos inconsistentes y registros sin avance.
- Persistencia SQLite y actualización por ID de formulario (reimportar no duplica reportes).
- Acceso con autenticación básica, para utilizar exclusivamente detrás de HTTPS.

## Instalación VPS

1. Copie esta carpeta a la VPS.
2. Duplique `.env.example` como `.env` y establezca credenciales únicas y robustas. No reutilice su contraseña Microsoft 365.
3. Ejecute `docker compose up -d --build`.
4. Configure Nginx como proxy inverso hacia `http://127.0.0.1:8086` con certificado TLS (HTTPS) y restricción de acceso adecuada, idealmente VPN.
5. Ingrese al dominio configurado y seleccione **Actualizar Excel** para cargar el archivo de reportabilidad.

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
