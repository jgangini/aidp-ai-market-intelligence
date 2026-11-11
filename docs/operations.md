# Operación y recuperación

## Texto incremental público

El protocolo 2.0 utiliza sobres `event`, `response`, `snapshot` y `heartbeat`, y `kind=text` para prosa provisional. Los consumidores que validan una lista cerrada de tipos deben incorporar esta extensión antes de usar el candidato; compartir versión no vuelve compatibles a lectores antiguos que rechazan tipos desconocidos. El envelope se serializa comenzando por `{"protocol"` y termina con newline.

```json
{"protocol":"ai-market-intelligence","version":"2.0","kind":"text","request_id":"request-1","turn_id":"request-1","stream_id":"0123456789abcdef0123456789abcdef","block_id":"source-web","block_revision":1,"source_key":"web","source_keys":["web"],"phase":"delta","delta_index":1,"text":"La muestra ","status":"provisional"}
```

Todos esos campos son obligatorios. `stream_id` es un hex de 32 caracteres, distinto por generación; `turn_id` coincide con la solicitud. Para borradores, `block_id` es `source-<fuente>` y `source_keys` contiene esa fuente. La síntesis usa `consolidated`, `source_key=null` y sus fuentes. `block_revision` anuncia la siguiente revisión tentativa del bloque canónico, no una revisión ya aprobada.

El ciclo es `start` con índice 0, fragmentos `delta` con índices consecutivos y `end`; una revisión posterior puede emitir `discard` con el siguiente índice, incluso después de `end`. Sólo `delta` lleva texto y siempre es el fragmento nuevo, nunca todo el prefijo. `end` cierra la generación, no la verificación. Un nuevo `start` del mismo bloque sustituye el provisional anterior. Los eventos durables `draft.upsert`, `draft.corrected` o `draft.compacted` con revisión igual o superior sustituyen ese provisional; `response` es canónica. Los fragmentos no se incorporan a hallazgos verificados.

Cuando la entrega se completa, `run.completed` se publica antes de retirar los provisionales
restantes. Su texto canónico permite reemplazarlos sin un intervalo vacío, incluso en una
transformación de informe que no emite `draft.compacted`; después llega `response` con sus
referencias y metadatos completos y retira los overlays del cliente. Las generaciones locales
se cierran sin enviar un `discard` de éxito redundante después del commit. Un resumen rechazado se descarta inmediatamente al
resolver su revisión. Los fallos y cancelaciones conservan la retirada previa al marcador
terminal, sin presentar texto rechazado como respuesta aceptada.

Deduplicar por `stream_id` e índice, rechazando un payload diferente para la misma identidad. Mantener el cursor durable independiente; rechazar cruces de solicitud y cambios de atribución/revisión dentro de un stream. Ignorar mensajes tardíos de streams retirados. El snapshot/replay y la reconexión limpian provisionales; los tokens no se guardan ni se regeneran por replay.

Cada solicitud admite hasta 1.024 paquetes transitorios, incluidos controles; cada delta hasta 2.048 caracteres Unicode y cada generación hasta 20.000. Se reserva espacio para cierre y retirada. Al agotarse el cupo se retira el provisional afectado y se completa la generación sin emitir más fragmentos, manteniendo las publicaciones canónicas. Permanecen los límites de 32 MiB por flujo, 8 MiB por frame SSE, 4.096 frames y 2 MiB por paquete público.

Renderizar el texto provisional como contenido no confiable con etiqueta de revisión pendiente; nunca ejecutar HTML o código ni interpretarlo como instrucciones de herramientas. El adaptador excluye JSON de control, otros campos y razonamiento; el contrato de prosa rechaza HTML y fences de código. Ante desconexión, recuperar con la misma sesión/request y revisar el ledger antes de repetir efectos. Una animación sobre un bloque completo no cuenta como streaming del modelo.

## Presentación explícita en eventos

Los eventos nuevos declaran `coverage.presentation` con `schema_version="1.0"` y
`purpose`: `progress` para `source.coverage`, `result` para `draft.*`,
`clarification` para `clarification.required`, `answer` para `run.completed`,
`artifact` para `artifact.upsert` y `terminal` para fallo o cancelación. La presencia
de esa versión es la señal de capacidad; no se deduce de frases, fuentes ni IDs de
bloques. No añade claves al sobre ni a `kind=text`, ni cambia el protocolo 2.0.
Los eventos históricos sin esta capacidad conservan su interpretación anterior.
Un consumidor que cierre las claves internas de `coverage` debe actualizar su
validador antes de recibirla; compartir versión no demuestra compatibilidad.

