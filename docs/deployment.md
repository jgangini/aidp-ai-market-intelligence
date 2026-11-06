# Despliegue con Deploy Studio

El manifiesto `terraform/deploy-studio.json` define el proyecto `aidp-ai-market-intelligence`, schema 2. Deploy Studio fija repositorio, release y commit, prepara Terraform y ejecuta los hooks con la identidad OCI del operador. El resultado previsto es un agente TEST con investigación pausada.

## Preparación

El operador necesita permisos para consultar identidad, región, modelo y recursos existentes, y para crear los recursos seleccionados como nuevos. El administrador elegido debe estar activo en el Identity Domain indicado. Una plataforma existente exige roles AIDP efectivos; en una plataforma nueva el instalador asigna y vuelve a comprobar el rol autorizado antes de publicar el agente.

El runner requiere las dependencias de `uv.lock`. La compatibilidad del AI Compute y sus paquetes se verifica por separado; instalar dependencias de hooks no certifica el entorno del agente.

La configuración de infraestructura admite Terraform **1.5.7**, versión CLI de la línea 1.5.x de [OCI Resource Manager](https://docs.oracle.com/en-us/iaas/Content/ResourceManager/Reference/terraformversions.htm). CI ejecuta init/formato/validate con 1.5.7 y 1.15.6; las pruebas con `mock_provider` se ejecutan con 1.15.6. La versión moderna de pruebas no es un requisito del despliegue.

La integración CTN debe admitir los grupos `aidp_platform`, `identity`, `artifacts`, `enterprise_ai`, `database`; los orígenes `oci_aidp_platforms`, `oci_aidp_workspaces`, `oci_identity_domains`, `oci_aidp_administrators`, `oci_genai_chat_models`, `oci_active_buckets`, `oci_autonomous_databases`; y `database_profile=new_or_existing` con `existing_database_wallet=none`. El selector de perfil transforma `26ai:DW` en versión `26ai` y workload `DW`.

## Crear o reutilizar servicios

| Servicio | Crear (`new`) | Reutilizar (`existing`) |
| --- | --- | --- |
| AIDP | Plataforma, workspace predeterminado y política del servicio, con propagación IAM. | OCID exacto y clave nativa del workspace; no importa el recurso al state. |
| Artefactos | Bucket privado, versionado y con binding de propiedad. | Namespace/bucket accesibles en el destino autorizado. |
| Memoria | Autonomous 26ai DW, 4 ECPU por defecto, 1 TB, licencia incluida, mTLS, sin autoscaling y `prevent_destroy`. | Base exacta AVAILABLE, 26ai DW; solo se consulta y valida. |

Los recursos nuevos se vinculan al despliegue mediante `deployment_binding`, calculado con tenancy, región, compartment, sufijo estable, dominio y administrador. Los recursos externos conservan su propietario. Cambiar un selector de new a existing en el stack original no transfiere propiedad: puede implicar retirar recursos del plan y debe tratarse como una transición explícita.

### Instalación nueva

1. Selecciona proyecto y release, carga la configuración OCI mediante el flujo protegido de Deploy Studio y elige región/compartment compatibles.
2. En Workbench selecciona `aidp_mode=new` y el nombre de la plataforma, inicialmente `aidp_market_intelligence`; no arrastres OCID ni workspace de otra plataforma. Terraform utiliza ese valor como `display_name` real. El workspace mantiene su nombre interno estable y el instalador resuelve su clave nativa exacta.
3. Selecciona dominio, administrador, modelo y los modos de bucket/base. Crear memoria requiere ADMIN.
4. Ejecuta preflight, revisa sus eventos y el plan Terraform. Autoriza apply únicamente para los recursos deseados.
5. Post-apply verifica recursos y propiedad, enlaza memoria, instala credenciales/fuentes/compute y comprueba TEST. Si queda pendiente, conserva el despliegue y usa Resume.

### Instalación sobre AIDP existente

Selecciona `aidp_mode=existing`, la plataforma de la lista y su compartment existente. Selecciona un workspace de la lista de la plataforma elegida: el formulario envía su **clave nativa**, no el nombre visible ni una ruta. Los hooks contrastan plataforma, compartment, workspace, identidad y permisos.

Si AI ya está READY, elige la misma base asociada. El GET firmado debe devolver `vectorDbId` igual al OCID indicado; si el campo falta, la base difiere o el estado es inválido, la instalación se bloquea. No se reemplaza una asociación de memoria. Para una plataforma sin AI habilitado, el hook requiere ADMIN y habilita la asociación elegida con ETag e intención durable.

Governance y Market sobre la misma plataforma deben conservar esa asociación de memoria y serializar operaciones sobre el workspace. Los prefijos, roles y recursos de otros módulos permanecen fuera de la instalación Market.

## Parámetros del formulario

El formulario realiza instalación/Resume con `install` como valor predeterminado en Terraform y hooks. No ofrece Lifecycle ni UUID de mantenimiento. Update/redeploy/rollback se ejecutan mediante la CLI administrativa, con sus identificadores explícitos y validación de permisos.

| Grupo / campos | Uso |
| --- | --- |
| Workbench: `aidp_mode` | Crear o seleccionar una plataforma ACTIVE. |
| `new_aidp_name` | New AIDP name, inicialmente `aidp_market_intelligence`, solo al crear; llega al recurso AIDP de Terraform. |
| `existing_ai_data_platform_ocid`, `existing_workspace_key` | Referencias exactas requeridas en modo existing. |
| Identidad: `identity_domain_id`, `admin_user_ocid` | Dominio y propietario elegibles, revalidados server-side. |
| Artefactos: `artifacts_bucket_mode` | Create new bucket o Use existing bucket; inicialmente existing, igual que Governance. |
| `artifacts_bucket_name` | Nombre exacto para crear un bucket nuevo. |
| `existing_artifacts_bucket_name` | Selector `oci_active_buckets` para reutilizar un bucket; preflight resuelve su nombre canónico para Terraform y los hooks. |
| Enterprise AI: `agent_model_id` | Modelo OCI ACTIVE guardado en el runtime pausado; misma preferencia `xai.grok-4.7` que Governance. CTN resuelve esa preferencia al OCID disponible antes de enviar la selección. |
| `apify_token` | Secreto para install/update/redeploy; rollback no lo utiliza. |
| Memoria: `autonomous_database_mode` | Crear o reutilizar Autonomous. |
| `autonomous_database_version`, `autonomous_database_workload` | Perfil 26ai DW. |
| `existing_autonomous_database_ocid` | Selector Existing Autonomous Database (`oci_autonomous_databases`); conserva OCID exacto y validación 26ai DW. |
| `autonomous_database_admin_password` | ADMIN protegido para creación o habilitación AI; puede omitirse al reutilizar una asociación READY exacta. |

Deploy Studio aporta tenancy, región, compartment y sufijo del despliegue. Preflight obtiene home region, endpoint del dominio y namespace desde OCI; no acepta un endpoint de identidad arbitrario del navegador.

El formulario mantiene el mismo orden, campos visibles, etiquetas y valores iniciales que Governance, salvo el nombre propio de Market y el token Apify junto a Select Model. El perfil visible es `Oracle AI Database 26ai · AI Lakehouse (DW)`. Los nombres internos de parámetros conservan los bindings de Market. `test_session_id` y `autonomous_database_compute_count` no aparecen ni se aceptan como campos del formulario: Terraform conserva sus defaults `""` y `4`. Sin UUID explícito, el hook entrega `None` al instalador y Resume conserva la sesión registrada. La CLI y Terraform directo mantienen sus parámetros avanzados.

Apify aporta datos web y sociales mediante Actors que el agente usa para investigación con evidencias. El token se obtiene en Apify Console → Settings → API & Integrations ([documentación oficial](https://docs.apify.com/integrations/api), [ajustes de cuenta](https://help.apify.com/en/articles/13929475-account-settings)). Preflight consulta la cuenta y la visibilidad de Actors/builds configurados, sin iniciar runs. Esto no acredita permisos de ejecución, suscripción ni acceso de pago. La investigación permanece pausada y el token sigue por el canal protegido.

## Identidad y secretos

El operador firma las solicitudes. `admin_user_ocid` identifica al propietario autorizado, sin crear otro provisioner ni cambiar la identidad firmante. Se revalida antes de habilitar memoria y durante instalación/mantenimiento. Una revocación detiene el avance.

Apify se entrega por `secret_inputs` y Credential Store: no entra en Terraform, outputs ni código generado. ADMIN llega también por el sobre protegido del hook; la creación de Autonomous lo usa como variable Terraform sensible y, por tanto, puede permanecer en el **state privado**. Se evita el atributo AIDP Terraform `vector_db_admin_cred`, que el proveedor no marca como sensible. No se requiere wallet, usuario SQL ni contraseña de wallet para este enlace control-plane.

No guardar secretos, config OCI, state o recibos reales en Git, logs o respuestas del navegador. Los errores públicos del hook omiten los cuerpos del SDK y de validación.

## Memoria, modelo y resultado

El agente conserva el proxy de memoria de Oracle (`AsyncProxyCheckpointClient`); no crea tablas del proveedor. El hook configura el enlace nativo mediante `EnableAiFeatureDetails` y exige READY con asociación exacta. Véanse [memoria de agentes](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/agent-memory.html) y [habilitación AI](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/ai-feature-enablement.html) de Oracle.

El modelo se guarda como `services={model_id,region,compartment_id}` en la fuente pausada y en `runtime_config` del recibo. `configured_agent_model_id` informa el valor guardado al consultar estado o completar la instalación; `agent_model_id` representa la selección solicitada. El hook de mantenimiento puede devolver `configured_agent_model_id` vacío porque su resultado no incluye ese dato: consulta `status` para comprobar el modelo después de update/redeploy/rollback. Resume conserva el snapshot y rollback restaura el anterior. Activar investigación requiere el contrato completo de servicios, identidad, perfil nativo observado, fuentes, guardrails y autorización de gasto; el instalador no los inventa.

Los outputs identifican plataforma/workspace, bucket/namespace, base y propiedad, región, dominio, administrador, modelo, operación y estado. `workspace_key` de una plataforma nueva se resuelve nativamente durante post-apply. `memory_status=READY` y un probe pausado no acreditan continuidad o aislamiento de checkpoints ni calidad de investigación. Esas comprobaciones siguen pendientes de aceptación nativa.

Para Resume, actualización, fallos y locks, consulta [operación](operations.md).
## Formulario declarativo de Deploy Studio

`terraform/deploy-studio.json` utiliza `schema_version: 2`. `form.groups` declara los títulos y el orden de los grupos; cada lista `items` fija el orden de campos, archivos y controles comunes, y `span` su ancho en columnas. Los campos declaran valores predeterminados y condiciones de visibilidad. CloudTechNext valida las referencias y requiere este contrato; no reconstruye formularios antiguos. Las credenciales se cargan en el grupo `credentials`; los demás controles se habilitan tras validarlas. Las releases publicadas conservan sus manifiestos inmutables: este contrato requiere una nueva release y un motor compatible.

El grupo inicial contiene solo `oci_config` y `oci_key`. El destino declara `oci_region`, `compartment_mode`, `new_compartment` y `existing_compartment`, con etiquetas y opciones en el JSON; estas últimas coinciden con `capabilities.compartment_modes`. Cada campo y archivo se referencia exactamente una vez. Market conserva `existing_database_wallet=none`, por lo que no declara el control `database_wallet`. La versión del contexto de hooks sigue siendo 1: es un contrato distinto del schema del formulario.

La contraseña ADMIN declara `generate_password=true` y `generate_password_when={field: autonomous_database_mode, equals: new}`. El motor ofrece el generador solo al crear una base; al usar una existente no genera otra contraseña. El token Apify se proporciona por separado y no declara generador. Ambos valores siguen siendo secretos y se entregan únicamente por los canales protegidos existentes.
