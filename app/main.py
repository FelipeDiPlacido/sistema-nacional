from fastapi import FastAPI
from sqlalchemy import text

from app.api.v1.national import router
from app.core.database import DbSession

app = FastAPI(
    title="Sistema Nacional de Gestión de Recursos y Riesgos",
    version="0.1.0",
    description=(
        "API nacional con JWT para registrar y habilitar ONG, verificar certificaciones y "
        "riesgos, gestionar inventario propio y adjudicar/liberar recursos transaccionalmente."
    ),
)


@app.get("/health", tags=["Salud"], summary="Consultar el estado de la API")
def health():
    return {"status": "ok"}


@app.get("/ready", tags=["Salud"], summary="Verificar la conexión con PostgreSQL")
def ready(db: DbSession):
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


app.include_router(router)
