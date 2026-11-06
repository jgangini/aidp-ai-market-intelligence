# Separación de AI Market Intelligence: diagnóstico y plan

Fecha: 2026-11-05 (America/Lima). Registro de diagnóstico y plan de fase 0. La implementación posterior y sus comprobaciones se mantienen en [estado del candidato](tasks/current.md); las observaciones de esta fase conservan su contexto temporal.

Destino aprobado: paquete Python CLI/hooks y artefactos AIDP, instalados, reanudados y actualizados desde Deploy Studio. Market no tendrá frontend, diálogo propio, login local, servidor API de portal, VM administrativa ni imagen de portal. Arko organiza responsabilidades Python sin imponer FastAPI/React. Solo GodEye conserva su interfaz integrada al visor; no forma parte de esta entrega.

## Alcance y procedencia

Fuente de solo lectura: `D:/dev/codex-oci-ai-data-platform-workbench-starter-kits`, HEAD verificado `3c82617fd298933995e954955841007253cc0985` (v4.0.11 indicada por el coordinador). `git status --short` vacío al inicio. Las referencias relativas de fuente de este documento se resuelven desde esa raíz y ese commit, salvo indicación explícita. No se modifica la fuente.

Destino: `D:/dev/codex-aidp-ai_market-intelligence`. Existe y estaba vacío al inicio: no era repositorio Git, no tenía remoto configurado ni `.oci`. En la comprobación final apareció `.oci` por una acción externa a este diagnóstico; solo se verificó su existencia, sin leer contenido ni acreditar validez. Sigue sin `.git`. `jgangini/aidp-ai_market-intelligence` es el repositorio solicitado, no un remoto observado ni un repositorio GitHub cuya existencia se haya comprobado. No se inicializó Git ni se consultó la cuenta GitHub. No se leyeron credenciales.

Requisitos: encargo del coordinador de separar el módulo conservando identidades, recuperación e invariantes vigentes, con la corrección explícita del usuario de no crear portales. Este archivo complementa el [plan común](../../codex-oci-ai-data-platform-workbench-starter-kits/docs/module-repository-separation.md), releído tras esa corrección el 2026-11-05; ese documento es el contrato de coordinación. El plan común es posterior al corte congelado y no redefine la procedencia de los 95 archivos inventariados. El coordinador conserva Deploy Studio, retirada del monolito y operaciones compartidas. No se inicia una especificación competidora.

Evidencia coordinada: la consulta autenticada `gh api` realizada por el coordinador a los tres repositorios solicitados devolvió HTTP 404. Para este destino significa ausente o sin acceso; no permite afirmar que el repositorio no existe. Este chat no repitió la consulta ni creó un remoto.

Se leyeron `AGENTS.md`, `docs/README.md`, `docs/contracts.md`, `docs/development.md`, `docs/modules/ai-market-intelligence.md`, las skills Arko y AIDP Agent Flow LangGraph y las guías OCI source-intake/workbench-deploy. Estas últimas no se ejecutan: no hay intake `.source` ni workflows Spark del módulo. Arko aporta límites, mapas y contratos; no se ejecuta `init`, no se impone scaffold ni framework. El grafo existente se consultó (`graphify-out/GRAPH_REPORT.md`, `wiki/index.md`): declara base `2adb89a4`, distinta del commit congelado; solo se usó para orientación, con código como autoridad.

## Hechos que condicionan la extracción

1. `apps/backend/app/modules/ai_market_intelligence/aidp/manifest.json` declara `publication_root=/Workspace/.artifact/ai_market_intelligence`, workspace lógico `default`, cero tablas, cero workflows, un agente CODE y un AI Compute. No requiere crear Bronze/Silver/Gold, SQL, jobs ni workflows para completar una plantilla.
2. `package/lab.json:5` conserva paquete `2.0.0`, `status=planned`, `research_enabled=false`, `scheduled=false`, `deployment_surface=TEST`, `semantic_accepted=false`. Los perfiles laboratory/production del paquete no prueban publicación PROD.
3. `client.py:24-27,95-98,132-174` usa recibo versión 2 en `/Workspace/.control/modules/ai_market_intelligence/installer.json`. Operación UUID, propietario, workspace, selección reuse y hashes son parte de su identidad. GET observa; POST avanza una fase. No se puede sustituir este recibo por estado vacío de una nueva aplicación.
4. `client.py:229,285-335,458-466` publica investigación pausada, impide sobrescribir archivos ajenos y rechaza agentes con producción existente. Probe correcto exige revisión, sesión y cero llamadas modelo/capturas. TEST ACTIVE es compatible con Installed Paused: actividad del deployment no habilita investigación ni constituye aceptación semántica/MCP.
5. `client.py:21,176-190` conserva el compute conocido y operación de creación, con recuperación de estados transitorios. El descriptor usa `aidp_ai_market_intelligence_compute`, 1 OCPU, 16 GiB y una réplica; son valores configurados, no sizing medido.
6. `installation/agent_package.py:8-44` valida el conjunto exacto de fuentes revisadas y ensambla un programa legible; configuración no puede contener secretos. La migración debe mantener una sola fuente editable y recalcular hashes/versiones solo cuando cambien los assets del paquete.

