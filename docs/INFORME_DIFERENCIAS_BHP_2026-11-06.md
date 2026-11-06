# Informe de diferencias frente a BHP Spence

Fecha de referencia: 2026-11-06 08:00, hora de Chile.

## Comparación disponible antes de la corrección

| Indicador | BHP | Plataforma anterior | Diferencia plataforma - BHP |
| --- | ---: | ---: | ---: |
| Programado | 66,90% | 67,80% | +0,90 pp |
| Real | 52,31% | 20,70% | -31,61 pp |
| Desviación | -14,59 pp | -47,10 pp | -32,51 pp |
| SPI | 0,782 | 0,31 | -0,472 |

El programado era cercano, pero el real estaba severamente subestimado porque se utilizaba únicamente el último porcentaje de cada OT/OP. La corrección acumula todos los incrementos válidos por OP.

## Diferencia de universo

| Control | BHP | Excel ITS disponible | Cobertura ITS |
| --- | ---: | ---: | ---: |
| Actividades | 3.449 | 288 | 8,35% |
| HH programadas | 46.524,5 | 2.735,5 | 5,88% |

Faltan 3.161 actividades y 43.789,0 HH respecto de la referencia. Por ello, incluso con el modelo matemático corregido, no corresponde exigir igualdad de porcentajes mientras ambos sistemas no utilicen el mismo alcance.

## Resultado posterior a la corrección

El repositorio local no contiene una copia de la base productiva ni sus reportes reales. Además, la instrucción prohíbe desplegar sin autorización. En consecuencia, no es técnicamente válido informar un porcentaje corregido contra BHP todavía.

Después de desplegar con autorización se debe:

1. Reimportar la versión Gantt para poblar `planned_hh` desde la columna HH.
2. Ejecutar la sincronización completa de Power Automate sin modificar los Id de Forms.
3. Consultar el corte exacto `2026-11-06 08:00 America/Santiago`.
4. Comparar cobertura de OT/OP, HH y registros faltantes antes de comparar porcentajes.

## Fuentes probables de diferencia restantes

- Universo de planificación ITS mucho menor que el universo BHP.
- Posibles OT/OP presentes en Forms pero ausentes en la versión Gantt.
- Definición contractual de HH y pesos no disponible para todo el shutdown.
- Calendarios, restricciones y línea base completa de Microsoft Project no disponibles.
- La fórmula oficial del indicador “máximo alcanzable” de BHP no fue entregada; la plataforma usa una estimación explícita basada en ritmo reciente.
- HH ejecutadas se estiman como trabajadores × horas reportadas y requieren validación operacional.

No se introdujo ningún factor artificial para aproximar los resultados a BHP.