En recopilación, `presentation.activity` contiene exactamente `activity_id`,
`phase`, `state` y `outcome`. Su identidad SHA-256 depende de ejecución, tarea y
operación; se conserva al continuar o reutilizar esa captura y no contiene la
pregunta ni datos del proveedor. Se mantienen `coverage.source`, `phase`,
`operation_key`, `group_key`, `collection_status` y `evidence_count` cuando aplican.
Los mensajes son deterministas y se muestran tal como se reciben.

| Fase | Estado | Resultado |
|---|---|---|
| `retrieving` | `running` | `null` |
| `pending` | `pending` | `pending` |
| `retrieved` o `reused` | `completed` | `available`, `empty`, `insufficient`, `unavailable` o `query_rejected` |
| `failed` | `failed` | `failed` |

`available` acredita evidencia recuperada, todavía sin aceptación factual.
`empty` sólo se emite cuando una captura normalizada confirma cero resultados sin
limitaciones ni señales de recuperación incompleta; no acredita ausencia en la
plataforma. `insufficient` cubre evidencia descartada o una captura cuyo vacío no
se puede establecer. `unavailable` declara recuperación no disponible;
`query_rejected` conserva que no se ejecutó otra consulta. `failed` cierra una
recuperación que lanzó una excepción, sin publicar su mensaje privado ni repetirla.
`completed` termina la actividad de recopilación, no la investigación.

El snapshot contiene `activity`, la última actividad por fuente, y
`activities`, un mapa por `activity_id` que conserva las últimas observaciones de
actividades distintas de la misma fuente. Sus valores son eventos originales;
no se reescriben al terminar la investigación. El terminal del turno cierra la UI
de actividades pendientes sin convertirlas en exitosas. `partial` sigue siendo
parcial: el propósito `answer` no acredita calidad completa. `run.completed.text`
continúa siendo texto canónico anticipado y `response` aporta el cierre completo.
Los deltas de fuente y de síntesis siguen siendo provisionales, con revisión y
`discard`; `end` no significa aceptación. `text` conserva su conjunto exacto de
campos y el adaptador nativo de texto permanece sin cambios.

La fixture compartida `tests/fixtures/presentation-v1.ndjson` incluye dos actividades
de Facebook, deltas `start/delta/end/discard`, corrección de bloque, respuesta parcial
y snapshot. Los validadores de productor rechazan propósito contradictorio,
actividad mal formada e inventarios de otro turno. La aceptación de Desk y la
prueba nativa del candidato se realizan aparte; estas fixtures no las sustituyen.

## Colección opcional de contenido

La capacidad `content_items` versión `1.0` es independiente de los bloques de fuente y del
texto incremental. Se entrega una sola vez por sobre, al mismo nivel que `response` o
`replay`, junto con el manifiesto de todas las evidencias de sus fichas y comentarios.
Los contratos de preguntas y `kind=text` no cambian. Un cliente sin soporte puede seguir
mostrando las fuentes y la respuesta final.

Cada colección tiene `revision` e `items`; cada ficha tiene `item_id`, `item_revision`,
`source_key`, `kind`, `primary_evidence_id`, `evidence_ids`, `text` Markdown, `content_basis`
y `limitations`. `comments_analysis` opcional conserva `parent_item_id`, estado, texto,
evidencias, cantidad de comentarios distintos analizados y limitaciones. No proporciona
sentimiento poblacional, total de comentarios ni autores ausentes. `not_collected` significa
que no hay comentarios admitidos, no que se haya probado su inexistencia; `unavailable`
también puede indicar que la síntesis individual no superó la revisión.

El máximo es 400 fichas y 1 MiB UTF-8 por colección. Se conservan hasta 50 evidencias por
síntesis, textos de hasta 3.000 caracteres y 12 limitaciones. Los IDs de evidencia deben
resolver en el manifiesto del mismo turno; el análisis de comentarios cita comentarios
vinculados al padre observado de esa publicación. La fixture compartida es
`tests/fixtures/content-items-v1.json`.
Revisiones menores no sustituyen estado; una misma revisión no puede cambiar el payload.
Un ítem retirado conserva su piso de revisión durante la solicitud. El replay restaura
la colección sin regenerar texto ni repetir capturas.