## Inventario y dependencias: decisión de traslado

| Fuente | Destino propuesto / decisión |
| --- | --- |
| `apps/backend/app/modules/ai_market_intelligence/{api.py,client.py,__init__.py}` | Extraer cliente y validadores de instalación a paquete Python importable por CLI/hooks. `api.py` es evidencia de validación/autorización a trasladar; no montar sus routers ni trasladar FastAPI al producto. |
| `apps/backend/app/modules/ai_market_intelligence/installation/` | Trasladar sus ocho archivos; ensamblador, credenciales, compute, recursos, playground y streaming son parte del instalador. |
| `apps/backend/app/modules/ai_market_intelligence/aidp/` | Mover a `aidp/` raíz como fuente canónica: manifiesto, workspaces/default/files/agents y agent_flow/{agents,compute}. Ajustar imports Python y resolución local de recursos; conservar rutas Workbench e identidades. |
| `apps/backend/app/modules/ai_market_intelligence/package/lab.json` | Mantener metadatos de paquete y un resolver local explícito de `aidp/`; no duplicar fuentes para satisfacer rutas anteriores. |
| `scripts/modules/ai_market_intelligence/` | Trasladar los cinco scripts Python con imports/rutas actualizados: `market.py`, `market_common.py`, `market_frozen.py`, `market_invoke.py`, `market_pilot.py`. Conservar `market-stream-example.mjs` solo como referencia de consumidor del protocolo, no como UI Market. |
| `apps/backend/tests/modules/ai_market_intelligence/` | Trasladar pruebas de runtime, ensamblado, instalador, credenciales/compute/Playground y pilotos; retener fixtures y expectativas reales. |
| `apps/backend/tests/market_fixture_factory.py`, fixtures usados y `conftest.py` pertinente | Extraer solo soporte realmente importado por estas pruebas; no copiar el harness completo del monolito. |
| `scripts/tests/market-stream.test.mjs` | Conservar check del reductor de referencia para identidad, secuencia y replay; no requiere frontend ni build de aplicación. |
| `apps/frontend/src/App.tsx:795-847,2939-2946,3500` | Evidencia del portal anterior que se retira: no trasladar `GovernanceModuleManager`, tarjeta, diálogo ni React. Configuración, progreso/reanudación y resultados de endpoint pasan a Deploy Studio mediante hooks/outputs. El coordinador integra sus controles genéricos. |
| `apps/frontend/tests/application-settings.test.mjs:1551-1648` y casos Market anteriores; `security.test.mjs` | Referencias de invariantes a cubrir en CLI/hooks y tests del consumidor Deploy Studio: secreto protegido, observación sin instalación, operación/TEST session estables y endpoint validado. No trasladar harness/UI del portal. |
| `docs/modules/ai-market-intelligence.md` y secciones Market de contracts/development/operations | Mantener guía del módulo y contratos locales; reparar enlaces al quedar independientes. No copiar documentación de kits/visor. |

Dependencias cruzadas confirmadas, que impiden copiar únicamente la carpeta del módulo:

