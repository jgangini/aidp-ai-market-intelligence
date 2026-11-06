# Despliegue independiente de Market Intelligence

Market se instala desde Deploy Studio mediante paquete Python, hooks y artefactos AIDP. No crea portal, API HTTP permanente, VM, frontend ni contraseña local. El candidato conserva TEST instalado/pausado, sin inferencia ni capturas Apify en la comprobación de instalación.

La ficha visible se llama **AIDP AI Market Intelligence**; el ID técnico sigue siendo `aidp-ai-market-intelligence`. Su descripción presenta agente, AI Compute, credenciales y conectores incluidos, mientras los límites de aceptación permanecen en esta guía y en la release. `presentation.image` reutiliza `/assets/oci-aidp-cloud-migration-lab.png`, asset AIDP ya aprobado y servido desde `app/frontend/public/assets/` de CloudTechNext. El componente `ProjectBrief` usa esa ruta pública local directamente: no es una URL raw de GitHub privado ni requiere token en el navegador. El asset no se copia a este producto ni añade dependencias de runtime; el catálogo de CTN pertenece al coordinador.

## Contrato del candidato

`terraform/deploy-studio.json` usa schema 1 y proyecto `aidp-ai-market-intelligence`. `identity_domain_id` es un selector obligatorio con `options_source=oci_identity_domains`, sin valor predeterminado; conserva el patrón OCID del dominio. El contrato solicitado al consumidor es listar dominios ACTIVE desde la home region descubierta, con OCID como valor y nombre más compartimento como etiqueta. Esa consulta y su prueba real pertenecen al coordinador; este producto no consulta OCI para poblar el formulario. Seleccionar el dominio no sustituye las comprobaciones de identidad y roles de los hooks. El campo `admin_user_ocid` usa el origen genérico `oci_aidp_administrators`. Preparar el manifiesto no acredita que la versión desplegada de Deploy Studio ya acepte ambos orígenes: parser y runner reales requieren validación integrada antes de publicar la release.

El coordinador verificó el candidato histórico `cff62ec` con Settings reales del consumidor: máximo de 250 archivos seleccionados y 10 MiB descomprimidos; el archivo GitHub admite hasta 100 MiB. Aquel árbol seleccionaba 72 archivos, 859667 bytes y ambos hooks importaban aislados en Python 3.12 sin red ni credenciales. El tamaño y las comprobaciones de la release vigente se registran en [el handoff](release-handoff.md); importar hooks no acredita instalación ni permisos OCI.

El operador autorizado firma las peticiones OCI. `admin_user_ocid` selecciona el propietario elegible, no cambia la identidad firmante. Preflight y Resume deben revalidar usuario activo, dominio, destino y permisos efectivos. Para plataforma nueva, el selector admite identidades activas del dominio elegibles para asignación: bootstrap asigna el rol autorizado y comprueba su efectividad antes de publicar archivos/agente. Para plataforma existente se exigen roles efectivos. No se crea otro provisioner ni se solicita contraseña local.

| Input | Uso |
| --- | --- |
| `aidp_mode` | `new` crea una plataforma; `existing` referencia su OCID sin administrar su estado Terraform. |
| `existing_ai_data_platform_ocid`, `existing_workspace_key` | Referencias exactas obligatorias solo en modo existing. Un nombre no permite adopción. |
| `identity_domain_id`, `admin_user_ocid` | Dominio y usuario seleccionados, sujetos a validación de los hooks. |
| `agent_model_id` | Referencia OCI incluida en el runtime pausado; nueva instalación y update congelan su binding. Resume conserva el recibo. |
| `artifacts_bucket_mode`, `artifacts_bucket_name` | Crear el único bucket de artefactos requerido o validar uno externo sin importarlo al estado. |
| `apify_token` | Secreto separado para preflight/post-apply; no es variable Terraform ni output. |
| `autonomous_database_mode` | Crear memoria dedicada o referenciar una base existente sin adoptarla. |
| `autonomous_database_version`, `autonomous_database_workload` | Perfil `26ai:DW` transformado por CTN en versión `26ai` y workload `DW`. |
| `autonomous_database_compute_count` | Entero entre 2 y 512 ECPU, 4 por defecto; sin autoscaling. |
| `existing_autonomous_database_ocid` | Base exacta, AVAILABLE, 26ai DW en la región seleccionada. |
| `autonomous_database_admin_password` | Contraseña ADMIN protegida para crear la base o habilitar AI; se omite al reutilizar una asociación READY exacta. |

