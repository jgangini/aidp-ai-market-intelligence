# Release privada: handoff para Deploy Studio

Publicada el 2026-11-05 a las 22:30 (America/Lima): [v0.1.0-rc.4](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.4).

| Dato | Valor verificado |
| --- | --- |
| Repositorio | `jgangini/aidp-ai_market-intelligence`, PRIVATE |
| Tag | `v0.1.0-rc.4` |
| Commit exacto | `95e5d08513f6c6f26f96e65d87ca6912e02166fd` |
| Release ID | `436068258` |
| Estado GitHub | `draft=false`, `prerelease=true`, `immutable=true` |
| Distribución Python | `0.1.0` |
| Paquete AIDP | `ai_market_intelligence`, `2.0.1` |
| Superficie nativa | TEST, investigación pausada |

Es una candidata para la instalación nativa autorizada, no aprobación PROD ni de calidad de respuestas. La inmutabilidad está activada en el repositorio y fue confirmada en la release publicada. Tag y assets quedan protegidos; las correcciones necesitan nuevo commit/tag. No se cambió la visibilidad.

## Cinco assets del contrato

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `aidp-ai-market-intelligence-release.json` | 5664 | `24e11a6ad683bbabc23c6038a9eaa220e159a417a3d3e4ccba205811633eb61c` |
| `aidp-ai-market-intelligence-source.zip` | 494404 | `11a7b757144712d3d545e12de26f72fa88343b1371c1da20ea9984aeb7c3003e` |
| `aidp_ai_market_intelligence-0.1.0-py3-none-any.whl` | 186692 | `a03eb527fa162a061ff9e9da9d919851dcd86c062b0ea41135a58edeaa6b6b49` |
| `aidp_ai_market_intelligence-0.1.0.tar.gz` | 178831 | `acbd97a347eb7f73552ccc05746b478cefadfa50eb200137fa17510b5eafc37d` |
| `SHA256SUMS` | 436 | `66a418c8fd7a7ec508b0376235b4ba900fa019fef82421e9e6d7419b6ee0ba15` |

El manifiesto de release usa schema 1 e identifica tag/commit, versiones, contrato Deploy Studio, checks y los tres payloads. `SHA256SUMS` contiene los hashes de los tres payloads y del manifiesto; no contiene un hash de sí mismo. Se verificó conjunto exacto de cinco assets, tamaños y digests reportados por GitHub, además de descarga posterior con igualdad byte por byte y checksums.

El ZIP se construyó con `git archive` del commit exacto y raíz única `source/`. Wheel y sdist se construyeron desde ese árbol limpio. Se comprobaron RECORD, los 40 assets canónicos contra los blobs Git, import del módulo de mantenimiento y CLI fuera del checkout; no se incorporaron `.oci`, estado, tokens, caches o archivos ignorados. El escaneo de patrones de credenciales revisó 310 payloads entre los tres archivos.

## Presentación del producto

El título visible exacto es **AIDP AI Market Intelligence**. La descripción presenta el agente de investigación, AI Compute dedicado, credenciales protegidas, conectores web/sociales, respuestas con evidencias y hooks de instalación, actualización y recuperación. Los límites de aceptación permanecen en la documentación operativa.

`presentation.image=/assets/oci-aidp-cloud-migration-lab.png` reutiliza la imagen aprobada que sirve CloudTechNext desde `app/frontend/public/assets/`; `image_alt` identifica AIDP AI Market Intelligence. El consumidor usa esta ruta local directamente. No requiere descargar una imagen desde el repositorio privado ni enviar tokens al navegador. Se comprobaron el parser real y el PNG existente (972 × 1618). El coordinador confirmó en rc.2 la resolución del SHA, el título, la descripción y la imagen cargada en Browser; este chat no ejecutó Browser. La integración del selector nuevo y el catálogo pertenecen al coordinador.

