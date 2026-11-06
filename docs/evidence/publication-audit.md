# Auditoría de exposición pública

Repositorio: [jgangini/aidp-ai_market-intelligence](https://github.com/jgangini/aidp-ai_market-intelligence). La autorización del usuario comprende hacer públicos los tres proyectos. La API GitHub confirmó `visibility=public`, `private=false` después de la revisión.

## Alcance revisado

- 155 archivos rastreados del árbol previo al cambio documental.
- 14 commits alcanzables y 210 blobs históricos, con inventario de rutas antes de leer blobs.
- 4 releases y 20 assets descargados; 1221 archivos internos revisados sin extraerlos al checkout.
- Digests/tamaños de GitHub y SHA256SUMS comprobados en cada release.

Se usaron Git, escaneo Python de patrones y asignaciones literales AST y revisión de contexto. No había un escáner externo de secretos instalado; no se instaló otro. Se buscaron claves privadas, tokens GitHub/Apify/AWS/Google/Slack, bearer JWT, URLs con credenciales y rutas de credenciales/state. Los archivos ignorados y .oci no se leyeron.

## Resultado y preservación

No se detectaron secretos confirmados. Las 52 coincidencias de URLs con credenciales son fixtures sintéticas en ocho pruebas que rechazan ese formato; se contrastaron también sus copias históricas y de releases. Este resultado describe los controles realizados y no una garantía de ausencia de cualquier clase de dato sensible.

Las releases rc.1–rc.4 conservan tags, IDs, assets y digests. Sus notas/manifiestos reflejan la publicación privada original y no se reescriben para cambiar la visibilidad. Los resultados detallados de la auditoría y las descargas quedan en .local/publication-audit, fuera de Git. La documentación pública de uso está en README, despliegue, operación y distribución; los registros de tareas/extracción quedan separados.

No se ejecutó OCI, apply, Browser ni Docker. El cambio de visibilidad no autoriza despliegues.
