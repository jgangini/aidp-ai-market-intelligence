# Release privada: handoff para Deploy Studio

Publicada el 2026-11-05 a las 21:25 (America/Lima): [v0.1.0-rc.2](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.2).

| Dato | Valor verificado |
| --- | --- |
| Repositorio | `jgangini/aidp-ai_market-intelligence`, PRIVATE |
| Tag | `v0.1.0-rc.2` |
| Commit exacto | `d1a06f3e5df65ed30e33855cbbc1fa018bab0ca8` |
| Release ID | `436029648` |
| Estado GitHub | `draft=false`, `prerelease=true`, `immutable=true` |
| Distribución Python | `0.1.0` |
| Paquete AIDP | `ai_market_intelligence`, `2.0.0` |
| Superficie nativa | TEST, investigación pausada |

Es una candidata para la instalación nativa autorizada, no aprobación PROD ni de calidad de respuestas. La inmutabilidad está activada en el repositorio y fue confirmada en la release publicada. Tag y assets quedan protegidos; las correcciones necesitan nuevo commit/tag. No se cambió la visibilidad.

## Cinco assets del contrato

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `aidp-ai-market-intelligence-release.json` | 3286 | `aa7d842e61e79f4e237a53fd64f140ce695d7dbd884985d0eb824a0b98410f37` |
| `aidp-ai-market-intelligence-source.zip` | 475600 | `aaf2b31a0c5b35a26950a4ed8ff964d75f8f25d7beb3e8cf6766bee2e2d7b395` |
| `aidp_ai_market_intelligence-0.1.0-py3-none-any.whl` | 182154 | `8db473897c5accc4f05681ea6c09f2b77cbdb6883b70387e71da5b73bcd0a3b1` |
| `aidp_ai_market_intelligence-0.1.0.tar.gz` | 171028 | `dbe4ef1222b122b3fda61c9e8604dfc126cf6e127a67b0b580527cd6c5c6b7fd` |
| `SHA256SUMS` | 436 | `ffc5d386eb95e468d797d6ca5aa8f570c0831b494fbc11475d84b7d0f60347a1` |

El manifiesto de release usa schema 1 e identifica tag/commit, versiones, contrato Deploy Studio, checks y los tres payloads. `SHA256SUMS` contiene los hashes de los tres payloads y del manifiesto; no contiene un hash de sí mismo. Se verificó conjunto exacto de cinco assets, tamaños y digests reportados por GitHub, además de descarga posterior con igualdad byte por byte y checksums.

El ZIP se construyó con `git archive` del commit exacto y raíz única `source/`. Wheel y sdist se construyeron desde ese árbol limpio. Se comprobaron RECORD, los 40 assets canónicos contra los blobs Git, import del módulo de mantenimiento y CLI fuera del checkout; no se incorporaron `.oci`, estado, tokens, caches o archivos ignorados. El escaneo de patrones de credenciales revisó 304 payloads entre los tres archivos.

## Presentación del producto

El título visible exacto es **AIDP AI Market Intelligence**. La descripción presenta el agente de investigación, AI Compute dedicado, credenciales protegidas, conectores web/sociales, respuestas con evidencias y hooks de instalación, actualización y recuperación. Los límites de aceptación permanecen en la documentación operativa.

`presentation.image=/assets/oci-aidp-cloud-migration-lab.png` reutiliza la imagen aprobada que sirve CloudTechNext desde `app/frontend/public/assets/`; `image_alt` identifica AIDP AI Market Intelligence. El consumidor usa esta ruta local directamente. No requiere descargar una imagen desde el repositorio privado ni enviar tokens al navegador. Se comprobaron el parser real y el PNG existente (972 × 1618); la revisión renderizada y la actualización del catálogo pertenecen al coordinador, no se ejecutaron aquí.

La release anterior [v0.1.0-rc.1](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.1), commit `cce5425551201a5f5bcdd70895b950ced7d4b02c`, release ID `435990780`, permanece inmutable y sin modificaciones. rc.2 publica esta corrección mediante nuevo commit, tag y assets.

## Consumo privado

Deploy Studio debe usar el repositorio anterior, `source_ref=v0.1.0-rc.2` y `source_commit_sha=d1a06f3e5df65ed30e33855cbbc1fa018bab0ca8`, proyecto `aidp-ai-market-intelligence`. El consumidor enumera tags y resuelve su commit; el marcado prerelease no lo excluye de ese contrato. Puede existir cache de opciones de hasta 300 segundos.

