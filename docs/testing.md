# Prueba de la entrega 3 desde Swagger

Esta guía prueba la API nacional en forma independiente. No requiere abrir Bonita ni RescueSync y no inicia procesos BPM.

## Preparación

1. Elegí el ambiente: para usar Render no hace falta levantar Docker local. Para probar en tu computadora, seguí el README y esperá que `docker compose up --build -d --wait` termine sin errores.
2. Abrí [Swagger en Render](https://rescuesync-national-api.onrender.com/docs) o <http://localhost:8010/docs>, según el ambiente elegido. Las cuentas y los registros son distintos en cada base.
3. Ejecutá `GET /ready`: debe responder `200` y `{"status":"ready"}`.
4. Ejecutá `GET /api/v1/certifications`: debe incluir `LOGISTICS`.

Los ejemplos crean datos de demostración. Usá correos, códigos de inventario y referencias nuevos en cada ejecución; cerrá las adjudicaciones al terminar. No suspendas ONG ni cambies recursos de otra persona para probar errores. Los correos y contraseñas de bootstrap se obtienen de la configuración privada del ambiente.

La API aplica [límites de solicitudes](request-limits.md). Si devuelve `429`, esperá los segundos de `Retry-After`. Cambiar de JWT no reinicia la cuota y varios integrantes pueden compartirla detrás de un proxy. Las [evidencias guardadas](evidence/README.md) describen las pruebas ya realizadas y los datos que dejaron.

## Registrar y autenticar una ONG

5. Ejecutá `POST /api/v1/ngos` con este cuerpo (cambiá el correo si ya lo usaste):

```json
{
  "name": "Asistencia Solidaria La Plata",
  "email": "asistencia.laplata@example.org",
  "password": "DemoONG-LaPlata-2026",
  "certification_codes": ["LOGISTICS"]
}
```

Debe devolver `201`, un `id` UUID y estado `PENDING_REVIEW`. Guardá ese UUID: en los ejemplos siguientes reemplazá `UUID_ONG` por ese valor real, sin cambiar las comillas.

6. Ejecutá `POST /api/v1/auth/login`:

```json
{
  "email": "asistencia.laplata@example.org",
  "password": "DemoONG-LaPlata-2026"
}
```

7. Copiá `access_token`, pulsá **Authorize**, pegá el token (sin comillas ni el prefijo `Bearer`), confirmá y cerrá el diálogo.
8. Ejecutá `GET /api/v1/auth/me`: debe mostrar rol `NGO` y el mismo `ngo_id`.
9. Ejecutá `GET /api/v1/ngos/me/competencies`: `LOGISTICS` debe estar pendiente, sin riesgos permitidos y con `enabled: false`.

## Cargar inventario y observar restricciones

10. Con el JWT de la ONG, ejecutá `POST /api/v1/resources`:

```json
{
  "ngo_id": "UUID_ONG",
  "code": "LP_WATER",
  "name": "Agua potable",
  "category": "SUPPLIES",
  "unit": "litros",
  "total_quantity": 100,
  "required_certification_code": "LOGISTICS"
}
```

Debe devolver `201`, disponibilidad 100 y cantidad comprometida 0. El código es único globalmente: usá otro si repetís esta demostración.

11. Ejecutá `POST /api/v1/resources/validate`:

```json
{
  "resource_code": "LP_WATER",
  "quantity": 25,
  "risk_level": "LOW"
}
```

Debe responder `200`, `fulfillable_quantity: 0`, `ONG_NOT_ENABLED` y `CERTIFICATION_NOT_VERIFIED`. La respuesta describe capacidades/restricciones y no reserva nada.

## Habilitar la ONG y verificar su certificación

12. Iniciá sesión mediante `POST /api/v1/auth/login` con el correo y contraseña que configuraste en `BOOTSTRAP_ADMIN_EMAIL` y `BOOTSTRAP_ADMIN_PASSWORD`.
13. Reemplazá el token en **Authorize** por el JWT administrativo. Si hace falta, pulsá **Logout** dentro del diálogo antes de pegarlo.
14. Confirmá con `GET /api/v1/auth/me` que el rol sea `ADMIN`.
15. Ejecutá `GET /api/v1/ngos`, con `status: PENDING_REVIEW`. Buscá tu ONG.
16. Ejecutá `PATCH /api/v1/ngos/{ngo_id}/status`, usando su UUID en el parámetro de ruta:

```json
{"status": "ENABLED"}
```

17. Ejecutá `PUT /api/v1/ngos/{ngo_id}/certifications/{certification_code}`, con el mismo UUID y `LOGISTICS`:

```json
{
  "status": "VERIFIED",
  "allowed_risk_levels": ["LOW", "MEDIUM"]
}
```

Debe mostrar la competencia habilitada con esos riesgos. Habilitar una ONG y verificar su certificación son decisiones separadas; ambas son necesarias para adjudicar este recurso.

## Validar y adjudicar desde coordinación

18. Iniciá sesión con `BOOTSTRAP_COORDINATOR_EMAIL` y `BOOTSTRAP_COORDINATOR_PASSWORD`, reemplazá el JWT en **Authorize** y verificá rol `COORDINATOR` con `/auth/me`.
19. Ejecutá `POST /api/v1/allocations/validate`:

```json
{
  "ngo_id": "UUID_ONG",
  "resource_code": "LP_WATER",
  "quantity": 25,
  "risk_level": "LOW"
}
```

Debe devolver cantidad aprovechable 25, `SUFFICIENT`, riesgos `LOW`/`MEDIUM` y ninguna restricción. Probá `HIGH`: debe informar `RISK_LEVEL_NOT_ALLOWED` sin borrar las competencias permitidas. Volvé a `LOW` para continuar.

20. Generá un UUID para `Idempotency-Key`, por ejemplo con `python -c "import uuid; print(uuid.uuid4())"`, y guardalo.
21. Ejecutá `POST /api/v1/allocations`, completando ese encabezado y este cuerpo:

```json
{
  "project_reference": "EMERGENCY-DEMO-001",
  "ngo_id": "UUID_ONG",
  "risk_level": "LOW",
  "items": [{"resource_code": "LP_WATER", "quantity": 25}]
}
```

Debe devolver `201`, estado `ACTIVE` y un `id` de adjudicación. Guardá ese ID.

22. Ejecutá `GET /api/v1/resources/LP_WATER`: debe mostrar total 100, comprometido 25 y disponible 75.
23. Repetí exactamente el paso 21 con la misma clave: debe devolver `200`, el mismo ID y conservar 25 comprometidos. Cambiar cantidades con esa misma clave debe dar `409`.
24. Ejecutá `GET /api/v1/allocations` con `project_reference: EMERGENCY-DEMO-001`: debe aparecer la adjudicación. También podés consultarla por ID.

## Liberar y comprobar idempotencia

25. Ejecutá `POST /api/v1/allocations/{allocation_id}/close` usando el ID guardado y declarando cuánto se consumió de cada recurso:

```json
{
  "items": [{"resource_code": "LP_WATER", "quantity": 15}]
}
```

El body debe incluir exactamente los recursos adjudicados; cada cantidad puede ser cero, pero no superar lo adjudicado. La respuesta debe devolver `CLOSED`, `closed_at` y `consumed_quantity: 15` en el ítem.
26. Consultá el recurso: debe tener total 85, 0 comprometidos y 85 disponibles. Las 15 unidades consumidas salen del total; las otras 10 previamente comprometidas se liberan.
27. Repetí el cierre con el mismo body: mantiene `CLOSED` y el inventario en 85 disponibles. Un consumo distinto para una adjudicación ya cerrada devuelve `409`.

## Casos adicionales

- **Varias ONG en una emergencia:** repetí el registro, habilitación, certificación e inventario con otro correo y otro `code`. Adjudicá a la segunda ONG usando la misma `project_reference` y una nueva `Idempotency-Key`. Debe devolver `201`; el listado por proyecto debe mostrar ambas. Cada adjudicación se cierra por separado.
- **Stock insuficiente:** para un proyecto nuevo, pedí 101 unidades del recurso con total 100. Debe devolver `409` con `INSUFFICIENT_STOCK`, sin comprometer ninguna cantidad.
- **Inventario ajeno:** validar con el JWT de otra ONG devuelve `RESOURCE_NOT_OWNED`; intentar editarlo devuelve `403`. Adjudicar el stock a la ONG incorrecta devuelve `409`.
- **Permisos:** una ONG o coordinación intentando habilitar ONG devuelve `403`. Sin JWT, las operaciones privadas devuelven `401`. Una ONG no puede darse de alta como `ADMIN` ni adjudicar recursos.
- **Suspensión:** administración puede enviar `{"status":"SUSPENDED"}`. Las nuevas adjudicaciones quedan bloqueadas, pero las activas se pueden cerrar.
- **Cambio de cantidades:** `PUT /api/v1/resources/{resource_code}` recibe `{"total_quantity":120,"is_active":true}`. Nunca permite un total menor que lo comprometido.
- **Revocación:** administración puede enviar `{"status":"REJECTED","allowed_risk_levels":[]}` al endpoint de certificación. No se permite verificar sin riesgos ni asignar riesgos a una certificación rechazada o pendiente.

## Prueba automática contra la API levantada

El script usa la biblioteca estándar de Python y crea dos ONG de demostración, inventarios y adjudicaciones. No elimina esos registros: termina con ambas adjudicaciones cerradas, registra consumo parcial y comprueba el stock remanente.

Configurá estas variables en tu terminal con las cuentas de tu ambiente:

```sh
export NATIONAL_URL=http://localhost:8010
export NATIONAL_ADMIN_EMAIL=admin@example.org
export NATIONAL_ADMIN_PASSWORD='contraseña-administrativa-de-tu-env'
export NATIONAL_COORDINATOR_EMAIL=coordination@example.org
export NATIONAL_COORDINATOR_PASSWORD='contraseña-de-coordinación-de-tu-env'
python scripts/smoke_test.py
```

En PowerShell, usá `$env:NATIONAL_URL="http://localhost:8010"` y el mismo formato `$env:NOMBRE="valor"` para las otras variables. Para usar Render, cambiá `NATIONAL_URL` a `https://rescuesync-national-api.onrender.com` y usá las cuentas de ese ambiente.

El script debe imprimir `"result": "ok"` y los identificadores creados, sin contraseñas ni JWT. Reproduce el recorrido principal con dos ONG; no ejecuta todas las comprobaciones de permisos registradas en el informe histórico ni la suite de concurrencia. No reintenta automáticamente respuestas `429`: esperá la cuota antes de repetir una prueba. No ejecutes la suite de pytest contra la base pública.
