# Arquitectura del agente

## Alcance

Market Intelligence es un agente genérico de investigación de mercado en AIDP. Su entrada recibe preguntas y continuaciones de conversación; su salida transmite progreso, hallazgos revisados y una respuesta con referencias. El paquete administrativo administra el ciclo de instalación sin implementar una aplicación web.

## Flujo de investigación

1. **Interpretación:** identifica entidad, geografía, objetivo, modo de investigación y restricciones. El código calcula el periodo por defecto desde el reloj del caso; los periodos explícitos se conservan.
2. **Planificación:** selecciona fuentes habilitadas pertinentes y consultas. Una pregunta abierta de conversación utiliza web y redes habilitadas; una restricción explícita limita esa selección.
3. **Ramas por fuente:** cada rama recopila, analiza, redacta y verifica su evidencia. Puede solicitar un refinamiento justificado dentro de los límites del flujo.
4. **Revisión y consolidación:** conserva unidades admitidas, retira afirmaciones rechazadas y sintetiza con atribución y calificadores. Una reparación utiliza la evidencia existente; no equivale a otra investigación ilimitada.
5. **Entrega:** el servidor construye referencias y periodo/alcance desde el estado validado, y emite un único cierre.

## Fuentes y concurrencia

| Familia | Fuentes configurables | Uso |
| --- | --- | --- |
| Web | `web` | Búsqueda y documentos públicos pertinentes. |
| Social | `x`, `facebook`, `instagram`, `youtube` | Conversación y publicaciones públicas. |
| Financiera | `financial_document`, `market_data` | Documentos, cifras reportadas y datos de mercado. |
| Económica | `world_bank` | Indicadores económicos pertinentes al encargo. |

Las familias ordenan tareas; no añaden supervisores ni llamadas al modelo. Se comparten dos cupos de captura y dos de decisiones del modelo. Fuentes distintas pueden avanzar simultáneamente; dentro de una fuente, el análisis espera la captura y la verificación espera el borrador. La llegada fuera de orden no determina el orden de la síntesis.

Habilitar una fuente no acredita acceso, resultados, completitud ni corroboración. YouTube puede aportar título y descripción sin transcripción audiovisual. La respuesta debe distinguir qué contenido se leyó. Una consulta de conversación no activa automáticamente finanzas o economía.

## Contrato de planificación y búsqueda

El coordinador propone una tarea por fuente seleccionada. `purpose` comunica su objetivo y condiciones materiales; las reglas de ejecución, fechas, cupos y herramientas permanecen en el controlador y el contexto de cada rama. Las consultas se proponen como grupos de conceptos literales y el código compila los operadores compatibles con cada fuente. Una búsqueda adicional necesita una brecha concreta; repetir la misma captura no añade evidencia.

El límite de 500 caracteres de `purpose` es una convención de este paquete, no un requisito de LangGraph ni una medida de cobertura. El adaptador OpenAI conserva `minLength` y `maxLength` en el esquema enviado para los modelos base admitidos por el catálogo; el contrato local se valida también después de normalizar las consultas. El formato estructurado no acredita pertinencia ni soporte factual. Los modelos ajustados tienen restricciones adicionales en [Structured Outputs de OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas) y requieren verificar su compatibilidad antes de incorporarlos.