En un stream vivo la colección completa se envía al cambiar su revisión, sin repetirla
en cada evento ni en los fragmentos de texto. Respuesta y snapshot la conservan completa.
El evento conserva su identidad inmutable; las revisiones de la colección son independientes.
Los clientes no deben usar el hash del sobre entero como identidad del evento: un manifiesto
puede ampliarse con evidencias de una colección posterior. Deben validar ambas capacidades
por separado y rechazar cambios en el evento o en una referencia ya conocida. Continúa
vigente el límite de 2 MiB por sobre, que incluye colección, manifiesto y demás contenido.

## Configuración e identidad

La espera de un actor usa por defecto su `callOptions.timeout` más 15 segundos de margen,
con un máximo de 240 segundos y sondeo cada cinco segundos. Los intervalos automáticos
admiten de uno a diez segundos; un override explícito de una a 24 lecturas conserva
los controles rápidos de fixtures y recuperación. Así, actores configurados con 120 o
180 segundos disponen de 135 o 195 segundos de observación, sin iniciar otra captura.
Las lecturas con estado temporalmente no disponible o contabilidad todavía no verificada
se reintentan sobre el mismo `runId` dentro de esa ventana, sin iniciar otro actor.
El efecto conserva un diagnóstico privado de etapa, clase de error, intento y código HTTP
si está disponible, sin mensajes ni contenido del proveedor; el diagnóstico se mantiene
también al recuperar el resultado. Los errores desconocidos de lectura se propagan y no
se convierten silenciosamente en una fuente pendiente. Un inicio incierto sin recibo
continúa pendiente, sin reenvío automático.
El prefijo raíz exacto `MCP tool 'get-actor-run' invocation failed: ` se clasifica
como lectura temporalmente no disponible del SDK. Otros textos no reciben esa tolerancia.
Las validaciones de recibo y envoltorio guardan códigos de causa, tipo y forma de respuesta,
conteos de registros/coincidencias y estado reconocido cuando corresponde; no guardan IDs
ajenos ni valores de estados desconocidos. Un recibo de otra ejecución, metadatos
contradictorios o un formato desconocido siguen siendo errores fatales de esa lectura.
Si se agota la ventana o sigue sin poder verificarse el recibo, se conserva `pending` y el mismo
`runId`; no se anuncia una respuesta final. Los heartbeats continúan durante las esperas
asíncronas. Una respuesta terminal y conciliada libera la sesión; una operación incierta
conserva su reserva hasta recuperarla o cancelarla explícitamente.

Cada turno conserva el `request_id` enviado por el cliente. Si la sesión tiene otra solicitud
pendiente, incluso con la misma pregunta, el agente devuelve una respuesta `pending` bajo
el identificador del turno nuevo, con `pending_request_id` y `resumable: true`; no lo
sustituye silenciosamente por el identificador anterior ni inicia modelos o capturas.
Para continuar la ejecución se reutilizan la sesión, el identificador y la pregunta originales;
para leer lo guardado se usa `{"action":"replay","request_id":"<pending_request_id>"}`.
Una respuesta `pending` de investigación incluye `continuation_safe: true` únicamente
cuando todos los efectos pendientes son capturas Apify con recibo conocido y lectura
reintentable; indica que el cliente puede continuar la misma solicitud de forma acotada.
Un conflicto con otra solicitud o un efecto de modelo, inicio incierto o error desconocido
no habilita esa continuación automática. `resumable` por sí solo no la autoriza.
El cliente conserva sesión, pregunta, candidato e identidad originales y respeta la
cancelación. `replay` solo lee; no existe un comando operacional `resume`.
El cliente mantiene la validación de identidad de cada flujo. Una vez terminada la solicitud,
una pregunta igual con un identificador nuevo puede iniciar una investigación independiente.

Deploy Studio administra Terraform y llama a los hooks del paquete seleccionado. El formulario instala o reanuda; la CLI administra una instalación existente. El operador firma las solicitudes OCI. `admin_user_ocid` identifica al propietario autorizado y no sustituye la identidad firmante.

Deploy Studio normaliza `Services LLM` y `Select Model` antes de Terraform. OCI IAM usa `agent_model_id`; OpenAI transmite `openai_model_id` como `agent_model_id`, con `llm_auth_mode=openai`. Los outputs y el hook comprueban que esa selección no cambió. La clave se guarda en Credential Store y el binding del agente contiene únicamente su referencia. Un modelo del catálogo requiere una prueba funcional de salida estructurada y streaming antes de aceptarse.

