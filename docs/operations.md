# Operación y recuperación

Deploy Studio gestiona Terraform y llama a los hooks del paquete fijado por release/commit. La CLI opera una instalación existente. Ambas rutas conservan identidad, recibos y configuración; la investigación permanece pausada en TEST.

## Estado y Resume

El instalador guarda su recibo v2 en `/Workspace/.control/modules/ai_market_intelligence/installer.json` y publica fuentes bajo `/Workspace/.artifact/ai_market_intelligence`. El avance recorre verificación, credenciales, compute, archivos, agente, permisos y TEST. Post-apply avanza como máximo 80 pasos por invocación.

Una operación pendiente se reanuda con el mismo despliegue, release, administrador y destino. El ID durable `dep_<16hex>` determina el UUID original de instalación; el ID transitorio de preflight no puede sustituirlo. No iniciar otra instalación ni borrar recibos para resolver un timeout. El modelo guardado en `runtime_config` se conserva aunque cambie la selección actual del formulario.

La observación del estado no instala recursos. `status` informa el modelo guardado; el output `configured_agent_model_id` del hook tras update/redeploy/rollback puede estar vacío y no debe interpretarse como pérdida de configuración. TEST ACTIVE e investigación pausada son estados compatibles. El probe exige fuente y sesión exactas y cero llamadas modelo/Apify; no verifica la continuidad del checkpointer ni la calidad de las respuestas.

## Acciones de mantenimiento

| Acción | Comportamiento |
| --- | --- |
| `install` | Instala o reanuda el agente TEST de esta instalación. |
| `update` | Congela paquete/configuración objetivo, respalda el anterior y publica otra revisión TEST. |
| `redeploy` | Redespliega el mismo paquete y configuración; un cambio de modelo requiere update. |
| `rollback` | Restaura el candidato y configuración anteriores registrados. |

El mantenimiento requiere instalación ready del mismo propietario, AIDP/workspace, agente, compute y credenciales. En una solicitud nueva de Deploy Studio se seleccionan **AIDP, bucket y base existing**. El stack original conserva la propiedad; no convertir sus selectores para simular una transferencia de state. Un despliegue PROD que comparta las fuentes bloquea su modificación.

Update/redeploy derivan una operación del despliegue durable, acción, hash del paquete y digest del modelo. `operation_request_id` permite otro UUID explícito para una nueva solicitud; conservarlo durante Resume. La sesión TEST se reutiliza y contrasta con agente, compute y endpoint. No se crean sesiones nuevas ni se reinician investigaciones, checkpoints o gasto para actualizar.

El journal guarda respaldos exactos, hashes y estados anterior/objetivo antes de sobrescribir archivos. Solo acepta bytes conocidos; drift ajeno bloquea. El refresh se registra antes de enviarse y una respuesta incierta se reconcilia en lugar de duplicarse. La nueva revisión debe estar ACTIVE y superar el probe antes de confirmar el recibo. Un fallo confirmado restaura el anterior.

Si update/redeploy termina `rolled_back`, esa actualización sigue fallida y devuelve error. Un rollback solicitado y verificado sí puede cerrar correctamente. Rollback no necesita token ni disponibilidad de Apify o metadata del modelo; en Deploy Studio conserva las verificaciones de identidad, infraestructura y asociación de memoria.

## Memoria: rechazo confirmado o resultado incierto

Antes de habilitar AI, el hook revalida al administrador y reserva una intención en `control/ai_market_intelligence/memory/<hash-plataforma>.json` del bucket de artefactos. Guarda operación, plataforma, base, binding e intento; no guarda ADMIN. El POST usa el ETag de AIDP y desactiva reintentos automáticos.