Deploy Studio aporta `tenancy_ocid`, `region`, `compartment_ocid`, `objectstorage_namespace` y `deployment_suffix`. Preflight resuelve `home_region` y `identity_domain_url` desde OCI y confirma `objectstorage_namespace` con el SDK; devuelve únicamente esos tres overrides declarados. Su contexto real lleva un ID transitorio `preflight-<hex>`, que no es UUID ni identidad de instalación: no se persiste como variable Terraform, output ni operación. El contexto post-apply contiene el ID durable del despliegue; el instalador deriva su UUID de operación de forma determinista mediante UUID5 sobre ese ID completo y un namespace fijo. Resume usa el mismo resultado, no un UUID nuevo. La URL del dominio se comprueba contra su ID; no es un endpoint arbitrario proporcionado por el navegador.

Terraform entrega `ai_data_platform_ocid`, `workspace_key`, `workspace_name`, `artifacts_bucket_name`, `objectstorage_namespace`, `region`, `home_region`, `compartment_id`, `identity_domain_id`, `identity_domain_url`, `admin_user_ocid`, `agent_model_id`, `owned_ai_data_platform`, `owned_artifacts_bucket` y `deployment_binding`. Son bindings sin secretos. `workspace_key` permanece vacío en modo new: post-apply consulta el workspace nativo exacto del nombre `ai-market-intelligence-<deployment_suffix>` creado por esa plataforma y fija su identidad en el recibo. No inventa una clave ni reutiliza un workspace por coincidencia aproximada. El manifiesto admite además resultados públicos de hooks `installation_status`, `installation_phase`, `operation_id`, `research_enabled`, `deployment_surface` y `apify_execution_permission`; no son recursos ni outputs Terraform fabricados.

## Recursos y propiedad

Los grupos del formulario son `aidp_platform`, `identity`, `artifacts`, `enterprise_ai` y `database`. `existing_ai_data_platform_ocid` usa `oci_aidp_platforms`, con patrón OCID conservado. El consumidor enumera plataformas ACTIVE por región/compartimento; este producto vuelve a validar la identidad y el workspace. Los campos de operación pertenecen a Workbench, modelo/Apify a Enterprise AI y memoria a Database. El título y PNG aprobados se conservan.

Modo new crea plataforma, política del servicio AIDP derivada del corte Starter Kits `3c82617fd298933995e954955841007253cc0985` y espera de propagación IAM. Esa política autoriza al servicio AIDP, no concede el rol administrativo a la persona seleccionada. Plataforma y bucket nuevos llevan tag `deployment_binding`, igual al SHA-256 de `tenancy_ocid:region:compartment_ocid:deployment_suffix:identity_domain_id:admin_user_ocid`. El hook verifica los bindings de contexto/output y tags vivos antes de asignar roles o publicar recursos. El sufijo estable procede del runner, no del preflight transitorio; el hash vincula recursos a configuración pero no concede permisos. Modo existing no crea ni adopta plataforma o políticas; su configuración y acceso deben comprobarse en preflight. El bucket solo entra como recurso administrado cuando se selecciona new; es privado y versionado. Cambiar un selector después de aplicar no transfiere propiedad ni autoriza destrucción: revisar un plan específico de transición antes de continuar.

No se crean medallion buckets, tablas ni workflows Spark ficticios. El catálogo/archivos/agente y AI Compute se gestionan mediante el instalador AIDP, con recibo v2 y operación estable. Recursos externos no se eliminan desde este stack. Los hooks conservan operación, propietario, claves, hashes y resultados inciertos; nunca sustituyen un recurso ajeno para obtener éxito.

El candidato no certifica por sí solo permisos IAM nativos, rol del administrador, compatibilidad regional, instalación completa ni calidad de investigación. Antes de cloud el coordinador integra parser/runner y serializa las operaciones compartidas. Update/redeploy deben mantener el recibo y el candidato anterior recuperable; destruir y recrear no es un redespliegue.

## Memoria nativa y Autonomous

El grafo conserva `AsyncProxyCheckpointClient` de Oracle. [Oracle documenta](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/agent-memory.html) que la memoria y sesiones residen en el Autonomous AI Lakehouse enlazado a la plataforma. El producto configura esa asociación con [`EnableAiFeatureDetails`](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/ai_data_platform/models/oci.ai_data_platform.models.EnableAiFeatureDetails.html), sin crear tablas privadas del proveedor ni conectar el agente por SQL.