La release anterior [v0.1.0-rc.1](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.1), commit `cce5425551201a5f5bcdd70895b950ced7d4b02c`, release ID `435990780`, permanece inmutable y sin modificaciones. También se conserva [v0.1.0-rc.2](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.2), commit `d1a06f3e5df65ed30e33855cbbc1fa018bab0ca8`, release ID `436029648`. También se conserva [rc.3](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.3), SHA `22028dc4a0b619d6ef83717b346646ac8594c92c`. rc.4 incorpora grupos, selector AIDP, modelo congelado y memoria mediante otro commit/tag/assets.

## Consumo privado

Deploy Studio debe usar el repositorio anterior, `source_ref=v0.1.0-rc.4` y `source_commit_sha=95e5d08513f6c6f26f96e65d87ca6912e02166fd`, proyecto `aidp-ai-market-intelligence`. El consumidor enumera tags y resuelve su commit; el marcado prerelease no lo excluye de ese contrato. Puede existir cache de opciones de hasta 300 segundos.

El backend de Deploy Studio necesita su acceso GitHub autorizado al repositorio privado. Descarga el código y empaqueta los hooks desde la fuente fijada; la VM de un portal no existe en este producto. Los hooks no descargan assets GitHub, no reciben un PAT y no colocan autenticación GitHub en Terraform state/user-data. La integración de acceso privado desde el backend real se confirma en la prueba del coordinador; la descarga con `gh` de esta entrega no la sustituye.

El parser/extractor real aceptó el ZIP de release: **74 archivos y 886092 bytes**, dentro de los límites actuales de **250 archivos / 10 MiB descomprimidos** y **100 MiB de archivo GitHub**. El coordinador verificó antes los hooks del candidato previo en Python 3.12; el parser de esta release acepta el árbol actualizado. El nuevo helper en el runner real sigue pendiente. Esto no acredita permisos ni ejecución OCI.

## Selector de Identity Domain

`identity_domain_id` usa `type=select`, `options_source=oci_identity_domains`, `required=true` y conserva `^ocid1\.domain\.[^\s]+$`. No tiene default ni opciones fijas. El parser real actualizado de CTN aceptó la fuente de rc.3. La consulta genérica del consumidor debe listar dominios ACTIVE desde la home region descubierta, valor OCID y etiqueta nombre más compartimento. La consulta real y su prueba corresponden al coordinador; no se pide introducir un OCID manualmente ni se consultó OCI aquí. El dominio elegido sigue sujeto a validación de hooks y a la vinculación con Governance en esta prueba compartida.

## Modelo, memoria y formulario

Schema 1 usa grupos `aidp_platform`, `identity`, `artifacts`, `enterprise_ai`, `database`; `existing_ai_data_platform_ocid` es select `oci_aidp_platforms`, conservando patrón OCID. CTN actualizado debe aceptar `database_profile=new_or_existing` y `existing_database_wallet=none`.

Autonomous crea/reutiliza 26ai DW. New usa 4 ECPU por defecto (2–512 enteros), 1 TB, mTLS, sin autoscaling y prevent_destroy; existing no adopta recursos. ADMIN llega como secreto a los hooks. Solo la creación usa una variable Terraform sensible: proteger el state privado. No se usa vector_db_admin_cred de Terraform AIDP. No se pide wallet ni SQL directo.

El hook revalida al administrador y exige READY con vectorDbId exacto desde el GET firmado sin deserialización; campo ausente/diferente bloquea. ETag e intención CAS protegen habilitación/Resume. Rechazos 4xx confirmados permiten corregir ADMIN; timeout/409 incierto no duplican POST. No se cambia una asociación READY, elimina memoria ni altera retención. Ver recuperación en [operaciones](operations.md).

Modelo y región/compartimento quedan en runtime_config del recibo y en la fuente pausada. configured_agent_model_id informa el valor guardado; Resume conserva snapshot, update congela target y rollback restaura anterior. Paquete 2.0.1: hash `83b6f4475272b7350de676344f553ff42b925080303dac9979949f01ed2cbcd1`. Ensamblado bare paused de package-check: SHA `e59cd79c014798eabe63b0e4b65fcd7aa91f893b07e0fd9b253aac79546dc86a`; la instalación incorpora su modelo y tiene hash propio. No se inventan perfiles nativos ni autorización de gasto; investigación sigue pausada.