| Situación | Respuesta operativa |
| --- | --- |
| READY y `vectorDbId` coincide | Reutilizar la asociación, sin otro POST ni contraseña ADMIN. |
| ENABLING con la base esperada | Esperar y reanudar el mismo despliegue. |
| Asociación ausente, otra base, estado desconocido/FAILED | Conservar recursos y revisar la configuración nativa; no sustituir la memoria. |
| HTTP 400/401/403/404/412 confirmado | La intención queda rejected mediante CAS. Corregir parámetros/ADMIN/permisos y reanudar la misma operación; se reserva otro intento con ETag y retry token nuevos. |
| Timeout o 409 sin readback READY exacto | Conservar submitted. Reconciliar la solicitud control-plane y su work request; no repetir ni borrar el marcador como atajo. |
| Intención ajena, inválida o modificada | Detenerse para reconciliación; no adoptar ni sobrescribirla. |

El mantenimiento no habilita AI ni cambia de base. Rollback del agente conserva el servicio de memoria y los checkpoints. No se deshabilitan AI, retención o compartición de sesiones como parte de una recuperación.

## Locks y concurrencia

Instalación/mantenimiento usan un lock Object Storage por plataforma/workspace dentro del bucket configurado: creación `If-None-Match: *`, liberación con ETag exacto, sin expiración ni takeover automático.

Si queda un lock tras un proceso incierto, confirma que no haya un hook ejecutándose y consulta su ETag. La CLI `release-lock` exige `--expected-etag`, `--confirm-no-running-hook` y `--apply`, y vuelve a validar al administrador. No liberar por nombre ni con un ETag obsoleto.

Dos configuraciones con distintos buckets no comparten ese lock, aunque apunten al mismo workspace. Conservar el binding de bucket y serializar externamente todas las operaciones sobre el workspace. La exclusión global entre buckets no está implementada.

## CLI sobre un destino existente

`Settings` acepta `oci_config_file`, `aidp_region`, `aidp_platform_id`, `aidp_workspace_key`/`aidp_workspace_name`, `compartment_id`, `objectstorage_namespace`, `artifacts_bucket_name`, `agent_model_id`, `identity_domain_id` y `admin_user_ocid`. Guarda el archivo fuera de Git y usa referencias exactas.

```powershell
uv run aidp-market --help
uv run aidp-market status --settings .local/market-settings.json
```

`status` consulta OCI y revalida permisos. La CLI no crea plataforma/base ni prepara o comprueba la asociación de memoria: esa responsabilidad corresponde al despliegue o a la configuración nativa autorizada antes de usarla.

Install/update/redeploy requieren `--operation-id` con UUID canónico, `--token-file` con la ruta al secreto Apify y `--apply`; `--test-session-id` selecciona una sesión existente cuando corresponde. Rollback usa UUID y `--apply`, sin token. Mantén los identificadores al reanudar. Nunca pasar el token real como argumento ni pegar settings/recibos en logs.

## Secretos y comprobaciones

El operador sigue firmando; el administrador no se suplanta. Se revalidan dominio, actividad y roles durante los avances. Apify consulta cuenta/actor/build en lectura; 401/403 requieren revisar credencial/acceso y 404 puede indicar ausencia o falta de visibilidad. Las lecturas no prueban entitlement de ejecución. Los errores públicos omiten cuerpos SDK y secretos.

El state/plan de creación puede contener ADMIN pese a `sensitive=true`; mantenerlo en almacenamiento privado. Apify queda fuera de Terraform. Los hooks reciben contexto/secrets por archivos protegidos del runner y no instalan dependencias durante la ejecución.

Para validación local utiliza `scripts/arch-postflight.ps1`: pruebas Python, package-check, protocolo Node, formato/validación/tests Terraform, límites y Sentrux. Requiere proveedores inicializados; no ejecuta apply ni descarga proveedores. Conserva la baseline existente: `arch-preflight.ps1` se detiene si ya existe para impedir sobrescribirla. Las pruebas con mocks no acreditan IAM, red, entorno del compute, asociación real ni persistencia de checkpoints.

No están implementados activar investigación por un comando público, publicación PROD, delete ni transferencia de state. La aceptación nativa, continuidad/aislamiento de memoria y calidad de investigación siguen siendo comprobaciones separadas.