- `apps/backend/app/main.py:20-21,245-248,650`: composición, factory y cierre del cliente del portal anterior; `api.py:36-94` depende de `require_admin`, `selected_platform_admin`, `Settings.aidp_ready()` e identity lookup. No trasladar composición HTTP, sesiones ni login. Extraer configuración/ciclo de vida del cliente y conservar validación de administrador mediante el contrato genérico Deploy Studio y preflight, sin confiar en identidad declarada por el navegador.
- `client.py:13-15`, `installation/compute.py:3`, `installation/playground.py:13`: herencia de `app.services.oci.aidp.AidpClient`. Ese padre importa Governance constants, runtime_secrets del visor, kits.naming y catálogo multikit (`aidp.py:18-24`). Extraer el cierre de métodos realmente usado (transporte firmado, workspace/files, permisos, credenciales, agent/compute), con pruebas de equivalencia. No copiar sus miles de líneas de aprovisionamiento de kits ni crear dependencia runtime a repos hermanos.
- `services/oci/aidp.py:225-264,2621-2640`: credencial OCI `AidpRuntime`, signer y métodos con nombre governance tienen uso compartido actual. Separar responsabilidad común sin renombrar la credencial remota ni cambiar identidad. `identity_hash`/`shared_credential` de `modules/ai_gods_eye_view/runtime/runtime_secrets.py` son dependencia concreta a desacoplar, no motivo para instalar el visor.
- `packages/catalog.py:18-45`, `packages/aidp.py`: parser de manifiesto, validación de hashes, `PackageError` y assets. Adaptar al único paquete con pruebas; conservar `lab_id`, `lab.json` y semántica vigente sin aliases legacy ni registro de paquetes ajenos.
- `scripts/modules/ai_market_intelligence/market*.py`: enlazan CLI/plugin AIDP instalado, configuración privada y helpers de sesión. Inventariar esas importaciones antes de mover; el plugin no debe vendorearse ni instalarse implícitamente.
- `.github/workflows/ci.yml:74-84`: perfil runtime Python 3.11, LangGraph 0.6.7, OCI 2.189.0, pytest 8.4.1, pypdf 6.20.1, jsonschema 4.26.0. El perfil del portal Python 3.12/Node 22 es evidencia del origen, no un requisito de frontend para Market. `requirements.txt` del agente solo añade httpx 0.28.1 y pypdf 6.20.1; no declara todo lo gestionado nativamente. `runtime_versions.py` compara Python y nueve distribuciones exactas, incluyendo MCP. Compatibilidad nativa sigue desconocida sin observaciones del destino.

## Contratos del portal anterior trasladados a CLI/hooks y Deploy Studio

Los tres endpoints siguientes son evidencia del origen y se retiran de Market independiente; no se conservan como rutas ni aliases. Sus invariantes se trasladan a comandos/hooks con contrato de resultados definido y probado en fase 1:

- `GET /api/admin/modules/ai_market_intelligence`: estado observado y campos públicos seguros; nunca inicia instalación.
- `POST /api/admin/users/{user_id}/modules/ai_market_intelligence`: operación estable, `reuse_existing`, `apify_token` secreto opcional, `test_session_id` opcional. Payload estricto y tamaño máximo 8192; errores no reflejan el token. 202 si installing, 200 para estado resultante; 400/409/503 conservan significado.
- `GET /api/admin/modules/ai_market_intelligence/agent-endpoint`: endpoint TEST exacto verificado; no acepta selectores en query.

Deploy Studio recibe configuración de destino, administrador elegible y token secreto; invoca instalación/reanudación y muestra progreso, estado Installed Paused y resultados/endpoint verificados. Los hooks separan observación sin efectos de avance explícito de operación. Los códigos HTTP 202/200/400/409/503 anteriores son evidencia; la fase 1 define su traducción a resultados/errores del runner, sin simular un servidor. El protocolo nativo NDJSON `ai-market-intelligence` v2.0 conserva identidad/revisión/secuencia y replay; su reductor es referencia de consumidor, no un chat propio. No activar investigación desde instalación u observación de estado.

Estructura propuesta: `src/ai_market_intelligence/` como paquete Python para dominio/instalación/adaptadores, `aidp/manifest.json`, `aidp/workspaces/default/files/agents/ai_market_intelligence/`, `aidp/workspaces/default/agent_flow/{agents,compute}/`, metadatos `package/lab.json`, `terraform/deploy-studio.json`, `terraform/hooks/`, scripts Python de desarrollo/validación, `tests/` y `docs/`. Sin `apps/frontend`, servidor de portal ni carpetas de tablas/workflows ficticios. Rutas Python internas se actualizan una vez con pruebas, sin dos layouts editables.

## Infraestructura new / existing: propuesta, no implementada