La CLI conserva el proveedor y modelo instalados durante un `update` de código, aunque los settings del operador estén desactualizados. Para cambiar esa selección se usa `update --change-model --apply` con settings actualizados. El formulario `update` de Deploy Studio aplica explícitamente sus campos. `redeploy` exige la misma selección instalada; `rollback` restaura la configuración respaldada. Una operación pendiente reanuda su objetivo congelado sin adoptar settings modificados después.

El harness local compara proveedor, modelo, endpoint, región, compartimento y referencia de credencial del binding con el `core/runtime.json` del candidato y su inventario verificado. También contrasta TOML y configuración del ejecutor antes de crear el adaptador. Rechaza diferencias o bindings incompletos; no cambia automáticamente de OpenAI a OCI IAM. OpenAI local requiere `MARKET_LLM_API_KEY` mediante un proveedor protegido; TEST nativo usa Credential Store. La igualdad de referencias no certifica que dos claves secretas tengan el mismo valor.

Los settings privados incluyen destino AIDP/workspace, región, bucket/namespace, dominio, administrador y modelo. No se publican configuraciones reales, `.oci`, claves, tokens, state ni recibos. El token Apify se entrega por archivo protegido o Credential Store, nunca como argumento literal.

```powershell
uv run aidp-market --help
uv run aidp-market status --settings .local/market-settings.json
```

`status` observa y revalida permisos; no instala ni activa investigación. La CLI presupone que la infraestructura y la asociación de memoria ya existen.

## Instalación y estado

El recibo individual está en `/Workspace/.artifact/ai_market_intelligence/.control/installer.json`. El código está en la carpeta hermana `agents/`, con entrada y dependencias directas y módulos en `core/`. Los respaldos de mantenimiento quedan fuera del subtree ejecutable.

La instalación recorre verificación, credenciales, compute, archivos, agente, permisos y TEST. Resume conserva operación, sesión y recursos confirmados. TEST ACTIVE e investigación pausada son estados compatibles: el probe pausado comprueba identidad, revisión y sesión sin inferencia ni actores; no demuestra calidad de investigación ni continuidad de checkpoints.

## Mantenimiento

| Acción | Comportamiento |
| --- | --- |
| `update` | Actualiza código conservando la selección instalada; `--change-model` aplica explícitamente el proveedor/modelo de settings. |
| `redeploy` | Redespliega exactamente el paquete y la configuración instalados; otro candidato requiere `update`. |
| `rollback` | Restaura el snapshot respaldado y verifica TEST. |
| `activate-test` | Activa investigación mediante un binding completo sobre el mantenimiento existente. |

Las operaciones de escritura requieren `--apply` y un UUID canónico `--operation-id`. Mantener el mismo UUID al reanudar. Install/update/redeploy reciben `--token-file`; rollback no necesita token ni disponibilidad de Apify. El binding activo se conserva en actualizaciones posteriores.

El journal congela configuración y hashes, respalda bytes antes de escribir y admite sólo bytes conocidos. El refresco se registra antes de enviarse. Una operación pendiente no se anuncia como completada: hay que observar o reanudar su journal. Un update que termina restaurado sigue siendo un update fallido; un rollback solicitado puede completar correctamente.

## Actualizar TEST conservando PROD

El mantenimiento bloquea por defecto una instalación con PROD. La ruta explícita utiliza `--preserve-prod --plugin-scripts <directorio-del-plugin>` y el adaptador `PluginTestDeployment`. Mantiene el lock propietario y usa realmente `native sync` y `native test-refresh` del plugin; no crea otro ejecutor de despliegue.

La actualización permite reemplazos y altas de archivos en carpetas ya presentes en el
inventario recibido. Verifica primero todos los originales y la ausencia de las altas,
incluso si un archivo preexistente tiene bytes idénticos al candidato. El journal fija
ambos inventarios y las altas; respalda por separado el paquete anterior y el candidato.
El propietario vuelve a exigir ausencia y guarda un intent de creación antes de cada
alta, con lectura de confirmación antes del sync de la CLI,
que conserva sus precondiciones de hashes para archivos existentes. El rollback retira
sólo altas con intent de creación propio, tipo FILE confirmado, intent durable de
retirada, hash esperado, ETag cuando está disponible y
confirmación de ausencia antes de restaurar el paquete anterior. Una respuesta perdida
se reconcilia por lectura; los bytes ajenos o modificados se preservan. Retirar o
renombrar archivos y crear carpetas nuevas requiere una migración de estructura.

