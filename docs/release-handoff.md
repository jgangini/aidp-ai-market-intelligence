# Distribución y verificación

El [repositorio público](https://github.com/jgangini/aidp-ai-market-intelligence) distribuye candidatas inmutables para Deploy Studio. El paquete Python utiliza `0.1.0` y el paquete AIDP `2.0.2`. Cada release identifica su tag, commit exacto, CI y alcance de validación en su manifiesto. Consulta las [releases publicadas](https://github.com/jgangini/aidp-ai-market-intelligence/releases) para seleccionar una candidata.

## Archivos publicados

| Archivo | Contenido |
| --- | --- |
| `aidp-ai-market-intelligence-source.zip` | Fuente Git con raíz única `source/`, manifiesto, Terraform y hooks. |
| `aidp_ai_market_intelligence-0.1.0-py3-none-any.whl` | Distribución instalable con fuentes canónicas AIDP. |
| `aidp_ai_market_intelligence-0.1.0.tar.gz` | Distribución Python de fuente. |
| `aidp-ai-market-intelligence-release.json` | Versiones, commit, assets, hashes, contrato y validación. |
| `SHA256SUMS` | Hashes de los tres payloads y del manifiesto; no contiene un hash de sí mismo. |

Los tags y assets inmutables no se reemplazan. Una corrección necesita otro commit y tag. Cambiar documentación en main no modifica un paquete publicado.

## Consumir desde Deploy Studio

Selecciona el proyecto `aidp-ai-market-intelligence` y una release. Deploy Studio resuelve su commit y fija `source_ref` y `source_commit_sha` para validar e instalar la misma fuente. El formulario requiere el motor declarativo `schema_version: 2`; un consumidor incompatible debe rechazarlo, sin formulario alternativo.

El formulario configura identidad, AIDP nuevo o existente, workspace, modelo, artefactos y memoria Autonomous AI Lakehouse 26ai DW. Apify añade un token protegido y comprobaciones de lectura de cuenta y actores/builds configurados; preflight no ejecuta actores. La infraestructura admite Terraform 1.5.7 de Resource Manager.

El consumidor descarga la fuente pública y empaqueta los hooks de esa referencia; no entrega un PAT al agente. El manifiesto incluye `src`, `aidp`, `package`, `pyproject.toml` y `uv.lock` para post-apply. Los límites del consumidor son 250 archivos seleccionados, 10 MiB extraídos y 100 MiB de archivo GitHub.

## Verificar la descarga

Descarga los cinco assets de una misma release en una carpeta nueva. Comprueba `SHA256SUMS`, tamaños y hashes del manifiesto. Verifica tag, commit, `project_id` y versiones; no mezcles archivos de distintas candidatas. El ZIP debe contener el mismo `deploy-studio.json` que el commit fijado.

La publicación exige CI del commit exacto, comprobación del paquete y descarga/verificación de los assets publicados. Credenciales, Terraform state y recibos operativos permanecen fuera de Git y de los assets.

## Alcance de validación

La instalación deja un candidato TEST y AI Compute preparados, con investigación pausada y memoria asociada. **Application URL** navega a Agent Hub de la plataforma verificada, en `/static/ah/`, con tenant y dominio codificados. El enlace no publica ni registra automáticamente el candidato TEST en Agent Hub.

Las pruebas locales verifican contratos, errores, empaquetado y Terraform con mocks. La aceptación nativa comprueba por separado PLAN/APPLY, recursos, memoria READY, AI Compute y despliegue TEST ACTIVE. Estas comprobaciones no acreditan inferencia, continuidad de checkpoints, calidad de respuestas ni ejecución de actores pagados. Cada release debe declarar cuáles se realizaron sobre su commit exacto.

Consulta [despliegue](deployment.md) para instalar y [operación](operations.md) para Resume y recuperación. Los registros concretos en `docs/evidence/` describen esa ejecución y no sustituyen el funcionamiento actual del producto.