El patrón de encargo explícito, investigadores concurrentes y cierre acotado puede contrastarse con [Open Deep Research de LangChain, revisión 1b7d2e8](https://github.com/langchain-ai/open_deep_research/tree/1b7d2e80db9faa586165c60e09096dbbfd483a64/src/open_deep_research). Sus contratos separan `research_brief` del trabajo por investigador; su evaluación incluye el paralelismo del supervisor. La guía de [investigación multiagente de Anthropic](https://www.anthropic.com/research/multi-agent-research-system) recomienda objetivos, alcance y esfuerzo acordes al encargo. Son referencias de diseño, no evidencia de calidad de este agente. No se incorpora código externo ni otra dependencia.

La investigación temática de Facebook usa búsqueda de publicaciones por palabras clave. Instagram combina búsqueda por palabras clave y una variante de hashtag derivada del encargo. Los destinos explícitos mantienen su lector de publicaciones; una consulta temática no se convierte en una consulta exclusiva del perfil oficial. Cada fuente admite hasta veinte publicaciones candidatas y filtra el intervalo exacto del encargo, aunque el proveedor solo acepte fechas por día o entregue resultados antiguos.

Después se leen hasta cinco comentarios de cada una de dos publicaciones pertinentes por fuente y pasada, cuando la captura indica que tienen comentarios. Los comentarios conservan su fecha, enlace y publicación de origen y se distinguen mediante `content_basis=comment_text`. Son opiniones atribuidas dentro de una muestra acotada, no afirmaciones de la marca ni una medición representativa del mercado. Búsqueda, hashtag y comentarios tienen efectos durables separados; un replay reutiliza sus recibos. El refinamiento reutiliza también los hashtags y comentarios idénticos ya capturados en esa solicitud. Una lectura de comentarios fallida conserva las publicaciones útiles y declara la limitación.

Instagram admite perfiles y enlaces directos a publicaciones en el [contrato del extractor](https://apify.com/apify/instagram-post-scraper/input-schema). El error conocido de perfil inaccesible conserva URL y `username` en la proyección del dataset para distinguirlo de datos incompatibles. La búsqueda temática usa el [extractor de hashtags y palabras clave](https://apify.com/apify/instagram-hashtag-scraper/input-schema). Si una respuesta contiene publicaciones válidas y un error conocido de una ruta solicitada, se conservan las publicaciones y se declara la cobertura parcial. Un error de otra ruta o de formato desconocido sigue rechazándose.

## Paquete y estructura remota

La fuente canónica está en `aidp/workspaces/default/files/agents/`. La entrada `market_intelligence.py` delega en `core/`; las decisiones y sus prompts residen en módulos legibles de `core/decisions/`. `package/lab.json` vincula inventario y hashes.

En AIDP, el artefacto se ubica bajo `/Workspace/.artifact/ai_market_intelligence/`:

- `agents/`: entrada, dependencias, manifiesto y paquete `core/`.
- `.control/installer.json`: estado individual de instalación.
- `.control/maintenance/`: journal y respaldos de mantenimiento.

El estado de control es hermano de `agents/`, no código del agente. El instalador y su CLI lo consultan para identificar y recuperar la instalación. El cierre de layout compara ubicación de metadatos CODE, archivos, carpetas y hashes; archivos ejecutables sobrantes no constituyen una instalación correcta.

## Evidencia, memoria y efectos

Las evidencias conservan texto, procedencia, fecha de publicación y fecha de observación. Esas fechas tienen funciones distintas. Una selección de documentos no permite afirmar ausencia en todo el inventario ni en la conversación pública. Las cifras financieras conservan empresa, moneda observada, unidad, periodo y calificador; el contexto geográfico no resuelve un símbolo monetario ambiguo.

Los checkpoints utilizan la memoria nativa de Oracle. Object Storage conserva artefactos y registros durables de efectos. Una operación externa incierta debe reconciliarse antes de repetirla; cancelar el consumidor no demuestra que el proveedor haya cancelado su trabajo.

## Streaming

El runtime entrega un iterador asíncrono. Los eventos identifican solicitud, revisión, secuencia, tipo y fuente para permitir al cliente ordenar, deduplicar y recuperar la salida. El progreso describe operaciones y estados; no expone razonamiento privado. Los bloques revisados y el resultado final son eventos distintos.

El consumidor estructurado utiliza el protocolo `ai-market-intelligence`; el chat recibe la presentación legible. El ejemplo `scripts/market-stream-example.mjs` muestra cómo consumir el protocolo sin aportar un frontend propio. La presencia de eventos en el servidor no demuestra que un intermediario o navegador los presente en vivo: esa frontera se comprueba con una conversación real.

Las generaciones públicas de borradores, síntesis y resúmenes pueden emitir fragmentos reales del campo narrativo `text` mientras el modelo produce su salida estructurada. El adaptador OpenAI solicita SSE y proyecta sólo ese campo; las decisiones, IDs internos, herramientas y razonamiento no se transmiten como prosa. El resultado completo sigue sujeto a esquema, límites, citas y revisión factual. La aceptación de transporte no acredita los hechos de un fragmento provisional.

Los fragmentos viajan en envelopes transitorios `kind=text` del protocolo 2.0, separados del contador durable. El controlador mantiene sólo buffers acotados y escribe los bloques canónicos tras revisión, sin actualizar el snapshot por token. Un bloque rechazado, incompleto o sustituido retira su texto provisional. El resultado final prevalece; el replay recupera exclusivamente estado y publicaciones durables. La ejecución nativa de tokens se valida con el modelo OpenAI configurado; los controles locales no certifican otra integración de modelos.

## Límites

Los verificadores pueden rechazar contenido respaldado o aceptar una atribución demasiado amplia. El modelo puede confundir metadatos, combinar detalles de fuentes distintas o emitir citas no literales. Las validaciones estructurales, la revisión factual y la evaluación comparativa son controles complementarios; ninguno garantiza precisión universal.

## Síntesis por unidad

El análisis devuelve síntesis individuales en un lote estructurado, además de los hallazgos
de la fuente. El controlador agrupa por fuente, URL pública canónica y localizador; ese
`item_id` no depende del recibo ni del texto capturado. Las evidencias conservan sus IDs
de captura. Una publicación recapturada actualiza su revisión sin cambiar la selección.
Los comentarios se vinculan únicamente mediante el padre observado y validado, no mediante
una coincidencia en el párrafo ni por compartir fuente.

Antes del borrador de fuente, las propuestas individuales pasan una revisión factual por
lotes con acceso a los originales. Una unidad rechazada no arrastra las otras admitidas.
El borrador de fuente y la consolidación reciben las fichas admitidas y siguen contrastando
las evidencias originales. Una descripción de YouTube continúa siendo título/descripción,
sin acreditar visión del video. No se crean llamadas por cada comentario ni capturas nuevas
para redactar fichas; las revisiones adicionales sí consumen llamadas al modelo.

La admisión respeta el presupuesto existente de contexto: primero conserva observaciones
completas y después incorpora metadatos, bloques y fichas que caben con todos sus originales.
Los comentarios no se admiten sin su publicación padre. Las coberturas del contexto distinguen
observaciones, unidades individuales, fichas y bloques omitidos; omitir una ficha opcional no
impide analizar la evidencia admitida de la fuente.
Si los comentarios fueron recuperados pero su unidad quedó fuera del contexto de una pasada,
la ficha publica `comments_analysis.status=unavailable`, con texto e IDs vacíos y conteo analizado
cero. `not_collected` se reserva para padres sin comentarios observados vinculables. Esta
comprobación usa el inventario del servidor, sin admitir borradores fuera de revisión ni
conservar una síntesis anterior descartada; el cambio sigue las revisiones normales de la ficha.

`content_items` es una colección pública opcional en los sobres existentes de evento,
respuesta y snapshot, fuera de `MarketEvent`. La colección se reemplaza completa por revisión;
la ausencia del campo no actualiza nada y una lista vacía con revisión superior retira lo
publicado. Las revisiones y fingerprints privados sobreviven a la retirada de un ítem.
El streaming de fuente y consolidación conserva su contrato; no hay deltas por ítem.

La ficha de una publicación usa `content_items.items[].text` como resumen revisado y
`primary_evidence_id` para resolver autor, fecha, URL y medios del padre en `references`.
Los comentarios no son fichas de publicaciones: `comments_analysis.evidence_ids` selecciona
sus referencias, cuyo `requested_url` conserva el padre observado. Cada referencia
`content_basis=comment_text` incluye `text`, el texto recuperado de hasta 12.000 caracteres,
no una clasificación de sentimiento ni el resumen del post. El cliente debe mostrarlo como
texto no confiable, sin interpretar HTML. Facebook conserva `author_name` y `author_url`
del comentarista cuando el actor entrega `profileName` y un `profileUrl` público válido;
un nombre ausente queda desconocido y nunca se sustituye por el autor del padre.

La descripción por unidad se redacta independientemente de autor, fecha y URL de
la ficha. Esos metadatos no se repiten como prefijo; una atribución o fecha material
para comprender el hallazgo sí se conserva. Esta instrucción del prompt no equivale
a una garantía de estilo del modelo y requiere evaluación sobre sus salidas reales.
El contrato público de actividades y propósito de eventos se define en
[operaciones](operations.md#presentación-explícita-en-eventos). La UI presenta el
contenido recibido y mantiene separados sus propios avisos de conexión y sesión.
Los medios de la búsqueda Facebook se leen de `attachments` tipados como `photo` o `video`,
conservando su tipo, URL recuperada y firma. Un video no se convierte en una imagen y el
enlace no acredita análisis del contenido ni su vigencia.

## Descubrimiento web y continuación

El plan traduce una investigación conversacional a palabras clave de identidad, mercado y menciones publicadas, sin imponer incidentes o sentimientos. El compilador SERP añade el intervalo de calendario tanto en `tbs` como en los operadores `after:`/`before:` de Google; la consulta del modelo no controla fechas. Los límites léxicos abarcan los días limítrofes y la validación posterior conserva el periodo exacto del encargo. Los metadatos distinguen consulta temática y consulta efectiva.

El análisis web recibe el estado, número de lote, cantidad recuperada y limitaciones de la última recuperación además de la evidencia acumulada. Una selección vacía posterior puede así justificar una reformulación diferente si sigue pendiente una condición material y queda presupuesto. Se conserva el máximo de una reformulación por fuente, dos lecturas por lote y diez destinos globales; no se llenan cupos con páginas irrelevantes.

El proveedor puede añadir al texto devuelto un sufijo `before:` igual a la fecha final de `tbs`, o `after:` igual a su fecha inicial. El normalizador reconoce únicamente esos sufijos ligados al intervalo y conserva la consulta efectiva y el sufijo en la procedencia. No acredita que se hayan aplicado todos los filtros; una restricción añadida puede reducir cobertura en el día limítrofe. Otros cambios de consulta siguen sujetos al contrato de identidad.