| Modalidad | Contrato que debe cerrarse con Deploy Studio |
| --- | --- |
| new | Paquete/artefactos inmutables y hooks ejecutados por Deploy Studio con identidad subida del operador, transporte firmado y workspace/modelo/artifacts seleccionados. Terraform/hooks reproducen solo recursos AIDP/OCI requeridos, sin VM, API/UI ni imagen de portal. Declarar plataforma/workspace y su propiedad; no heredar Terraform completo con kits/visor. |
| existing | Seleccionar plataforma/workspace/identidad exactos y verificar `/roles` con esa identidad, acceso a artifacts, modelo y credenciales. Reanudar únicamente recursos del recibo coincidente. Un recurso por nombre sin recibo no es una instalación válida ni permite adoptar estado. |

Los hooks y `terraform/deploy-studio.json` schema v1 requieren diseño con el coordinador; todavía no existe descriptor independiente. La fuente solo permite `gods_eye_view` en `enabled_vm_modules`: añadir Market a esa lista no implementa un despliegue independiente. No aplicar planes ni crear stack/VM como fallback.

Verificación adicional: `terraform/j_oci_ai_data_platform.tf:35-58` declara siempre `oci_ai_data_platform_ai_data_platform.lab`, sin selector existing; sus locales también usan directamente ese recurso. AIDP new/existing es capacidad nueva, no una opción ya implementada que baste exponer en formulario. No añadir recursos externos al estado administrado ni aplicar cambios al stack original para convertir su propiedad.

Consumidor real leído en solo lectura: `D:/dev/codex-cloudtechnext/app/backend/app/services/deployment_source.py`. `ManifestField:107-169`, `PreflightContract:383-406`, `PostApplyContract:350-380` y `DeploymentManifest:409` usan `extra="forbid"`. El formulario admite select con opciones estáticas, text con validación y visibilidad condicional; `options_source` solo contempla modelos, schemas Autonomous, Vaults y buckets, sin AIDP. Propuesta compatible para la primera versión: select estático new/existing y campos OCID text condicionales, validados por preflight; no inventar `options_source` AIDP ni capacidades/atributos extra. Descubrimiento AIDP posterior exige cambio genérico del consumidor y sus pruebas, propiedad del coordinador.

El token Apify será input secreto de despliegue mediante `type=password`, `secret=true`, `transform=none` y sin default; el runtime necesita el token real, no su hash. Declararlo por nombre en `preflight.secret_inputs` y `post_apply.secret_inputs`, contratos ya soportados. Estos valores describen campos existentes del parser; el nombre definitivo del campo y del project_id se congelará en la fase de contratos con fixture aceptado por el parser real. La ruta protegida termina en Credential Store. Debe quedar fuera de Terraform state/plan, user-data, outputs, imagen, código generado, logs, correo y almacenamiento/respuestas persistentes del navegador. Definir entrega de una sola vez y eliminación verificable del sobre temporal. Reutilizar credencial existente por identidad sin leer/exportar su valor; sin mecanismo verificable para validarla con Apify, indicar acceso no comprobado y solicitar un binding protegido, sin rotación automática.

El selector genérico de administrador pertenece al coordinador en CloudTechNext: ID estable, usuario activo, dominio/ámbito correctos y roles efectivos, revalidados server-side antes de instalar y al Resume. Cambiar tenancy/dominio/plataforma invalida la selección. En plataforma nueva se selecciona identidad elegible del dominio y se verifican las asignaciones autorizadas después del bootstrap, antes de publicar el agente; no consultar roles de una plataforma inexistente. Los hooks consumen el contrato genérico verificado, sin inventar un `options_source` ni aceptar un OCID manipulado como autorización. Seleccionar administrador no suplanta al firmante: se usa la identidad autorizada del operador, sin login local ni provisioner alterno. Si faltan permisos, preflight informa el bloqueo antes de mutar.

## Propiedad, redeploy y rollback