Terraform crea, en modo new, una base 26ai DW con 4 ECPU por defecto, 1 TB, licencia incluida, mTLS y autoscaling desactivado. `prevent_destroy` protege la base de memoria. En modo existing solo consulta el recurso y valida ID, estado, versión y workload. Los outputs `autonomous_database_id` y `owned_autonomous_database` contienen referencias y propiedad, nunca contraseñas. El post-apply revalida el administrador antes de escribir la intención de habilitación o llamar a OCI.

La contraseña ADMIN llega al hook en `secret_inputs`; únicamente la creación de Autonomous la utiliza como variable Terraform sensible. Por ello el stack/state de creación contiene material secreto y requiere la protección privada del runner. No se envía la contraseña a `vector_db_admin_cred` del recurso AIDP de Terraform: el proveedor 8.28.0 no marca ese atributo como sensible. La habilitación se realiza desde el hook protegido con ETag, sin retries automáticos. Apify mantiene su separación completa de Terraform.

`capabilities.database_profile=new_or_existing` y `existing_database_wallet=none` requieren el consumidor CTN actualizado. El enlace control-plane usa OCID y ADMIN, sin wallet, usuario SQL ni contraseña wallet. Los productos con SQL directo conservan sus contratos propios; no se eliminan por analogía.

Una plataforma READY solo se reutiliza si el GET firmado devuelve `vectorDbId` igual al OCID elegido. El SDK 2.189.0 descarta ese campo en su modelo tipado; se conserva el JSON del mismo GET soportado mediante `skip_deserialization=True`. Si el servidor no devuelve la asociación, esta difiere o AI está FAILED, se bloquea la instalación. La presencia real de ese campo y los permisos de enablement siguen pendientes de aceptación nativa. READY por sí solo y HTTP 409 no prueban la base asociada.

No se deshabilita AI, cambia la asociación, elimina memoria ni modifica retención. Governance y Market sobre una plataforma deben seleccionar la misma base ya asociada. El [repositorio Governance](https://github.com/jgangini/aidp-ai-data-gobernance) es independiente; sus recursos, roles y prefijos se conservan. Una segunda base no se presenta como memoria independiente dentro de una plataforma que ya está configurada.

## Modelo configurado e investigación pausada

El paquete AIDP 2.0.1 permite un binding pausado exacto `services={model_id,region,compartment_id}`. Se inserta en los bytes del agente, queda congelado en `runtime_config` del recibo y en el hash del descriptor. `configured_agent_model_id` muestra ese valor guardado; `agent_model_id` Terraform representa la selección solicitada. Resume no reemplaza el modelo desde entradas nuevas. Update guarda un target separado y rollback restaura el binding anterior; la identidad automática de mantenimiento incluye su digest. Redeploy exige la misma configuración.

Este binding no inventa un perfil nativo ni autorización ilimitada. Activar investigación sigue exigiendo el contrato completo de servicios, identidad, perfil de dependencias observado, fuentes, guardrails y permiso de gasto. El probe pausado verifica fuentes y cero llamadas; no acredita continuidad de checkpoints, conexión de memoria ni inferencia.

## Comprobaciones locales

Las pruebas Python comprueban el límite de secretos y el contrato de empaquetado. `terraform/tests/ownership.tftest.hcl` usa proveedores simulados para comprobar creación nueva, cero recursos externos administrados y rechazo de selecciones incompletas u obsoletas. Requiere Terraform 1.7+ y proveedores disponibles localmente. No ejecutar apply ni descargar proveedores como efecto lateral de una revisión.

```powershell
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform validate
terraform -chdir=terraform test
```

`validate` y `test` requieren inicialización y plugins OCI/time; si no están disponibles se informa la limitación, sin atribuir validación nativa a una comprobación de texto. Secretos, planes y estado permanecen fuera de Git, artefactos públicos y logs.

Validación del candidato el 2026-11-05: Terraform 1.15.6, `init -backend=false` con descarga autorizada a este destino, `fmt -check -recursive`, `validate` y cinco pruebas nativas de ownership con proveedores simulados aprobados. El lock fija OCI 8.28.0 y time 0.14.2. No se ejecutó apply ni petición OCI real. Las pruebas Python cubren la separación de secretos, selección de administrador requerida sin grants implícitos, recursos permitidos, archivo portable y exclusión del ID preflight transitorio; no sustituyen las pruebas de roles efectivos del preflight.
