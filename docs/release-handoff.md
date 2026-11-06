# Distribución y verificación

El [repositorio público](https://github.com/jgangini/aidp-ai_market-intelligence) distribuye candidatas inmutables para Deploy Studio. La versión del paquete Python es `0.1.0` y la del paquete AIDP es `2.0.1`. La [candidata vigente](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.6) utiliza `v0.1.0-rc.6`; su manifiesto de release identifica el commit exacto y la CI de ese SHA. La [release anterior rc.5](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.5) conserva el commit `ce5ae002995b9406e509d427603b8716b5cca63f`.

rc.6 está publicada como prerelease pública e inmutable: release ID `436769686`, commit `93102dfb76da2d0025143a36ecdcf274f36e6e63`, `draft=false`, `prerelease=true`, `immutable=true`. Los cinco assets descargados coinciden byte por byte con los preparados y con los digests públicos; el [registro de rc.6](evidence/release-rc6-validation.md) conserva hashes, CI y límites de aceptación. La publicación no implica merge del PR a main ni aceptación OCI. La revisión Browser de rc.6 sigue pendiente del coordinador tras actualizar la caché que aún mostraba rc.5.

Resource Manager 1.5.x necesita la corrección introducida desde `v0.1.0-rc.5`. rc.4 conserva su requisito `>= 1.7.0` y no se puede inicializar con Terraform 1.5.7. La [comprobación del runtime 1.5.7](evidence/terraform-1.5.7.md) detalla el alcance verificado.

rc.5 introdujo el [formulario declarativo schema 2](deployment.md#formulario-declarativo-de-deploy-studio), que rc.6 conserva. Requiere el motor CTN que valida y renderiza `form.groups` directamente; rc.1–rc.4 siguen inmutables con schema 1 y ese motor las rechaza. El manifiesto y `SHA256SUMS` de cada release identifican su commit y sus cinco assets.

## Archivos publicados

rc.6 alinea el formulario con Governance: mismo orden/campos/etiquetas/defaults, con nombre propio `aidp_market_intelligence` y token Apify como único campo adicional. El nombre crea el `display_name` real de AIDP; sesión TEST y ECPU permanecen como parámetros avanzados fuera del formulario. rc.5 conserva sus tags/assets inmutables.

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
gh release download v0.1.0-rc.6 --repo jgangini/aidp-ai_market-intelligence --dir .local/release-download
```

Los assets de rc.1–rc.4 conservan sus condiciones originales de publicación, incluida la marca histórica `publication_requirements.private=true`; el repositorio y sus descargas son actualmente públicos. Desde rc.5 se declara esa visibilidad pública. Eso no cambia la protección de credenciales, state o recibos operativos.

## Alcance de la candidata

La fuente implementa instalación TEST pausada, update/redeploy/rollback y enlace protegido de memoria. Falta aceptar nativamente permisos, el entorno del runner/compute, `vectorDbId` y continuidad/aislamiento de checkpoints. La memoria READY y el probe pausado no prueban inferencia ni calidad de respuestas. El gate Sentrux contra la baseline inicial permanece fallido; no representa una aprobación arquitectónica.

Consulta [despliegue](deployment.md) para instalar y [operación](operations.md) para Resume/recuperación. El manifiesto publicado de rc.6 contiene sus checks, hashes y CI; los registros anteriores se conservan como [evidencia de rc.4](evidence/release-rc4-validation.md). La [auditoría de exposición pública](evidence/publication-audit.md) documenta la revisión de fuente, historial y assets.
