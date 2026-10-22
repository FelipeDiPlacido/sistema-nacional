# Límites de solicitudes

La API limita la frecuencia de solicitudes antes de validar el JSON, comprobar contraseñas o consultar PostgreSQL. Los límites se aplican también a solicitudes inválidas y a usuarios autenticados. Cambiar de ruta o renovar el JWT no reinicia la cuota general.

## Valores iniciales

| Operación | Por dirección de cliente | Total del proceso | Ventana |
| --- | --- | --- | --- |
| General, incluidos Swagger y OpenAPI | 120 | 600 | 1 minuto |
| `POST /api/v1/auth/login` | 10 | 30 | 1 minuto |
| `POST /api/v1/ngos` | 10 | 30 | 1 hora |

Login y registro consumen tanto su cuota específica como la general. Los intentos rechazados también cuentan. Las ventanas son fijas desde el primer intento y no se prolongan con cada rechazo. Agregar una barra final a las rutas no evita el límite.

Al exceder una cuota, la respuesta es `429` con un mensaje en español, `Cache-Control: no-store` y `Retry-After`, que indica cuántos segundos esperar. Los clientes deben respetarlo en lugar de reintentar en un bucle.

Las consultas `GET` y `HEAD` a `/health` y `/ready` quedan fuera de las cuotas para permitir los controles del proveedor. Esto no constituye una garantía de disponibilidad ante ataques.

## Configuración

Las variables son opcionales: el código activa los valores anteriores sin configurar nada adicional.

| Variable | Valor inicial |
| --- | --- |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `120` |
| `RATE_LIMIT_GLOBAL_REQUESTS_PER_MINUTE` | `600` |
| `RATE_LIMIT_LOGIN_PER_MINUTE` | `10` |
| `RATE_LIMIT_GLOBAL_LOGIN_PER_MINUTE` | `30` |
| `RATE_LIMIT_REGISTRATION_PER_HOUR` | `10` |
| `RATE_LIMIT_GLOBAL_REGISTRATION_PER_HOUR` | `30` |

Todos los valores deben ser enteros positivos. En Render se configuran en **Environment** del servicio web; luego hay que desplegar para aplicarlos. Para cambiar los valores en Compose local, agregá las variables al bloque `environment` del servicio `api`: su archivo `.env` no se pasa íntegramente al contenedor.

## Clientes, proxies y capacidad

Se usa la dirección de cliente que entrega el servidor ASGI. El middleware no interpreta directamente `X-Forwarded-For`, `Forwarded` ni `X-Real-IP`. Uvicorn puede ajustar esa dirección a partir de proxies configurados como confiables: no se debe permitir cualquier origen mediante `FORWARDED_ALLOW_IPS=*` en una instalación accesible directamente. Ver [configuración oficial de proxies de Uvicorn](https://uvicorn.dev/deployment/#proxies-and-forwarded-headers).

Detrás de un proxy, varias personas pueden compartir la misma cuota de cliente. El límite total no depende de la dirección y sigue vigente si las direcciones cambian. Si el grupo alcanza una cuota durante una demostración, esperá `Retry-After` o ajustá los valores para el uso previsto; no desactives los controles para resolverlo.

Los contadores usan `limits` con almacenamiento en memoria y ventanas fijas. El Dockerfile inicia **un worker** explícitamente. Conservá **una instancia** del servicio: los contadores no se comparten entre procesos ni réplicas y se reinician al reiniciar/desplegar. Antes de escalar, hace falta almacenamiento compartido para las cuotas, además de separar las migraciones del arranque. Esta configuración no agrega Redis ni otro servicio pago.

Esta protección reduce abuso por frecuencia. No sustituye JWT, permisos, límites de capacidad del proveedor ni protección contra DDoS. No evita por completo el registro automatizado ni garantiza resistencia a ataques distribuidos.

## Verificación

`tests/test_rate_limits.py` cubre límites por cliente y globales, intentos válidos e inválidos, barras finales, cabeceras falsas, expiración, concurrencia, controles de salud y documentación OpenAPI. La suite completa también verifica que registro, certificaciones, inventario y adjudicaciones sigan funcionando con la protección activa.

```sh
docker compose -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
docker compose -f compose.test.yaml down
```

Usá la base efímera de pruebas para comprobar los límites. No ejecutes pruebas de carga ni intentes agotar todas las cuotas del ambiente público: bloquearías temporalmente las pruebas normales del equipo.
