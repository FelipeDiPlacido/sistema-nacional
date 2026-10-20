# Sistema Nacional de Gestión de Recursos y Riesgos

Servicio independiente de RescueSync para el onboarding de ONG, el catálogo de certificaciones y los perfiles de competencia. Esta primera etapa establece autenticación JWT y datos estructurados de habilitación; todavía no adjudica ni bloquea recursos.

## Ejecución local con Docker

Requiere Docker Desktop o Docker Engine con Compose.

```powershell
Copy-Item .env.example .env
```

Reemplazá `JWT_SECRET` por un secreto aleatorio de al menos 32 caracteres y `POSTGRES_PASSWORD` por una contraseña local. `DATABASE_URL` del archivo de ejemplo corresponde al uso desde la computadora; Compose configura la URL interna del contenedor por separado.

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

Las declaraciones de certificación y la ONG nueva comienzan en `PENDING_REVIEW`. La respuesta comunica el nivel y las capacidades como datos, sin tratar la revisión como un rechazo de la oferta. En esta etapa no se incluye una consola de revisión ni se habilitan capacidades automáticamente. Los niveles de riesgo permitidos quedan vacíos hasta que la certificación y la ONG tengan una habilitación verificada; el perfil persiste los niveles autorizados sin inventar políticas de riesgo.

## Pruebas

Con Python 3.14:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

Las pruebas HTTP usan SQLite aislado; las migraciones y el despliegue están preparados para PostgreSQL 17.

## Siguientes etapas

1. Agregar inventario y consulta de disponibilidad nacional.
2. Implementar compromiso con locking transaccional PostgreSQL y liberación/cierre.
3. Integrar la evaluación posterior al cierre de convocatoria y la asignación de lotes en RescueSync.
4. Desplegar en cloud y publicar las URLs reales de repositorios y servicios.
