# Sistema Nacional de Gestión de Recursos y Riesgos

API externa de RescueSync para registrar ONG, revisar certificaciones y niveles de riesgo, gestionar inventario por ONG y comprometer/liberar recursos con transacciones y bloqueos en PostgreSQL. Mantiene la implementación inicial de Felipe y completa el circuito administrativo por API.

La integración automática con RescueSync y Bonita y la publicación de una URL cloud siguen pendientes. Las ofertas y sus borradores permanecen en RescueSync; este servicio guarda inventario, capacidades y adjudicaciones.

## Levantar el ambiente

Requiere Docker con Compose. Desde este repositorio:

```sh
cp .env.example .env
```

En PowerShell: `Copy-Item .env.example .env`.

Editá `.env` antes de iniciar:

| Variable | Uso |
| --- | --- |
| `POSTGRES_PASSWORD` | Contraseña de la base local; usá una cadena alfanumérica aleatoria para evitar caracteres especiales en la URL de Compose |
| `JWT_SECRET` | Secreto aleatorio de al menos 32 caracteres para firmar JWT |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Duración del JWT; 60 por defecto |
| `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD` | Cuenta de administración nacional; contraseña de al menos 12 caracteres |
| `BOOTSTRAP_COORDINATOR_EMAIL`, `BOOTSTRAP_COORDINATOR_PASSWORD` | Cuenta técnica de coordinación para adjudicar y, más adelante, integrar Bonita; contraseña de al menos 12 caracteres |
| `DATABASE_URL` | URL para ejecutar Python fuera de Docker; Compose configura la URL interna automáticamente |

Los correos de ejemplo son ficticios. Usá contraseñas diferentes. Podés generar valores con `python -c "import secrets; print(secrets.token_hex(32))"`. No subas `.env` a Git.

```sh
docker compose up --build -d --wait
docker compose logs --tail=50 api
```

La API ejecuta migraciones, carga el catálogo de certificaciones y crea las cuentas configuradas. Los reinicios **no resetean contraseñas ni reactivan cuentas existentes**. Si un correo de bootstrap ya pertenece a una ONG, el inicio falla para evitar elevar sus permisos. La base se conserva en el volumen local.

- Swagger: <http://localhost:8010/docs>
- OpenAPI: <http://localhost:8010/openapi.json>
- Salud: <http://localhost:8010/health>
- Conexión a PostgreSQL: <http://localhost:8010/ready>

## Permisos y autenticación

Todas las operaciones privadas usan `Authorization: Bearer <access_token>`. Obtené el JWT con `POST /api/v1/auth/login` enviando `email` y `password`. En Swagger, pegá el `access_token` en **Authorize**. Al cambiar de rol, reemplazá ese token. `GET /api/v1/auth/me` confirma la cuenta, el rol y, si corresponde, la ONG.

| Rol | Permisos |
| --- | --- |
| `NGO` | Consultar su perfil, crear/actualizar su inventario, validar disponibilidad y consultar/cerrar sus adjudicaciones |
| `ADMIN` | Revisar ONG/certificaciones, administrar inventario y realizar operaciones de coordinación |
| `COORDINATOR` | Consultar competencias, validar y adjudicar recursos de ONG; consultar/cerrar adjudicaciones |

El registro público solo crea cuentas `NGO` pendientes. Una ONG pendiente puede cargar inventario, pero no recibe nuevas adjudicaciones hasta que esté habilitada y tenga las certificaciones y riesgos correspondientes. Suspenderla o revocar certificaciones bloquea nuevos compromisos; las adjudicaciones existentes siguen pudiendo cerrarse para liberar stock.

## Endpoints

Los cuerpos, tipos, valores permitidos y campos obligatorios están en Swagger. La [guía de prueba completa](docs/testing.md) incluye cuerpos JSON y resultados esperados.

| Método | Ruta | Acceso / función |
| --- | --- | --- |
| POST | `/api/v1/ngos` | Público: registrar una ONG pendiente y declarar certificaciones |
| POST | `/api/v1/auth/login` | Público: obtener JWT |
| GET | `/api/v1/auth/me` | Autenticado: identidad y rol |
| GET | `/api/v1/certifications` | Público: catálogo activo |
| GET | `/api/v1/ngos/enabled` | Público: ONG habilitadas con cuenta activa |
| GET | `/api/v1/ngos` | Administración: listar y filtrar por `status`; `offset`/`limit` |
| GET | `/api/v1/ngos/me/competencies` | ONG: perfil propio |
| GET | `/api/v1/ngos/{ngo_id}/competencies` | Coordinación/administración: perfil de una ONG |
| PATCH | `/api/v1/ngos/{ngo_id}/status` | Administración: habilitar, suspender o devolver a revisión |
| PUT | `/api/v1/ngos/{ngo_id}/certifications/{certification_code}` | Administración: verificar/rechazar certificación y establecer riesgos |
| POST | `/api/v1/resources` | ONG propietaria/administración: crear inventario con `ngo_id` obligatorio |
| GET | `/api/v1/resources` | Público: inventario activo, filtro opcional `ngo_id` |
| GET | `/api/v1/resources/{resource_code}` | Público: disponibilidad de un recurso activo |
| PUT | `/api/v1/resources/{resource_code}` | ONG propietaria/administración: actualizar `total_quantity` e `is_active` |
| PATCH | `/api/v1/resources/{resource_code}/owner` | Administración: asignar dueño al inventario antiguo sin propietario ni compromisos |
| POST | `/api/v1/resources/validate` | ONG: evaluar sus capacidades y recursos sin reservar |
| POST | `/api/v1/allocations/validate` | Coordinación/administración: evaluar para un `ngo_id`, sin reservar |
| POST | `/api/v1/allocations` | Coordinación/administración: adjudicar con encabezado `Idempotency-Key` UUID |
| GET | `/api/v1/allocations` | Autenticado: listar; filtro `project_reference`, `offset`/`limit`; ONG solo ve las propias |
| GET | `/api/v1/allocations/{allocation_id}` | Autenticado: consultar adjudicación, con restricción por ONG |
| POST | `/api/v1/allocations/{allocation_id}/close` | ONG propietaria/coordinación/administración: finalizar su participación y liberar sus recursos |