- Una sola autoridad escritora por instalación; los locks actuales son de proceso, no garantía distribuida entre portal antiguo y ejecuciones CLI/hooks de Deploy Studio (`services/oci/aidp.py:258-263`). La transferencia exige impedir escrituras concurrentes antes del cambio de dueño. No iniciar ambos instaladores sobre el mismo workspace.
- Mantener recibo v2, operation_id, owner, workspace, claves compute/agente/credenciales, intenciones, test sessions, hashes del paquete y memoria/capturas/gasto. No exportar esos valores privados a esta documentación ni llevar `.local`, `.aidp-local`, `.oci`, TF state o recibos reales a Git.
- Traslado de fuentes no implica compatibilidad automática de candidatos: `client.py:234-258` exige readback exacto del TEST ya enviado; `client.py:292-293` rechaza producción existente. El futuro redeploy necesita protocolo explícito de update y evidencia de ownership; no está implementado por este diagnóstico.
- Rollback: conservar paquete/fuentes AIDP previos verificados, hashes y referencias privadas de instalación; recuperar los artefactos del candidato propio solo bajo un protocolo de update autorizado y después de excluir escrituras concurrentes. No hay imagen/routing de portal Market que revertir. La instalación combinada previa permanece disponible hasta el corte coordinado. Nunca destruir primero agente/compute/credencial ni resetear investigación para volver a instalar.
- Separar CLI/hooks/paquete/infra versionados del despliegue nativo del agente. La release nueva no habilita investigación ni convierte TEST a PROD. El coordinador debe decidir traspaso de propiedad antes de retirar el monolito.
- El recibo genérico de despliegue previsto por el plan común (stack, commit/digests, recursos owned/external y exclusión durable) es responsabilidad nueva de infraestructura. Complementa el recibo de instalación Market v2; no lo sustituye ni cambia su formato por inferencia. El contrato integrado debe vincular ambos sin secretos y conservar una sola autoridad de escritura. Un AIDP externo nunca se destruye desde el stack del producto.

## Plan de ejecución posterior y criterios verificables

1. Cerrar el plan común, propiedad y modalidad new/existing. Verificar repo remoto antes de inicializar/publicar; mantener este destino sin secretos. Bootstrap local solo al existir un Git persistente y antes de implementación no trivial.
2. Preparar wrappers preflight/postflight mínimos y una baseline única del nuevo repo. Definir reglas Sentrux desde su contrato real; no copiar resultados ni baseline del monolito. Actualizar Graphify solo si ya existe; no inicializarlo como efecto lateral.
3. Extraer runtime/AIDP, adaptadores mínimos, pruebas y documentación desde el commit congelado. Aceptación: todo asset del manifiesto existe, hash correcto, cero tablas/workflows, ensamblado determinista con una sola fuente y sin secretos; sin imports a Governance/visor/kits ni dependencias runtime a repos hermanos.
4. Extraer lógica a CLI/hooks y trasladar autorización al contrato genérico Deploy Studio. Aceptación: observación cero mutaciones, avance explícito con admin elegible revalidado/UUID estable, validación estricta/redacción, conflicto accionable, reintento con misma identidad, fuente pausada, compute conservado ante timeout/estado transitorio. Sin frontend, login local, servidor HTTP permanente, imagen de portal ni VM administrativa; sin instalación local simulada. Probar selección manipulada/obsoleta, usuario inactivo/dominio ajeno/sin roles y revocación al Resume; en plataforma nueva comprobar roles tras bootstrap.
5. Implementar preflight Apify acotado descrito más abajo, con transporte simulado que falle si se inicia un run. Aceptación: casos identidad inválida, cada actor, permisos desconocidos, HTTP 401/403/404/429, timeout/redirección y secretos ausentes del resultado/log. Ninguna aprobación falsa de capacidad de ejecución.
6. Ejecutar workflow único: pruebas Python de CLI/hooks/instalador/runtime/factories, node test del reductor de referencia y, cuando existan, validación Terraform con mocks. El coordinador valida selector/runner genéricos en CloudTechNext; Market prueba el contrato recibido y su revalidación. Postflight con gate/reglas Sentrux; no declarar completado con checks requeridos ausentes. Sin build frontend ni Docker de portal. Dependencias y operaciones nativas se reservan para la fase autorizada posterior.
7. Solo con autorización de fase cloud y `.oci` local protegido: comprobar roles/identidades, transporte/modelo/MCP, revisión nativa y TEST con investigación pausada. Setup exige cero inferencia y cero Apify starts. Aceptación de investigación pagada requiere autorización/campaña separada y evidencia del candidato; permanece pendiente.