El backend de Deploy Studio necesita su acceso GitHub autorizado al repositorio privado. Descarga el código y empaqueta los hooks desde la fuente fijada; la VM de un portal no existe en este producto. Los hooks no descargan assets GitHub, no reciben un PAT y no colocan autenticación GitHub en Terraform state/user-data. La integración de acceso privado desde el backend real se confirma en la prueba del coordinador; la descarga con `gh` de esta entrega no la sustituye.

El parser/extractor real aceptó el ZIP de release: **72 archivos y 859960 bytes**, dentro de los límites actuales de **250 archivos / 10 MiB descomprimidos** y **100 MiB de archivo GitHub**. El coordinador verificó antes ambos imports en Python 3.12 sobre solo ese árbol, sin red ni credenciales. Esto no acredita permisos ni ejecución OCI.

## Parámetros: Governance → Market sobre el mismo AIDP

El coordinador ejecuta Governance primero con AIDP nuevo. Market se agrega después mediante referencias existentes; no ejecutar apply ni crear plataforma desde este chat. Los nombres de outputs se contrastaron en código de Governance, sin leer estado/credenciales ni valores privados.

| Campo de Market / solicitud | Valor para esta prueba |
| --- | --- |
| `compartment_mode` | `existing` |
| `compartment` | Output Governance `compartment_ocid`; preferir OCID exacto |
| `region` | Output Governance `region` |
| `aidp_mode` | `existing` |
| `existing_ai_data_platform_ocid` | Output Governance `ai_data_platform_ocid` |
| `existing_workspace_key` | `workspace_key` nativo confirmado por el post-apply de Governance |
| `identity_domain_id` | Output Governance `identity_domain_id` |
| `admin_user_ocid` | Output Governance `admin_user_ocid`, activo y con rol AIDP efectivo |
| `agent_model_id` | Output Governance `model_id`, verificado en esa región |
| `artifacts_bucket_mode` | `existing` |
| `artifacts_bucket_name` | Output Governance `artifacts_bucket_name` |
| Namespace | Output Governance `objectstorage_namespace`, revalidado automáticamente por preflight |
| `lifecycle_action` | `install` para primera instalación / Resume |
| `operation_request_id` | Vacío en el primer intento; conservar la operación al reanudar |
| `test_session_id` | Vacío al primer intento; el instalador registra/verifica su sesión TEST |
| `apify_token` | Secreto del formulario, únicamente `preflight.secret_inputs` y `post_apply.secret_inputs` |

En Governance nuevo, el output Terraform `workspace_key` puede estar vacío antes del post-apply: no usarlo hasta que la clave nativa quede resuelta. No sustituirla por `workspace_name`, una ruta o una coincidencia de nombre. Los OCIDs y valores concretos todavía no fueron proporcionados por la prueba nativa; son bindings de outputs verificados, no datos inventados.

El operador autorizado sigue firmando; seleccionar el administrador no lo suplanta. Market revalida dominio, actividad y roles efectivos y reutiliza `AidpRuntime` y credenciales verificadas sin otra identidad provisioner. Los recursos AIDP/bucket existentes permanecen fuera de su state Terraform. El instalador mantiene raíces propias `/Workspace/.artifact/ai_market_intelligence` y `/Workspace/.control/modules/ai_market_intelligence`; no crea tablas ni workflows Spark.

## Gates y alcance pendientes

- CI del commit [aprobada](https://github.com/jgangini/aidp-ai_market-intelligence/actions/runs/42149771102): 1325 pruebas Python, ocho de protocolo y cinco Terraform con mocks. La CI automática del commit exacto fue aprobada; las comprobaciones locales de publicación se enfocaron en el contrato y los archivos.
- **Sentrux gate fallido**, sin reglas locales; baseline vacía conservada. Calidad 0.8125 → 0.6619, coupling 0 → 0.74, complejas 0 → 11; cero ciclos y god files. Esta release no se declara arquitectónicamente aprobada.
- Aceptación OCI, versiones/dependencias del compute y ejecución real del runner pendientes. El coordinador posee Browser, Docker, colas y mutaciones OCI, en orden Governance → Market → GodEye.
- Preflight Apify solo lee cuenta/actores/builds fijados; ejecución y entitlement siguen `not_verified`. No iniciar actores pagados ni campañas de respuestas como parte de la instalación. El probe exige revisión exacta y cero llamadas modelo/capturas; TEST queda pausado.
- Locks con el mismo bucket excluyen procesos; configuraciones con buckets distintos sobre un workspace requieren serialización global. No hay transferencia de state ni adopción automática.
- PROD/delete/transferencia de state siguen sin implementar. Ver [operaciones](operations.md) para update/redeploy/rollback probados con mocks, respaldo y recuperación.
