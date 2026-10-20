# Sistema Nacional de Gestión de Recursos y Riesgos

Servicio independiente de RescueSync para el onboarding de ONG, el catálogo de certificaciones y los perfiles de competencia. Esta primera etapa establece autenticación JWT y datos estructurados de habilitación; todavía no adjudica ni bloquea recursos.

## Ejecución local con Docker

Requiere Docker Desktop o Docker Engine con Compose.

```powershell
Copy-Item .env.example .env
```

Reemplazá `JWT_SECRET` y `RESOURCE_ADMIN_TOKEN` por secretos aleatorios diferentes de al menos 32 caracteres, y `POSTGRES_PASSWORD` por una contraseña local. `DATABASE_URL` del archivo de ejemplo corresponde al uso desde la computadora; Compose configura la URL interna del contenedor por separado. El token de administración solo permite registrar inventario; no es el JWT de una ONG.

```powershell
docker compose up --build -d --wait
```

- Swagger UI: <http://localhost:8010/docs>
- OpenAPI JSON: <http://localhost:8010/openapi.json>
- Salud: <http://localhost:8010/health>
- Preparación de PostgreSQL: <http://localhost:8010/ready>

La imagen ejecuta las migraciones y carga el catálogo inicial de certificaciones antes de iniciar FastAPI. PostgreSQL persiste en un volumen de Compose.

## Endpoints iniciales

| Método | Ruta | Función |
| --- | --- | --- |
| POST | `/api/v1/ngos` | Registrar una ONG y declarar certificaciones para revisión |
| POST | `/api/v1/auth/login` | Obtener un JWT con correo y contraseña |
| GET | `/api/v1/auth/me` | Consultar la cuenta autenticada |
| GET | `/api/v1/certifications` | Consultar el catálogo activo |
| GET | `/api/v1/ngos/enabled` | Consultar ONG habilitadas |
| GET | `/api/v1/ngos/me/competencies` | Consultar el perfil estructurado propio |
| POST | `/api/v1/resources` | Registrar disponibilidad inicial con `X-National-Admin-Token` |
| GET | `/api/v1/resources` | Consultar disponibilidad global |
| GET | `/api/v1/resources/{resource_code}` | Consultar disponibilidad de un recurso |
| POST | `/api/v1/resources/validate` | Evaluar disponibilidad, competencia y riesgo para la ONG autenticada |

Las declaraciones de certificación y la ONG nueva comienzan en `PENDING_REVIEW`. La respuesta comunica el nivel y las capacidades como datos, sin tratar la revisión como un rechazo de la oferta. En esta etapa no se incluye una consola de revisión ni se habilitan capacidades automáticamente. Los niveles de riesgo permitidos quedan vacíos hasta que la certificación y la ONG tengan una habilitación verificada; el perfil persiste los niveles autorizados sin inventar políticas de riesgo.

## Cargar y evaluar disponibilidad

El alta inicial del inventario requiere el encabezado `X-National-Admin-Token`, configurado con `RESOURCE_ADMIN_TOKEN`. Por ejemplo, desde Swagger:

```json
{
  "code": "POTABLE_WATER",
  "name": "Agua potable",
  "category": "SUPPLIES",
  "unit": "litros",
  "total_quantity": 100,
  "required_certification_code": "LOGISTICS"
}
```

El código de certificación debe existir y estar activo. El inventario informa `available_quantity = total_quantity - committed_quantity`; en esta etapa todo recurso nuevo empieza con `committed_quantity` en cero. Los endpoints GET muestran recursos activos y su disponibilidad global.

Para evaluar una solicitud, registrá una ONG, iniciá sesión y autorizá el JWT en Swagger. Enviá `POST /api/v1/resources/validate`:

```json
{
  "resource_code": "POTABLE_WATER",
  "quantity": 25,
  "risk_level": "LOW"
}
```

La identidad de la ONG se obtiene del JWT. La respuesta `200` informa la cantidad disponible y `fulfillable_quantity`, el nivel de autorización, el estado de certificación, `availability_status` (`SUFFICIENT`, `PARTIAL` o `NONE`) y las restricciones observadas. Por ejemplo, una ONG pendiente recibe sus capacidades y restricciones en la respuesta; no se responde con un rechazo HTTP por no estar habilitada. La consulta es informativa: no reserva ni descuenta inventario, por lo que no debe usarse como garantía de adjudicación.

## Pruebas

Con Python 3.14:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

Las pruebas HTTP usan SQLite aislado; las migraciones y el despliegue están preparados para PostgreSQL 17. La migración `0002` incorpora el inventario nacional y restricciones de integridad para que las cantidades comprometidas no puedan superar el total.

## Siguientes etapas

1. Implementar compromiso con locking transaccional PostgreSQL y liberación/cierre.
2. Integrar la evaluación posterior al cierre de convocatoria y la asignación de lotes en RescueSync.
3. Desplegar en cloud y publicar las URLs reales de repositorios y servicios.