Correspondencia con fases comunes: este assessment cierra únicamente el inventario local de fase 0. La fase 1 debe fijar inputs/outputs, administrador verificado, propiedad, identidad y release con el coordinador; la extracción (fase 2) y refactor (fase 3) irán en cambios separados para comparar comportamiento. Las fases 4/5 requieren paquete Python/artefactos AIDP y digests, manifiesto inmutable y validación de parser/runner antes de catalogar; no requieren imagen de portal. Retirada y transición de instalaciones (fases 6/7) pertenecen al coordinador. La pequeña base OCI mínima puede repetirse con procedencia explícita por producto; no se crea un framework compartido ni dependencia al repositorio general.

## Apify: auditoría y preflight propuesto sin runs

Sea `R=apps/backend/app/modules/ai_market_intelligence/aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/`.

Evidencia implementada en el origen: el input está en `api.py:14-29,58-76`, UI password `apps/frontend/src/App.tsx:1084` y envío `:974-988`; son puntos del portal que se retiran y cuyas validaciones pasan a Deploy Studio/CLI/hooks. No existe input Apify en los archivos inspeccionados `terraform/deploy-studio.json`, `terraform/b_variables.tf`, `terraform/hooks/post_apply.py`. `installation/credentials.py:8,48-71` guarda tipo `SECRET_TOKEN`, nombre `AiMarketIntelligenceApifyBearer`, campo `token`, verificando identidad/ownership/estado ACTIVE sin rotación automática. Eso no prueba el token ante Apify. `R/services.py:34-41` lo obtiene de `aidputils.secrets.get` y lo vincula al MCP con bearer. `R/native.py:112-121` y `R/provider.py:148-153` solo comprueban herramientas `call-actor`, `get-actor-run`, `get-dataset-items`. No comprueban identidad de cuenta, acceso a actores ni entitlement. El probe pausado tampoco ejecuta `check_services`, reservado al camino de investigación habilitada.

Registro canónico: `R/actor_profiles.py:3-38`. Este inventario es evidencia del commit, no configuración duplicada para mantener:

| Perfil | Actor ID | Build fijado |
| --- | --- | --- |
| facebook_search | Us34x9p7VgjCz99H6 | 0.0.343 |
| instagram_search | DrF9mzPPEuVizVF4l | 0.1.561 |
| facebook_posts | KoJrdxJCTtpon81KY | 0.0.408 |
| instagram_posts | nH2AHrwxeTRJoN5hX | 0.0.650 |
| x_search | nfp1fpt5gUlBwPcor | 0.0.1053 |
| website | aYG0l9s7dbB7j3gbS | 0.3.102 |
| web_search | 3ox4R101TgZz67sLr | 1.0.35 |
| web_search_localized | yD95d01y3SlKrPqQ7 | 0.0.21 |
| web_search_serp | 563JCPLOqM1kMmbbP | 0.0.92 |
| youtube_search | h7sDV53CddomktSi5 | 0.0.322 |
| yahoo_finance (suspendido) | lujI4mrby2M9OV868 | 1.1.1 |
| yahoo_history | fDacJgPkfZVoNFLCq | 0.0.27 |
| world_bank | h9FnVs8QXP9lBK6Gp | 1.0.6 |

El actor yahoo_finance permanece suspendido por rechazo MCP (`actor_profiles.py:32-34`); no habilitarlo silenciosamente ni bloquear todos los demás por esa capacidad desactivada. Obtener siempre el conjunto requerido y builds del registro vigente, incluyendo los límites/categorías de gasto existentes; sin fallback a latest u otro actor.

Preflight propuesto, pendiente de implementación:

1. Input de despliegue secreto con entrega protegida a la credencial actual; bearer en header, nunca en URL. Verificar cuenta mediante el endpoint documentado [GET /v2/users/me](https://docs.apify.com/api/v2/users-me-get). Su respuesta puede incluir datos privados y contraseñas de proxy: seleccionar únicamente identidad/estado necesarios y descartar el resto sin registrar el body.
2. Consultar metadata de cada actor requerido con el token y comprobar el build fijado mediante contrato oficial verificado. [Get Actor](https://docs.apify.com/api/v2/actor-get) documenta consulta de actor, no una autorización para ejecutar. La forma concreta para consultar builds y restricciones deberá verificarse antes de programar; no se ensayaron endpoints ni llamadas autenticadas en esta fase.
3. Separar resultados: autenticación de cuenta, visibilidad de actor, disponibilidad del build y permiso de ejecución. Un 200 de metadata solo prueba esa lectura. Permiso de ejecución queda `not_verified` salvo que una API documentada sin ejecución lo pruebe para la identidad y actor exactos. No declarar "Apify listo" a partir de metadata ni del inventario MCP.
4. Errores accionables sanitizados: 401 revisar token inválido/revocado; 403 revisar scopes, acceso o restricciones; 404 actor/build ausente o no visible para esa identidad (sin afirmar borrado); restricción explícita de alquiler/suscripción/permiso exige corregir el acceso. 429/timeout/5xx son indeterminados con reintento acotado; no disparan capturas. No exponer respuesta cruda ni token.
5. Si la credencial existente no permite una comprobación segura desde el instalador, devolver estado no comprobado y conservarla; el diseño debe resolver binding protegido, sin pedir exportarla a logs ni sobrescribirla. Solo preflight de lectura no cambia `research_enabled`, aceptación semántica ni TEST/PROD.

Revalidar la cuenta y la matriz de actores cuando cambien token/actores y antes del primer trabajo; un resultado del despliegue no acredita permisos futuros. Seguir distinguiendo límites configurados, alquiler/entitlement, build disponible y ejecución no comprobada. No convertir esta revalidación en un smoke pagado implícito.

El proveedor actual sanea diagnóstico y reconoce fallos de build/input (`R/provider.py:27-38,53-93`), pero no ofrece clasificación específica 401/403/404/restricciones ni `/users/me`. Conservar estas defensas y ampliar la taxonomía sin interpretar texto ambiguo como permiso.

Regresión pendiente: extender `test_market_installation_credentials.py:25,35,43,50`, `test_market_current_installer.py:31,61,120,157,229,315,341` y pruebas de providers. Mockear identidad/actor/build y cubrir 200 con ejecución no comprobada, 401, 403, 404 ambiguo, restricción, 429, timeout, actor suspendido y redacción. Transporte de prueba debe rechazar cualquier inicio de run (`call-actor`, POST de runs y variantes sync, incluso GET con ejecución). Verificar cero starts e idempotencia después del fallo.

No se validó ningún token, actor, build ni entitlement en vivo. Las páginas oficiales se consultaron como documentación pública el 2026-11-05; la conectividad del instalador y su identidad siguen sin comprobarse.

## Validación realizada en esta fase

- Preflight: no ejecutado; cambio exclusivamente documental, destino sin Git ni scripts. No se fabricaron wrappers para validar un informe.
- Tests: suites de aplicación no ejecutadas; revisión estática de fuente y referencias. Comprobación documental con PowerShell: 95 archivos versionados existen, bloques Markdown balanceados, lectura UTF-8 y criterios principales presentes. El intento inicial con `python` no se ejecutó por alias Microsoft Store sin intérprete disponible en PATH; se usó PowerShell nativo, sin instalar nada. No se ejecutaron scripts de investigación ni llamadas autenticadas.
- Graphify: reporte/index leídos, grafo desactualizado respecto al commit congelado; no actualizado porque fuente de solo lectura y sin cambios de código.
- Sentrux: no ejecutado; no hay reglas en el destino vacío y esta fase no cambia código/configuración/arquitectura ejecutable.
- Codex: GPT-6.1 Sol High -- extracción con contratos y recuperación entre servicios. [Referencia](https://learn.chatgpt.com/docs/models)
- Notas: sin extracción, Git init, publicación, Docker, puertos, inferencia, actors, stacks ni cloud. Plan común corregido releído; parser real Deploy Studio y AIDP incondicional verificados anteriormente; 404 remoto se registra como evidencia del coordinador. Tras retirar las propuestas de portal se verificaron con PowerShell requisitos positivos, ausencia de formulaciones incompatibles, UTF-8, bloques Markdown y enlace local al plan común. Solo se editó este assessment. Plan new/existing, fixture de manifiesto, administrador genérico y entrega del secreto pendientes de fase 1. Las pruebas nativas no se sustituyen por revisión estática.

## Discrepancias y decisiones pendientes tras leer el plan común

La propuesta anterior de FastAPI/React propios era incompatible con la corrección del usuario y fue reemplazada en inventario, estructura, contratos, infraestructura, rollback y validación. El destino ahora es exclusivamente paquete Python CLI/hooks y artefactos AIDP administrados desde Deploy Studio, sin VM ni imagen de portal. No quedan discrepancias de alcance con el plan común corregido. Se mantienen las brechas AIDP existing y preflight Apify, y el uso de `secret_inputs` sin ampliar arbitrariamente schema 1. Quedan por fijar project_id, nombres de inputs/outputs, assets/release, traducción de resultados al runner y contrato genérico del administrador, propiedad del coordinador. El recibo infra owned/external y la exclusión durable también necesitan diseño integrado; el recibo Market v2 y los locks de proceso actuales no los implementan. Estos son gaps explícitos del plan, no funciones acreditadas.

Delegación acotada: un explorador revisó Apify en solo lectura, independiente del inventario principal. Aportó inventario exacto de 13 perfiles y brechas de autenticación/entitlement; se integraron sus referencias y propuesta sin ejecutar llamadas. No hubo ediciones concurrentes ni rework de integración. No se midió tiempo individual ni hay contabilidad de tokens disponible; no se afirma ahorro comparativo. No se repitieron baseline/postflight ni se compartieron mutaciones.


## Anexo: inventario exacto desde el árbol Git congelado

Archivos versionados, sin caches ni estado privado. Aplican las decisiones de traslado/adaptación anteriores.

### Módulo y fuentes AIDP (51 archivos)

Raíz fuente: `apps/backend/app/modules/ai_market_intelligence/`.
```text
__init__.py
aidp/manifest.json
aidp/workspaces/default/agent_flow/agents/ai_market_intelligence.json
aidp/workspaces/default/agent_flow/compute/ai_market_intelligence.json
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/__init__.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/actor_profiles.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/actor_tools.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/artifacts.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/collector.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/config.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/connectors.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/contracts.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/documents.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/effects.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/factory.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/finance.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/financial.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/mcp_payload.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/mcp_tools.json
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/media.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/memory.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/model.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/model_bridge.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/native.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/passages.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/prompts.json
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/provider.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/research_contracts.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/runtime_core.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/runtime_versions.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/serp.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/services.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/session.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/social.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/social_search.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/state.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/wire.py
aidp/workspaces/default/files/agents/ai_market_intelligence/ai_market_intelligence/workflow.py
aidp/workspaces/default/files/agents/ai_market_intelligence/market_intelligence.py
aidp/workspaces/default/files/agents/ai_market_intelligence/requirements.txt
api.py
client.py
installation/__init__.py
installation/agent_package.py
installation/agent_source.py
installation/compute.py
installation/credentials.py
installation/playground.py
installation/resources.py
installation/stream.py
package/lab.json
```

### Pruebas del módulo (38 archivos)

Raíz fuente: `apps/backend/tests/modules/ai_market_intelligence/`.
```text
__init__.py
test_market_current_installer.py
test_market_installation_compute.py
test_market_installation_credentials.py
test_market_installation_playground.py
test_market_installer.py
test_market_intelligence_actor_tools.py
test_market_intelligence_connectors.py
test_market_intelligence_finance.py
test_market_intelligence_graph.py
test_market_intelligence_localized_search.py
test_market_intelligence_media.py
test_market_intelligence_payload.py
test_market_intelligence_research_acquisition.py
test_market_intelligence_research_boundaries.py
test_market_intelligence_research_collector.py
test_market_intelligence_research_documents.py
test_market_intelligence_research_financial.py
test_market_intelligence_research_followup.py
test_market_intelligence_research_gateway.py
test_market_intelligence_research_invoke.py
test_market_intelligence_research_lifecycle.py
test_market_intelligence_research_page_budget.py
test_market_intelligence_research_passages.py
test_market_intelligence_research_progressive_reads.py
test_market_intelligence_research_refinement.py
test_market_intelligence_research_runtime.py
test_market_intelligence_research_serp_error.py
test_market_intelligence_research_units.py
test_market_intelligence_social_search.py
test_market_intelligence_status_unavailable.py
test_market_intelligence_web_search_provider.py
test_market_intelligence_website_provider.py
test_market_intelligence_youtube_provider.py
test_market_package.py
test_market_research_fixture_factory.py
test_market_research_flow_factory.py
test_market_research_pilot.py
```

### Scripts del módulo (6 archivos)

Raíz fuente: `scripts/modules/ai_market_intelligence/`.
```text
market-stream-example.mjs
market.py
market_common.py
market_frozen.py
market_invoke.py
market_pilot.py
```
