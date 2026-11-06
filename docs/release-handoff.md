# Distribución y verificación

El [repositorio público](https://github.com/jgangini/aidp-ai_market-intelligence) distribuye candidatas inmutables para Deploy Studio. La versión del paquete Python es `0.1.0` y la del paquete AIDP es `2.0.1`. La nueva [candidata](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.5) utiliza `v0.1.0-rc.5`; su manifiesto de release identifica el commit exacto y la CI de ese SHA. La [release anterior rc.4](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.4) conserva el commit `95e5d08513f6c6f26f96e65d87ca6912e02166fd`.

Resource Manager 1.5.x necesita la corrección de compatibilidad de `v0.1.0-rc.5`. rc.4 conserva su requisito `>= 1.7.0` y no se puede inicializar con Terraform 1.5.7. La [comprobación del runtime 1.5.7](evidence/terraform-1.5.7.md) detalla el alcance verificado.

La misma candidata `v0.1.0-rc.5` incorpora el [formulario declarativo schema 2](deployment.md#formulario-declarativo-de-deploy-studio). Requiere el motor CTN que valida y renderiza `form.groups` directamente; las releases anteriores siguen inmutables con schema 1 y ese motor las rechaza. El manifiesto y `SHA256SUMS` de la nueva release identifican su commit y sus cinco assets. La publicación de esta candidata no acredita aceptación OCI ni revisión Browser.

## Archivos publicados

| Archivo | Contenido |
| --- | --- |
| `aidp-ai-market-intelligence-source.zip` | Fuente Git con raíz única `source/`, manifiesto Terraform y hooks. |
| `aidp_ai_market_intelligence-0.1.0-py3-none-any.whl` | Distribución instalable con fuentes canónicas AIDP. |
| `aidp_ai_market_intelligence-0.1.0.tar.gz` | Distribución Python de fuente. |
| `aidp-ai-market-intelligence-release.json` | Versiones, commit, assets, hashes, contrato y alcance de validación. |
| `SHA256SUMS` | Hashes de los tres payloads y del manifiesto; no contiene un hash de sí mismo. |

Los tags y assets inmutables no se reemplazan. Una corrección de código necesita otro commit/tag; una actualización de documentación en main no cambia el binario publicado.

## Consumir desde Deploy Studio

Selecciona el proyecto `aidp-ai-market-intelligence` y la release. El consumidor enumera tags, resuelve su commit y fija `source_ref` y `source_commit_sha`. Para esta candidata deben coincidir con los valores anteriores. El backend descarga la fuente pública y empaqueta los hooks de esa referencia; no se entrega un PAT al agente. La autenticación GitHub opcional puede utilizarse para los límites de la API.

El consumidor admite hasta 250 archivos seleccionados, 10 MiB extraídos y 100 MiB de archivo GitHub. El manifiesto incluye `src`, `aidp`, `package`, `pyproject.toml` y `uv.lock` para post-apply. La imagen de la ficha es el asset local de CTN `/assets/oci-aidp-cloud-migration-lab.png`.

## Verificar la descarga

En una carpeta nueva, descarga los cinco assets de la misma release. Comprueba los hashes de `SHA256SUMS` y los tamaños/hashes del manifiesto. Verifica que tag, commit, project_id y versiones correspondan al paquete que instalarás. No mezclar un ZIP de fuente con una wheel o manifiesto de otra release.

```powershell
gh release download v0.1.0-rc.5 --repo jgangini/aidp-ai_market-intelligence --dir .local/release-download
```

Los assets de rc.1–rc.4 conservan sus condiciones originales de publicación, incluida la marca histórica `publication_requirements.private=true`; el repositorio y sus descargas son actualmente públicos. rc.5 declara esa visibilidad pública. Eso no cambia la protección de credenciales, state o recibos operativos.

## Alcance de la candidata

La fuente implementa instalación TEST pausada, update/redeploy/rollback y enlace protegido de memoria. Falta aceptar nativamente permisos, el entorno del runner/compute, `vectorDbId` y continuidad/aislamiento de checkpoints. La memoria READY y el probe pausado no prueban inferencia ni calidad de respuestas. El gate Sentrux contra la baseline inicial permanece fallido; no representa una aprobación arquitectónica.

Consulta [despliegue](deployment.md) para instalar y [operación](operations.md) para Resume/recuperación. El manifiesto publicado de rc.5 contiene sus checks, hashes y CI; los registros anteriores se conservan como [evidencia de rc.4](evidence/release-rc4-validation.md). La [auditoría de exposición pública](evidence/publication-audit.md) documenta la revisión de fuente, historial y assets.