## Reglas de inventario y adjudicación

- Cada registro nuevo de inventario pertenece a una ONG. `code` sigue siendo único en el sistema: dos ONG pueden usar, por ejemplo, `LP_WATER` y `BERISSO_WATER` para sus existencias de agua. No se comparte stock entre ellas.
- `available_quantity = total_quantity - committed_quantity`. No se permite reducir el total por debajo de lo comprometido. Desactivar inventario impide nuevas adjudicaciones y no bloquea la liberación de las existentes.
- La validación devuelve `200` con capacidades, riesgos permitidos, cantidad aprovechable y restricciones (`ONG_NOT_ENABLED`, `RESOURCE_NOT_OWNED`, `CERTIFICATION_NOT_VERIFIED`, `CERTIFICATION_INACTIVE`, `RISK_LEVEL_NOT_ALLOWED`, `INSUFFICIENT_STOCK`). Es informativa y no reserva stock.
- La adjudicación bloquea la ONG y las filas de inventario en orden estable, vuelve a verificar permisos, certificación, riesgos y cantidades, y compromete **todos sus ítems o ninguno**. Si no puede, devuelve `409` con el detalle.
- Puede haber varias ONG adjudicadas a la misma `project_reference`, con una adjudicación por pareja proyecto/ONG. Cada una debe contener todos sus recursos. No hay una transacción conjunta entre las adjudicaciones de distintas ONG.
- Una solicitud nueva devuelve `201`. Repetir la misma `Idempotency-Key` y contenido devuelve `200` sin duplicar compromisos. Reutilizar la clave con otro contenido, o intentar otra adjudicación para el mismo proyecto/ONG, devuelve `409`.
- Cada cierre libera solo la adjudicación indicada, una única vez, y devuelve `CLOSED`. Para terminar un proyecto con varias ONG hay que cerrar cada adjudicación. Una cerrada no se reactiva; repetir su solicitud devuelve la misma adjudicación cerrada.

## Actualizar una instalación anterior

La migración `0004` conserva cuentas, cantidades y adjudicaciones. Todas las cuentas anteriores mantienen el rol `NGO`; no se les conceden privilegios administrativos.

1. Agregá las cuatro variables `BOOTSTRAP_*` a `.env` usando correos diferentes de los de ONG ya registradas.
2. Ejecutá `docker compose up --build -d --wait`.
3. Cambiá los clientes que enviaban `X-National-Admin-Token`: ahora deben iniciar sesión y enviar JWT. `RESOURCE_ADMIN_TOKEN` ya no se usa.
4. Agregá `ngo_id` a las altas de inventario.
5. El inventario anterior queda con `ngo_id: null`, porque la base no permite deducir su dueño. No puede recibir nuevas adjudicaciones hasta asignarlo con `PATCH /resources/{resource_code}/owner`. Si tiene recursos comprometidos, cerrá primero las adjudicaciones existentes. No borres la base para actualizar.

## Pruebas

Con Docker, en una base efímera dedicada (no usa la base de desarrollo):

```sh
docker compose -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
docker compose -f compose.test.yaml down
```

Ejecuta migraciones, `alembic check`, pruebas HTTP tanto con SQLite como con PostgreSQL, migración con datos anteriores y bloqueo concurrente en PostgreSQL. Las pruebas destruyen exclusivamente tablas de la base de pruebas; las pruebas PostgreSQL exigen una URL cuyo nombre de base termine en `_test`. No se ejecutan en paralelo sobre la misma base.

Con Python 3.14 y dependencias de desarrollo:

```sh
python -m pip install -e ".[dev]"
ruff check app tests migrations scripts
python -m pytest -q
```

Sin `NATIONAL_TEST_DATABASE_URL`, las pruebas HTTP usan SQLite y las pruebas exclusivas de PostgreSQL se omiten. El [script de demostración HTTP](scripts/smoke_test.py) también prueba el servicio levantado, dejando registros de demo con adjudicaciones cerradas; instrucciones en [docs/testing.md](docs/testing.md).

## Despliegue pendiente

La imagen de ejecución es el target `base` del Dockerfile. Acepta `PORT` (8000 por defecto), escucha en `0.0.0.0` y requiere una base PostgreSQL persistente accesible mediante `DATABASE_URL` con dialecto `postgresql+psycopg://`. Configurá también `JWT_SECRET` y las credenciales de bootstrap como variables privadas del proveedor. La URL externa debe usar HTTPS; `/ready` sirve como comprobación de salud.

El contenedor aplica migraciones y seed al iniciar: para este despliegue inicial usá una instancia. Antes de escalar a varias, separá las migraciones/seed en un trabajo único de despliegue. Falta elegir/configurar el proveedor, desplegar, verificar el servicio desde fuera y publicar sus URLs reales. Tener Docker funcionando localmente no completa ese requisito de la entrega 3.
