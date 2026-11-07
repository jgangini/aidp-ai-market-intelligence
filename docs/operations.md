# Operación y recuperación

Deploy Studio gestiona Terraform y llama a los hooks de instalación del paquete fijado por release/commit. Su formulario usa `install` por defecto y permite Resume; no expone Lifecycle ni UUID de mantenimiento. La CLI administrativa opera una instalación existente. Ambas rutas conservan identidad, recibos y configuración; la investigación permanece pausada en TEST.

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

El mantenimiento mediante CLI requiere instalación ready del mismo propietario, AIDP/workspace, agente, compute y credenciales. El stack original conserva la propiedad; no convertir sus selectores para simular una transferencia de state. Un despliegue PROD que comparta las fuentes bloquea su modificación. El formulario de instalación no inicia una operación administrativa.

La CLI exige `--operation-id` con UUID explícito para update/redeploy/rollback; conservarlo durante la reanudación. Los hooks internos conservan la derivación de operación por despliegue, acción, paquete y modelo para sus contratos administrativos; no se selecciona esa acción desde el formulario de instalación. La sesión TEST se reutiliza y contrasta con agente, compute y endpoint. No se crean sesiones nuevas ni se reinician investigaciones, checkpoints o gasto para actualizar.

El journal guarda respaldos exactos, hashes y estados anterior/objetivo antes de sobrescribir archivos. Solo acepta bytes conocidos; drift ajeno bloquea. El refresh se registra antes de enviarse y una respuesta incierta se reconcilia en lugar de duplicarse. La nueva revisión debe estar ACTIVE y superar el probe antes de confirmar el recibo. Un fallo confirmado restaura el anterior.

Si update/redeploy termina `rolled_back`, esa actualización sigue fallida y devuelve error. Un rollback solicitado y verificado sí puede cerrar correctamente. Rollback no necesita token ni disponibilidad de Apify o metadata del modelo; en Deploy Studio conserva las verificaciones de identidad, infraestructura y asociación de memoria.

## Memoria: rechazo confirmado o resultado incierto

Antes de habilitar AI, el hook revalida al administrador y reserva una intención en `control/ai_market_intelligence/memory/<hash-plataforma>.json` del bucket de artefactos. Guarda operación, plataforma, base, binding e intento; no guarda ADMIN. El POST usa el ETag de AIDP y desactiva reintentos automáticos.

| Situación | Respuesta operativa |
| --- | --- |
| READY y `vectorDbId` coincide | Reutilizar la asociación, sin otro POST ni contraseña ADMIN. |
| ENABLING con la base esperada o sin asociación visible | El hook espera sin repetir POST. Continúa con READY y coincidencia nativa de la base, o con la procedencia verificable de la solicitud que acaba de enviar. |
| READY sin asociación visible tras el POST de esta invocación | Exige el identificador de work request devuelto por ese POST, operación ENABLE_AI_FEATURES real, plataforma exacta y SUCCEEDED. Conserva el binding de base de la solicitud y sella el ETag nativo mediante CAS. No lo presenta como readback del OCID. |
| Resume con recibo ready y asociación no visible | Exige recibo de la misma operación/binding, WR satisfactoria y ETag de plataforma sin cambios. Un recibo accepted sin sellar requiere conciliación nativa; no puede acreditar una revisión posterior. |
| READY sin procedencia verificable, otra base, estado desconocido/FAILED | La instalación se bloquea. Conservar recursos y revisar la configuración nativa; no sustituir la memoria ni deducir el destino de una intención local o de una WR encontrada por fecha. |
| HTTP 400/401/403/404/412 confirmado | La intención queda rejected mediante CAS. Corregir parámetros/ADMIN/permisos y reanudar la misma operación; se reserva otro intento con ETag y retry token nuevos. |
| Timeout o 409 sin readback READY exacto | Conservar submitted. Reconciliar la solicitud control-plane y su work request; no repetir ni borrar el marcador como atajo. |
| Intención ajena, inválida o modificada | Detenerse para reconciliación; no adoptar ni sobrescribirla. |

El mantenimiento no habilita AI ni cambia de base. Rollback del agente conserva el servicio de memoria y los checkpoints. No se deshabilitan AI, retención o compartición de sesiones como parte de una recuperación.

La espera dispone de 15 minutos y consulta cada 5 segundos con reloj monotónico. No inicia otra consulta al agotar el plazo; una solicitud ya en curso puede prolongarlo hasta sus timeouts de conexión/lectura de 5/20 segundos. Estas lecturas desactivan los reintentos automáticos del SDK. El preflight mantiene el bloqueo de una plataforma existente cuya asociación aún no puede verificarse, especialmente si se intenta crear otra base.

Los resultados pendientes y las asociaciones no verificables tienen mensajes públicos constantes, sin cuerpos de excepciones ni secretos. Ambos devuelven error y outputs vacíos; no se publica `memory_status=READY` ni se instala el agente. El GET público de AIDP no garantiza `vectorDbId`; una intención `submitted` sola nunca sustituye la respuesta aceptada del servicio. La reutilización del recibo requiere que su work request siga consultable; una operación antigua o un cambio de revisión exige conciliación.

La base nueva pertenece al stack y admite su eliminación explícita. Una base existente solo es un data source y no se elimina. El AIDP nuevo depende de la base administrada para que Terraform elimine primero la plataforma. Esto no vacía el bucket de artefactos: la limpieza de sus objetos/versiones y la detención del instalador requieren coordinación previa al DESTROY.

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

La infraestructura funciona con el contrato Terraform 1.5.7 de Resource Manager. El postflight local usa Terraform moderno para las pruebas con mocks; CI comprueba init/formato/validate también con 1.5.7. Mantén ambas comprobaciones al modificar precondiciones o funciones Terraform.

No están implementados activar investigación por un comando público, publicación PROD, delete ni transferencia de state. La aceptación nativa, continuidad/aislamiento de memoria y calidad de investigación siguen siendo comprobaciones separadas.

## Etiquetas de recursos en Deploy Studio

El output no sensible `deploy_studio_resource_labels` relaciona cada dirección exacta de instancia Terraform con una etiqueta breve en inglés. Se resuelve durante PLAN, incluye las instancias seleccionadas de recursos y consultas de datos y omite las que tienen `count = 0`. Las claves de `for_each` conservan su representación JSON entre corchetes. Las etiquetas no contienen OCID, credenciales ni atributos calculados. CloudTechNext puede mostrar estos nombres y conservar la dirección en los detalles técnicos; el inventario, las acciones, el progreso y las dependencias proceden del PLAN y los logs de OCI Resource Manager, no de este mapa ni del orden de los archivos `.tf`.
