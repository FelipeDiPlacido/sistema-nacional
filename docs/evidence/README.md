# Evidencias de verificación del despliegue

Fecha: **21/10/2026**, zona horaria de Argentina (**GMT-3**).

Ambiente: <https://rescuesync-national-api.onrender.com>, API nacional en Render con PostgreSQL. Se realizaron solicitudes HTTP desde fuera del proveedor. No se ejecutaron procesos Bonita ni acciones automáticas desde RescueSync.

## Circuito funcional

El [informe JSON de la verificación funcional](2026-10-21-cloud-verification.json) conserva el resultado combinado de **38 comprobaciones**:

| Etapa | Comprobaciones | Alcance |
| --- | --- | --- |
| Administración y ONG | 22 | Registro pendiente, identidad, rechazo de autoaprobación, inventario, habilitación, certificaciones, riesgos, adjudicaciones de dos ONG para un proyecto y cierres idempotentes |
| Coordinación | 16 | Identidad `COORDINATOR`, consultas, validación, reserva y liberación; rechazo de administración de ONG, revisión de certificaciones y edición de inventario |

Resultado: `ok`, sin errores de limpieza. El informe fue copiado del registro generado durante la prueba; no contiene contraseñas, JWT, claves de Render ni URLs privadas de base de datos.

Referencia de código revisada durante esa sesión: [`71f6ee3`](https://github.com/FelipeDiPlacido/sistema-nacional/commit/71f6ee3c846b2630a80d984cb8f0e14bdcf82e49). La API no expone el hash desplegado: esta referencia identifica el código local revisado, no una comprobación independiente del hash del contenedor remoto.

### Datos de demostración conservados

- Proyecto `CLOUD-DEMO-DFEE32D2F7`: dos ONG habilitadas, un recurso de 10 litros por ONG y dos adjudicaciones cerradas. Cada recurso pasó de 10 disponibles a 4 y volvió a 10, con 0 comprometidos.
- Proyecto `CLOUD-COORD-CD051D7C61`: una adjudicación adicional cerrada por coordinación. Usó el primer recurso, cuya disponibilidad pasó de 10 a 7 y volvió a 10.
- Quedó una ONG suspendida de la comprobación preliminar de permisos, identificada en `preliminary_ngo_suspended`, sin adjudicaciones.

Los UUID de ONG y adjudicaciones y los códigos de recursos están en el JSON. Son referencias históricas: si se modifica o reinicia la base, los resultados actuales pueden diferir. Las cuentas de ONG de prueba usaron contraseñas aleatorias que no forman parte de la evidencia.

## Límites de solicitudes

Las 38 comprobaciones anteriores se realizaron **antes** de agregar límites de solicitudes. No constituyen una prueba de resistencia a ataques ni de concurrencia en la nube.

La protección se validó localmente en Docker con **79 pruebas aprobadas**, Ruff y `alembic check`, sobre una base PostgreSQL efímera dedicada. El [extracto del resultado de la suite](2026-10-21-request-limits-local-tests.txt) conserva la salida relevante. Incluye comprobaciones de concurrencia sobre PostgreSQL y 17 casos adicionales de límites, contando la parametrización por motor de base de datos.

El [informe de verificación pública de los límites](2026-10-21-cloud-request-limits.json) registra por separado las comprobaciones posteriores al despliegue: diez solicitudes de login con JSON vacío devolvieron `422`, la undécima devolvió `429` con `Retry-After: 56`, y después de esperar volvió a responder `422`. Esto confirma que el bloqueo es temporal. `/health` y `/ready` mantuvieron `200` durante el bloqueo.

La comprobación pública no creó cuentas ni adjudicaciones y no agotó la cuota de registro. Las cuotas de registro y concurrencia se verificaron en las pruebas locales. Ver [cómo funcionan y se configuran las cuotas](../request-limits.md).

## Repetir las pruebas

La [guía de Swagger](../testing.md) contiene los parámetros y ejemplos de cada paso. El [script HTTP](../../scripts/smoke_test.py) repite el recorrido principal con dos ONG y credenciales leídas del entorno. Ninguno requiere disponer de los tokens usados en la sesión original.

Las 38 comprobaciones son observaciones funcionales por HTTP; no son 38 casos de pytest ni incluyen todas las pruebas automatizadas locales. No se probó la integración RescueSync/Bonita ni se realizó una prueba de carga sobre Render.