La operación fija el snapshot publicado y verifica agente, compute, endpoint y configuración durante actualización y commit. No modifica ni elimina PROD. Una publicación es un snapshot separado del código de desarrollo: [Oracle Agent Flow deployment](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/agent-flow-deployment.html).

Los recibos del plugin y del instalador conservan su procedencia. Una respuesta perdida se reconcilia contra el intent existente antes de repetir un POST. Los efectos inciertos del modelo guardan el tipo de excepción y, si el proveedor lo expone, el código HTTP de error; excluyen mensajes, prompts y credenciales. El estado incierto no prueba por sí solo un fallo HTTP: también puede proceder del procesamiento de la salida estructurada. La confirmación exige TEST ACTIVE, revisión servida y probe correctos. Si falla de forma confirmada, el rollback restaura el respaldo y refresca TEST.

## Activar investigación

`activate-test` recibe `--research-binding`, settings privados, UUID y `--apply`. Reutiliza respaldo, lock, journal, refresco y rollback. No crea compute ni sesiones ni exige exportar el token Apify instalado. Al seleccionar OpenAI por primera vez, recibe `--llm-api-key-file` para crear su credencial independiente; las activaciones posteriores reutilizan esa referencia.

El binding debe conservar identidad, Credential Store, modelo, región/compartimento, almacenamiento y generación. Fuentes, idioma, guardrails y perfil observado del runtime son explícitos. `candidate_revision` selecciona el hash del paquete canónico. Un binding distinto no puede reanudar la misma operación; una respuesta incierta no declara activación.

Después, `inspect_runtime` comprueba código y modo, y `check_services` comprueba servicios sin iniciar investigación. Una consulta real sigue siendo necesaria para comprobar el flujo funcional.

## Diagnóstico de fuentes sociales

Un actor `SUCCEEDED` puede devolver únicamente un registro de error. Revisar el dataset, la cantidad de publicaciones normalizadas, el periodo y la atribución antes de considerar útil la captura. Conservar actor, build, entrada y recibo de cada prueba; leer el dataset existente antes de iniciar otra captura.

