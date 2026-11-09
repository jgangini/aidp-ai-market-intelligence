# Despliegue con Deploy Studio

## Requisitos

- Cuenta OCI con permisos para el destino, los recursos seleccionados y el modelo regional.
- Identity Domain activo y administrador AIDP elegible. El operador firma; el administrador no es una credencial de suplantación.
- Autonomous AI Lakehouse 26ai DW, nuevo o existente, para memoria de Agent Flow.
- Bucket de artefactos privado y credencial Apify protegida.
- Runner Deploy Studio compatible con `terraform/deploy-studio.json`, schema 2, y las dependencias de `uv.lock`.

Terraform admite 1.5.7 de OCI Resource Manager. CI comprueba también la versión moderna declarada en su matriz; las pruebas `mock_provider` requieren esa versión moderna. El entorno del runner y el AI Compute se comprueban por separado.

## Crear o reutilizar recursos

| Servicio | Crear | Reutilizar |
| --- | --- | --- |
| AIDP | Plataforma, workspace y permisos del servicio. | Plataforma y clave nativa del workspace exactas. |
| Artefactos | Bucket privado versionado con binding de propiedad. | Namespace y bucket existentes accesibles. |
| Memoria | Autonomous 26ai DW con `prevent_destroy`. | Base AVAILABLE, 26ai DW, sin importar su propiedad al state. |

Los recursos nuevos quedan vinculados mediante `deployment_binding`; los externos conservan su propietario. Cambiar un selector no transfiere propiedad ni state. Si se comparte plataforma con otros productos, conservar memoria y recursos comunes y serializar las operaciones del workspace.

## Formulario

`form.groups` define orden y distribución; `form.fields` fija tipos, valores y visibilidad. El consumidor valida referencias y entrega archivos de credenciales por su canal protegido. `execution_source` selecciona OCI Resource Manager; `run_steps` etiqueta fases, mientras el inventario de recursos procede del PLAN.

| Campo | Uso |
| --- | --- |
| `aidp_mode` | Crear o reutilizar plataforma. |
| `new_aidp_name` | Nombre al crear; inicialmente `aidp_market_intelligence`. |
| `existing_ai_data_platform_ocid`, `existing_workspace_key` | Plataforma y clave nativa del workspace existente. |
| `identity_domain_id`, `admin_user_ocid` | Dominio y administrador revalidados. |
| `artifacts_bucket_mode` | Crear o reutilizar bucket; inicialmente crear. |
| `artifacts_bucket_name` | Nombre al crear; inicialmente `oci_artifacts`. |
| `existing_artifacts_bucket_name` | Bucket existente resuelto desde OCI. |
| `agent_model_id` | Modelo regional ACTIVE; la preferencia de formulario se resuelve al OCID disponible. |
| `apify_token` | Credencial secreta para instalación y mantenimiento que la requiera. |
| `autonomous_database_mode` | Crear o reutilizar memoria. |
| `autonomous_database_version` | Perfil `26ai:DW`. |
| `existing_autonomous_database_ocid` | Base exacta que debe reutilizarse. |
| `autonomous_database_admin_password` | ADMIN protegido para creación o habilitación AI cuando corresponda. |

Deploy Studio aporta tenancy, región, compartment y sufijo estable. Preflight consulta home region, endpoint del dominio y namespace; no confía en un endpoint arbitrario del navegador. El dominio HTTPS admite host y puerto estándar; rechaza credenciales, rutas adicionales, consultas y fragmentos.

`test_session_id` y `autonomous_database_compute_count` son parámetros avanzados de CLI/Terraform, no controles del formulario. Sin sesión explícita, Resume conserva la registrada. El generador de ADMIN se ofrece sólo al crear una base. Apify se entrega por separado y no utiliza ese generador.

## Secuencia de instalación

1. Fijar proyecto y release/commit en Deploy Studio.
2. Cargar configuración OCI mediante su flujo protegido y elegir región/compartment.
3. Seleccionar AIDP, workspace, administrador, modelo, bucket y memoria. Para recursos existentes, usar sus identidades exactas.
4. Ejecutar preflight y revisar el PLAN y la propiedad antes de apply.
5. Ejecutar post-apply: verificar recursos y memoria, instalar credenciales/compute/archivos/agente y comprobar TEST.
6. Si la operación queda pendiente, usar Resume con la misma operación y selección; no comenzar otra instalación.

Preflight Apify consulta cuenta, actores y builds fijados en `core/actor_profiles.py`, sin iniciar runs. Cuando un perfil tiene `build_id`, exige su identidad exacta; los demás consultan el número fijado en el historial acotado. No sustituye una build ausente por `latest`. La lectura no acredita permiso de ejecución ni suscripción de pago.

## Memoria

El enlace se hace por control-plane con `EnableAiFeatureDetails`; no exige wallet ni acceso SQL. Una asociación READY explícita debe coincidir con la base elegida. Si el GET nativo omite esa asociación, la recuperación requiere el recibo de solicitud aceptada, su work request exacto y verificación de la plataforma; no se infiere a partir de READY solamente.

Habilitar AI necesita autorización administrativa, ETag e intent durable. Una espera o respuesta perdida conserva el registro y exige reconciliación; nunca reemplaza la base para superar el bloqueo. El agente utiliza `AsyncProxyCheckpointClient`, sin crear tablas privadas del proveedor.

## Secretos

Apify utiliza `secret_inputs` y Credential Store; no entra en Terraform, outputs ni código generado. ADMIN se entrega protegido; al crear Autonomous, su variable sensible puede permanecer en el state privado. No publicar state, `.oci`, settings, recibos ni cuerpos de errores del SDK.

El modelo se guarda en el runtime y recibo. La instalación deja investigación pausada: no inicia inferencia ni actores. Activarla requiere el binding completo descrito en [operación](operations.md).

## Acceso y publicación

Los outputs identifican recursos, propiedad, operación, estado y modelo configurado. **Application URL** abre Agent Hub en el host resuelto desde la plataforma firmada, con tenant y dominio codificados para inicio de sesión. Es un enlace de navegación: no publica ni registra el agente.

Para que Market Intelligence pueda atender consultas allí, hay que actualizar y verificar TEST, publicar el endpoint y comprobar AI Registry con el plugin. Después se valida la conversación real. El endpoint de API no es una interfaz web de chat. Véanse [operación](operations.md) y [distribución](distribution.md).
