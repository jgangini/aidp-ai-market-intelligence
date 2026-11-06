# Despliegue independiente de Market Intelligence

Market se instala desde Deploy Studio mediante paquete Python, hooks y artefactos AIDP. No crea portal, API HTTP permanente, VM, frontend ni contraseña local. El candidato conserva TEST instalado/pausado, sin inferencia ni capturas Apify en la comprobación de instalación.

## Contrato del candidato

`terraform/deploy-studio.json` usa schema 1 y proyecto `aidp-ai-market-intelligence`. El campo `admin_user_ocid` usa el origen genérico `oci_aidp_administrators`, cuya integración del consumidor pertenece al coordinador. Preparar el manifiesto no acredita que la versión desplegada de Deploy Studio ya lo acepte: parser y runner reales requieren validación integrada antes de publicar la release.

El operador autorizado firma las peticiones OCI. `admin_user_ocid` selecciona el propietario elegible, no cambia la identidad firmante. Preflight y Resume deben revalidar usuario activo, dominio, destino y permisos efectivos. Para plataforma nueva, el selector admite identidades activas del dominio elegibles para asignación: bootstrap asigna el rol autorizado y comprueba su efectividad antes de publicar archivos/agente. Para plataforma existente se exigen roles efectivos. No se crea otro provisioner ni se solicita contraseña local.

| Input | Uso |
| --- | --- |
| `aidp_mode` | `new` crea una plataforma; `existing` referencia su OCID sin administrar su estado Terraform. |
| `existing_ai_data_platform_ocid`, `existing_workspace_key` | Referencias exactas obligatorias solo en modo existing. Un nombre no permite adopción. |
| `identity_domain_id`, `admin_user_ocid` | Dominio y usuario seleccionados, sujetos a validación de los hooks. |
| `agent_model_id` | Modelo OCI seleccionado; no habilita investigación. |
| `artifacts_bucket_mode`, `artifacts_bucket_name` | Crear el único bucket de artefactos requerido o validar uno externo sin importarlo al estado. |
| `apify_token` | Secreto separado para preflight/post-apply; no es variable Terraform ni output. |

Deploy Studio aporta `tenancy_ocid`, `region`, `compartment_ocid`, `objectstorage_namespace` y `deployment_suffix`. Preflight resuelve `home_region` y `identity_domain_url` desde OCI y confirma `objectstorage_namespace` con el SDK; devuelve únicamente esos tres overrides declarados. Su contexto real lleva un ID transitorio `preflight-<hex>`, que no es UUID ni identidad de instalación: no se persiste como variable Terraform, output ni operación. El contexto post-apply contiene el ID durable del despliegue; el instalador deriva su UUID de operación de forma determinista mediante UUID5 sobre ese ID completo y un namespace fijo. Resume usa el mismo resultado, no un UUID nuevo. La URL del dominio se comprueba contra su ID; no es un endpoint arbitrario proporcionado por el navegador.

Terraform entrega `ai_data_platform_ocid`, `workspace_key`, `workspace_name`, `artifacts_bucket_name`, `objectstorage_namespace`, `region`, `home_region`, `compartment_id`, `identity_domain_id`, `identity_domain_url`, `admin_user_ocid`, `agent_model_id`, `owned_ai_data_platform`, `owned_artifacts_bucket` y `deployment_binding`. Son bindings sin secretos. `workspace_key` permanece vacío en modo new: post-apply consulta el workspace nativo exacto del nombre `ai-market-intelligence-<deployment_suffix>` creado por esa plataforma y fija su identidad en el recibo. No inventa una clave ni reutiliza un workspace por coincidencia aproximada. El manifiesto admite además resultados públicos de hooks `installation_status`, `installation_phase`, `operation_id`, `research_enabled`, `deployment_surface` y `apify_execution_permission`; no son recursos ni outputs Terraform fabricados.

## Recursos y propiedad

Modo new crea plataforma, política del servicio AIDP derivada del corte Starter Kits `3c82617fd298933995e954955841007253cc0985` y espera de propagación IAM. Esa política autoriza al servicio AIDP, no concede el rol administrativo a la persona seleccionada. Plataforma y bucket nuevos llevan tag `deployment_binding`, igual al SHA-256 de `tenancy_ocid:region:compartment_ocid:deployment_suffix:identity_domain_id:admin_user_ocid`. El hook verifica los bindings de contexto/output y tags vivos antes de asignar roles o publicar recursos. El sufijo estable procede del runner, no del preflight transitorio; el hash vincula recursos a configuración pero no concede permisos. Modo existing no crea ni adopta plataforma o políticas; su configuración y acceso deben comprobarse en preflight. El bucket solo entra como recurso administrado cuando se selecciona new; es privado y versionado. Cambiar un selector después de aplicar no transfiere propiedad ni autoriza destrucción: revisar un plan específico de transición antes de continuar.

No se crean medallion buckets, tablas ni workflows Spark ficticios. El catálogo/archivos/agente y AI Compute se gestionan mediante el instalador AIDP, con recibo v2 y operación estable. Recursos externos no se eliminan desde este stack. Los hooks conservan operación, propietario, claves, hashes y resultados inciertos; nunca sustituyen un recurso ajeno para obtener éxito.

El candidato no certifica por sí solo permisos IAM nativos, rol del administrador, compatibilidad regional, instalación completa ni calidad de investigación. Antes de cloud el coordinador integra parser/runner y serializa las operaciones compartidas. Update/redeploy deben mantener el recibo y el candidato anterior recuperable; destruir y recrear no es un redespliegue.

## Comprobaciones locales

Las pruebas Python comprueban el límite de secretos y el contrato de empaquetado. `terraform/tests/ownership.tftest.hcl` usa proveedores simulados para comprobar creación nueva, cero recursos externos administrados y rechazo de selecciones incompletas u obsoletas. Requiere Terraform 1.7+ y proveedores disponibles localmente. No ejecutar apply ni descargar proveedores como efecto lateral de una revisión.

```powershell
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform validate
terraform -chdir=terraform test
```

`validate` y `test` requieren inicialización y plugins OCI/time; si no están disponibles se informa la limitación, sin atribuir validación nativa a una comprobación de texto. Secretos, planes y estado permanecen fuera de Git, artefactos públicos y logs.

Validación del candidato el 2026-11-05: Terraform 1.15.6, `init -backend=false` con descarga autorizada a este destino, `fmt -check -recursive`, `validate` y cinco pruebas nativas de ownership con proveedores simulados aprobados. El lock fija OCI 8.28.0 y time 0.14.2. No se ejecutó apply ni petición OCI real. Las pruebas Python cubren la separación de secretos, selección de administrador requerida sin grants implícitos, recursos permitidos, archivo portable y exclusión del ID preflight transitorio; no sustituyen las pruebas de roles efectivos del preflight.