Las versiones fijadas de actores externos pueden dejar de estar disponibles. Un rechazo `build_not_found` indica un conector no disponible, no ausencia de publicaciones. Comprobar por lectura el actor, el identificador de build, su número y estado `SUCCEEDED` antes de elegir otra versión exacta; no sustituirla automáticamente por `latest`. El parámetro de ejecución recibe el número de build, como documenta [Apify](https://docs.apify.com/api/v2/actors-runs-post).

Para Facebook, la consulta temática usa [Facebook Posts Search](https://apify.com/scraper_one/facebook-posts-search/input-schema): `query`, `resultsCount`, `searchType=latest`, `startDate` y `endDate`. Los milisegundos de `timestamp` se normalizan y luego se aplica el intervalo exacto. La [lectura de una página pública seleccionada](https://apify.com/apify/facebook-posts-scraper/input-schema) permanece disponible para destinos explícitos. No introducir nombres de marcas ni perfiles fijos en el adaptador.

Para Instagram, la consulta temática usa [Instagram Hashtag Scraper](https://apify.com/apify/instagram-hashtag-scraper/input-schema) con `keywordSearch=true` y `resultsType=posts`, más una búsqueda acotada de hashtag con `keywordSearch=false`. El proveedor puede devolver publicaciones antiguas o un error de ruta junto a publicaciones válidas; revisar periodo y cobertura del dataset. El [lector de publicaciones](https://apify.com/apify/instagram-post-scraper/input-schema) conserva su uso para perfiles y enlaces explícitos.

Los comentarios usan [Facebook Comments Scraper](https://apify.com/apify/facebook-comments-scraper/input-schema) e [Instagram Comment Scraper](https://apify.com/apify/instagram-comment-scraper/input-schema), únicamente sobre enlaces admitidos de publicaciones observadas. No se solicitan respuestas anidadas. La ausencia de comentarios útiles puede deberse al límite de muestra, al periodo o a contenido irrelevante; no equivale a ausencia de conversación. Los builds están fijados en `core/actor_profiles.py`; actualizar uno requiere comprobar su entrada, proyección y salida antes de aceptar el candidato en TEST.

## Publicar y registrar para Agent Hub

La publicación se realiza con la CLI AIDP Agent Flow LangGraph instalada. Comprobar su ayuda y el proyecto privado antes de ejecutar; no forma parte del comando `aidp-market install` ni de una actualización TEST implícita.

1. Verificar layout exacto y aceptar el candidato servido en TEST bajo sus criterios declarados.
2. Planificar y aplicar `native publish` con autorización de publicación, manteniendo el lock propietario.
3. Observar y reconciliar el intent hasta confirmar la revisión publicada y el endpoint.
4. Inspeccionar la tarjeta y registrar o adoptar la identidad exacta en AI Registry.
5. Comprobar `registry readiness` y abrir Agent Hub mediante el enlace verificado.
6. Enviar una consulta real en el cliente y observar progreso, bloques y resultado final.

`registry portal-link` sólo resuelve navegación; `registry readiness` exige publicación y registro ACTIVE. Ninguno acredita que el chat haya atendido una pregunta. Un agente reemplazado no hereda el registro del anterior. Tras republicar hay que comprobar la versión y el contrato registrados.

La disponibilidad del endpoint, el registro, el streaming del cliente y la calidad factual son verificaciones distintas. Una entrega parcial o fallida en el chat no se presenta como investigación completada por el hecho de que el despliegue esté activo.

## Locks y recuperación de incertidumbre

El lock Object Storage es por plataforma/workspace y bucket: creación condicional y liberación con ETag exacto, sin expiración ni takeover automático. Configuraciones que usan buckets distintos no comparten exclusión; conservar el binding y serializar operaciones del workspace.

`release-lock` requiere ETag observado, `--confirm-no-running-hook` y `--apply`. Nunca liberar por nombre ni borrar recibos para desbloquear. Cuando una escritura queda incierta, conservar bloqueo e intent y observar su resultado; no iniciar otra operación para sustituirla.

La habilitación de memoria utiliza un intent en el bucket y comprueba READY con la base exacta. Un rechazo confirmado permite corregir y reanudar; timeout o resultado ambiguo conservan el intent enviado hasta reconciliación. No cambiar la base asociada para recuperar un agente. El mantenimiento conserva memoria y checkpoints.

## Cambios de estructura

`native inspect-binding` observa por separado ubicación CODE y archivos ejecutables. `native inspect-layout` verifica inventario completo. Cambiar entry/dependencies no mueve los metadatos ni el recibo. Los comandos `migrate` y `rebuild-development` son operaciones administrativas explícitas con contratos propios; no sustituyen una actualización ordinaria.

Las rutas e inventarios de recuperación deben coordinarse con el instalador antes de retirar archivos. El registro permanece por artefacto, fuera de `agents/`. Un árbol ejecutable sobrante impide declarar el layout cerrado aunque TEST responda.

## Límites operativos

La CLI del producto no ofrece delete, transferencia de state ni publicación PROD directa; la publicación del agente corresponde al plugin. La asociación READY de memoria no demuestra continuidad o aislamiento de checkpoints. Una prueba local no acredita IAM, red, dependencias ni streaming nativos. Las incertidumbres deben mantenerse visibles hasta su reconciliación.

## Diagnóstico de búsquedas web

Distinguir resultados del buscador, candidatos compatibles, selección del modelo y lecturas efectivas. Una segunda selección del mismo SERP no es una segunda búsqueda Google. Una selección sin destinos se informa como tal y el análisis recibe ese resultado para decidir la continuación. El filtro `tbs` se acompaña de operadores de calendario generados por el controlador; ninguna de esas pistas acredita por sí sola la fecha de publicación. Los títulos y snippets siguen siendo oportunidades de lectura, no evidencia del contenido completo.

El proveedor puede añadir al texto devuelto un sufijo `before:` igual a la fecha final de `tbs`, o `after:` igual a su fecha inicial. El normalizador reconoce únicamente esos sufijos ligados al intervalo y conserva la consulta efectiva y el sufijo en la procedencia. No acredita que se hayan aplicado todos los filtros; una restricción añadida puede reducir cobertura en el día limítrofe. Otros cambios de consulta siguen sujetos al contrato de identidad.
