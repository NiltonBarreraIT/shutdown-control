# Auditoría del modelo OT/OP y Curva S

Fecha de revisión: 2026-11-06 (hora de Chile)

## Arquitectura observada

- Aplicación monolítica Python (`app.py`) con SQLite, servidor HTTP nativo y frontend HTML/JavaScript sin framework.
- `POST /api/sync/forms` recibe filas desde Power Automate, normaliza encabezados de Microsoft Forms y ejecuta un UPSERT por `reports.id`.
- `reports` conserva una fila por identificador de origen. `ot` y `activity` son atributos de esa fila; no existen tablas maestras separadas para OT y OP.
- La Gantt se versiona en `plan_versions` y `plan_tasks`. Cada tarea contiene OT/OP, fechas, duración y otros atributos opcionales.
- Autenticación, sesiones e integración Power Automate están aisladas de los cálculos de planificación y no requieren cambios destructivos.

## Hallazgos obligatorios

1. **Avance real incorrecto:** `plan_payload()` vinculaba sólo el reporte más reciente de cada OT+OP y exponía su porcentaje como `actual_progress`. En el dashboard, `payload()` también resumía por el último reporte de la OT. Esto implementaba una fotografía, no incrementos acumulables.
2. **Curva S incorrecta:** el frontend reconstruía el real histórico seleccionando el último porcentaje disponible antes de cada corte. Debe sumar todos los incrementos válidos hasta el corte y limitar sólo la presentación a 100%.
3. **OT/OP sin consolidación compartida:** una OT con varias OP no tenía una función backend única de ponderación. Los módulos podían mostrar cifras distintas bajo el mismo nombre.
4. **Ponderación incompleta:** la Gantt importaba duración, pero no la columna `HH` del Excel operativo. Por eso no podía aplicar la prioridad HH planificadas → duración → uniforme.
5. **Duplicados:** SQLite evita duplicar el mismo Id porque `reports.id` es clave primaria y el endpoint rechaza Id repetidos dentro de un lote. Sin embargo, un reenvío con el mismo Id y contenido diferente sobrescribe campos operacionales sin conservar la versión anterior ni clasificar el conflicto de contenido.
6. **Anomalías de avance:** porcentajes individuales fuera de 0–100 se marcan inválidos, pero no existía detección de suma incremental superior a 100% por OP.
7. **Fechas:** la planificación se guarda como hora local sin zona; Forms puede aportar UTC (`Z`). El JavaScript anterior ignoraba el sufijo de zona al interpretar fechas. El cálculo compartido debe normalizar todo a `America/Santiago` antes de aplicar el corte.
8. **Universo no comparable con BHP:** el Excel disponible contiene 288 tareas y 2.735,5 HH, mientras la referencia BHP declara 3.449 actividades y 46.524,5 HH. La diferencia de universo impide esperar igualdad directa, incluso con fórmulas corregidas.
9. **Datos ejecutados:** `workers` y `hours` son datos reportados. No deben utilizarse como peso planificado. Las HH ejecutadas sólo pueden estimarse como `workers × hours` por reporte cuando ambos campos son válidos.
10. **Trazabilidad existente útil:** OT original inmutable, pendientes sin OT, discrepancias de OT, sincronización atómica y logs deben conservarse.

## Decisiones de implementación

- Mantener `reports` como registro fuente inmutable por Id y añadir auditoría de conflictos, sin borrar historial.
- Crear funciones backend compartidas para consolidar OP, OT, programa, Curva S y escenarios.
- Acumular incrementos por OT+OP ordenados por fecha; conservar suma original y limitar a 100% sólo el indicador presentado.
- Importar HH planificadas desde Excel/Project cuando estén disponibles. Elegir una sola metodología coherente por conjunto: HH si la cobertura es completa, duración si no, y uniforme como último recurso.
- Calcular cortes históricos sólo con reportes cuya fecha sea menor o igual al corte.
- Mantener el endpoint y su contrato de seguridad; ampliar su respuesta/auditoría sin romper Power Automate.
- No desplegar hasta autorización explícita.