[Governance canónico](https://github.com/jgangini/aidp-ai-data-gobernance) es público por instrucción separada; Market permanece privado. No se ejecutó OCI, Browser ni Docker aquí. La revisión Browser de esta release queda con CTN y los despliegues siguen detenidos.

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
| `autonomous_database_mode` | `existing` después de Governance en la misma plataforma. |
| `existing_autonomous_database_ocid` | OCID exacto de la base ya asociada a Governance, verificado nativamente; no se inventó ni consultó aquí. |
| `autonomous_database_admin_password` | Vacío si la asociación está READY y coincide; protegido si hay que habilitar AI. |
| `lifecycle_action` | `install` para primera instalación / Resume |
| `operation_request_id` | Vacío en el primer intento; conservar la operación al reanudar |
| `test_session_id` | Vacío al primer intento; el instalador registra/verifica su sesión TEST |
| `apify_token` | Secreto del formulario, únicamente `preflight.secret_inputs` y `post_apply.secret_inputs` |

En Governance nuevo, el output Terraform `workspace_key` puede estar vacío antes del post-apply: no usarlo hasta que la clave nativa quede resuelta. No sustituirla por `workspace_name`, una ruta o una coincidencia de nombre. Los OCIDs y valores concretos todavía no fueron proporcionados por la prueba nativa; son bindings de outputs verificados, no datos inventados.

El operador autorizado sigue firmando; seleccionar el administrador no lo suplanta. Market revalida dominio, actividad y roles efectivos y reutiliza `AidpRuntime` y credenciales verificadas sin otra identidad provisioner. Los recursos AIDP/bucket existentes permanecen fuera de su state Terraform. El instalador mantiene raíces propias `/Workspace/.artifact/ai_market_intelligence` y `/Workspace/.control/modules/ai_market_intelligence`; no crea tablas ni workflows Spark.

## Gates y alcance pendientes

- CI del commit [aprobada](https://github.com/jgangini/aidp-ai_market-intelligence/actions/runs/42156728701): 1377 pruebas Python, ocho de protocolo y once Terraform con mocks. La CI automática del commit exacto y esas suites del postflight local fueron aprobadas. El postflight canónico terminó con error en Sentrux, según el siguiente punto; log local `.local/postflight-model-memory-cohesion.log`. El preflight se detuvo para preservar la baseline existente. Las regresiones cubren revocación antes de mutar, CAS/rechazos/timeout, configuración congelada y rollback.
- **Sentrux gate fallido**, sin reglas locales; baseline vacía conservada. Calidad 0.8125 → 0.6620, coupling 0 → 0.73, complejas 0 → 11; cero ciclos y god files. La regresión nueva se corrigió por cohesión. Frente a rc.3: calidad 0.6619 → 0.6620, coupling 0.74 → 0.73 y complejas 11 → 11; baseline/reglas intactas. El gate heredado no se declara aprobado.
- Aceptación OCI, presencia real de vectorDbId, continuidad/aislamiento de checkpoints, versiones/dependencias del compute y ejecución real del runner pendientes. El coordinador posee Browser, Docker, colas y mutaciones OCI, en orden Governance → Market → GodEye.
- Preflight Apify solo lee cuenta/actores/builds fijados; ejecución y entitlement siguen `not_verified`. No iniciar actores pagados ni campañas de respuestas como parte de la instalación. El probe exige revisión exacta y cero llamadas modelo/capturas; TEST queda pausado.
- Locks con el mismo bucket excluyen procesos; configuraciones con buckets distintos sobre un workspace requieren serialización global. No hay transferencia de state ni adopción automática.
- PROD/delete/transferencia de state siguen sin implementar. Ver [operaciones](operations.md) para update/redeploy/rollback probados con mocks, respaldo y recuperación.
