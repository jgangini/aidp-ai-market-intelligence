# Evaluación de calidad

## Contratos opcionales de informe

Los [esquemas de extensiones de mercado](../contracts/README.md) describen métricas
de muestra y sentimiento por objetivo/aspecto en un informe genérico. El productor
liga `delivery` a solicitud y ejecución de tarea antes de admitir efectos; no
permite cambiar plantilla o metadatos al reintentar la misma solicitud ni iniciar
otra con el mismo `task_run_id` en esa conversación. La identidad de tarea no
reemplaza la autenticación y el aislamiento de sesión.

Los conteos se derivan de evidencia retenida y revisada, no de cifras generadas por
el modelo. Una recaptura con texto distinto no hereda el análisis del texto viejo.
Las propuestas de sentimiento conservan objetivo, aspecto, original y soporte
literal; otra decisión revisa cada anotación con su propia evidencia. Las revisiones
se dividen en lotes acotados que incluyen originales y contexto padre. Una anotación
omitida o rechazada conserva abstención segura; ni su razón factual rechazada ni su
polaridad reaparecen en el agregado. El método permanece `not_calibrated`.

Las regresiones recorren el adaptador nativo empaquetado con proveedores simulados:
contrato, plantilla estable, manifiesto de soporte, periodos originales, replay sin
efectos, aislamiento, correlación y cierres largos. Los fixtures son sintéticos; no
acreditan disponibilidad de servicios ni precisión del modelo. La aceptación de
una instalación exige verificar el candidato servido y el consumidor. Un informe
de otro dominio no necesita estas extensiones. JSON Schema no sustituye la revisión
semántica ni las pruebas nativas.

## Utilidad del informe ejecutivo

El texto prioriza hallazgos pertinentes y respaldados. Una implicación comercial,
oportunidad, riesgo o acción sugerida debe tener premisas verificables y conservar
su carácter condicional; no se exige completar secciones sin evidencia. El mismo
verificador y la reparación acotada se mantienen. Ni un estado operativo completo
ni la similitud entre respuestas acreditan calidad comercial.

La proyección separa soportes de la síntesis, bloques admitidos e inventario de las
extensiones. Una referencia recuperada no es por sí sola pertinente ni admisible.
Sin soporte admitido, el informe se abstiene brevemente de recomendar decisiones.
Las unidades sin sentimiento clasificable no se interpretan como neutrales.

Los controles locales cubren consejo condicional conservado, referencias de
inventario no promovidas, material rechazado, ausencia de soporte, límites
materiales, metadatos operativos conservados y periodos documentales/financieros
no confundidos con la ventana de captura. Los fixtures no demuestran que un
modelo real produzca mejores recomendaciones ni resuelven la calibración de
pertinencia o sentimiento; esa aceptación requiere su evaluación específica.

## Aceptación de texto incremental

Comprobar varios fragmentos públicos antes del cierre real del proveedor, no sólo particiones de una respuesta ya terminada. El ensayo HTTP local mantiene la conexión abierta con una barrera y exige callbacks antes de liberarla. Las regresiones cubren UTF-8 y escapes divididos, razonamiento/campos privados excluidos, refusals, herramientas, identidad de completado, finalización truncada, uso registrado y retirada de un provisional rechazado.

El control de grafo exige fragmentos anteriores a la revisión, reemplazo por el bloque durable, cierre sin intervalo vacío, retirada explícita ante rechazo, presupuesto acotado y ausencia de tokens en snapshots. El consumidor comprueba intercalación, deduplicación, orden, revisiones, replay y prevalencia del resultado final. El contrato limita exposición a prosa pública provisional: no presenta como verificados los hechos antes de la revisión ni permite que ese texto dirija herramientas. La aceptación conjunta requiere observación nativa y varios fragmentos visibles en el cliente antes del final, con el mismo candidato, sesión y solicitud.

La evidencia de aceptación vincula el candidato servido, la identidad de la sesión y
la solicitud con los paquetes recibidos y el estado renderizado por el cliente.
Registrar el primer progreso, el primer delta, la respuesta canónica, los efectos
y las limitaciones. Separar el tiempo de preparación o despliegue del tiempo de
investigación. Una grabación local o una animación no reemplazan la observación de
una transmisión nativa; un caso de una fuente no acredita todas las demás.

Un fallo conocido de validación, timeout o del servicio OpenAI durante la investigación produce
un evento de fallo y una respuesta terminal de error, después de drenar las tareas
paralelas admitidas. El texto público no expone el mensaje privado del proveedor.
Un fallo de transporte o un HTTP 429 no autoriza repetir efectos: los recibos inciertos
conservan su estado y bloquean una investigación nueva en la misma sesión hasta su
conciliación. Una cancelación del consumidor sigue propagándose como cancelación.
Los fallos inesperados de almacenamiento, checkpoints o serialización requieren su
propio diagnóstico; no se convierten en una respuesta de investigación aceptada.
Si una rama detecta la incertidumbre registrada por otra antes de recibir su fallo,
el coordinador drena las ramas ya admitidas sin cancelar la excepción originaria.
Ese fallo conserva su diagnóstico frente al bloqueo derivado; no se repite ninguna
llamada para resolver el orden de llegada.

## Síntesis individuales y comentarios

La colección opcional se valida por separado de la síntesis de fuente: cada ficha debe
responder por su unidad original y cada resumen de comentarios por su padre exacto.
Las pruebas contrastivas incluyen intercambio de padres, autor frente a comentarista,
comentarios sin nombre, publicaciones recapturadas, unidades rechazadas, retirada y
reaparición, versiones desconocidas y clientes sin colección. Dos recibos del mismo
comentario cuentan un comentario distinto, no dos personas o testimonios independientes.
También se comprueban respuestas pendientes o fallidas con fichas conservadas, originales
adicionales a los hallazgos de fuente, admisión sin comentarios huérfanos, contexto grande
con múltiples bloques y envío de colección sólo al cambiar su revisión. El evento duplicado
idéntico se suprime durante la emisión; modificar su cuerpo sigue siendo un error.

El análisis propone síntesis por lotes y la revisión decide por unidad antes de publicar;
los originales siguen disponibles para la verificación y consolidación. Una ficha ausente
no demuestra que no haya publicaciones. La comparación de costo y latencia requiere
mediciones controladas, separando generación, revisión, primer resultado y cierre completo.

La aceptación de fichas comprueba que la selección de una publicación conserva su
resumen, autor y fecha observados, medios, evidencias y estado de comentarios. El
resumen no repite los metadatos como prefijo. Las pruebas de navegación y recarga
se hacen antes de cerrar el contexto del navegador; datos guardados o una revisión
del contrato no demuestran por sí solos que todas las fichas hayan sido visibles.

Comentarios sin evidencia admitida permanecen sin analizar. Una captura fallida o
incompatible conserva su diagnóstico y sus recibos; no permite afirmar que la
publicación carezca de comentarios. Una miniatura se entrega sólo si existe en los
datos recuperados; el avatar del autor no sustituye a la imagen de una publicación.

Los recibos, versiones observadas, métricas y resultados de cada ensayo se conservan
en almacenamiento privado ignorado por Git. La aceptación funcional y el gate de
arquitectura son comprobaciones distintas; ambos declaran su alcance y sus fallos.

## Qué se evalúa

El objetivo es una respuesta útil al encargo, con hechos respaldados, alcance fiel y cobertura declarada. Repetir las mismas palabras no es un requisito. Una entrega con referencias o un estado operativo `completed` no equivalen a calidad aprobada; `partial` expresa límites del resultado, no una puntuación universal.

Separar tres niveles:

- **Decisiones:** entidad, periodo, fuentes e intención interpretada; justificación de refinamientos.
- **Trayectoria:** herramientas y datos realmente utilizados, errores, reparaciones, tiempos y efectos.
- **Respuesta:** respaldo de afirmaciones, atribución, calificadores, cobertura, claridad y pertinencia.

## Criterios

La frontera de streaming se comprueba con HTTP real controlado: varios fragmentos deben
atravesar el adaptador y el grafo antes de liberar el cierre del proveedor, tanto para
`source-<fuente>` como para `consolidated`. Rechazar un borrador o su reparación retira
su provisional inmediatamente. Los controles de polling usan reloj simulado para
actores que finalizan a los 85 y 115 segundos: esperan el mismo `runId`, mantienen
límites finitos y no duplican capturas. Estos controles no sustituyen la aceptación
nativa y visual del candidato servido con el modelo configurado.

Una respuesta fiel no introduce restricciones que el usuario no pidió. Las afirmaciones materiales deben estar sustentadas por la evidencia citada. Atribuir un predicado a varios documentos requiere soporte en cada miembro nombrado; detalles repartidos entre documentos no forman una afirmación compartida.

Una repetición no prueba corroboración independiente, pero tampoco autoriza afirmar que sus autores sean dependientes. Distinguir una limitación de inferencia de una ausencia empírica. Las publicaciones citadas y el inventario total tienen alcances distintos.

Las cifras conservan empresa, población, geografía, periodo, unidad, moneda y calificador. `$` por sí solo no identifica COP o USD. Una cautela de moneda puede respaldarse comprobando todo el texto suministrado; un fragmento no demuestra que el documento completo omita esa información. Una moneda explícita en otra parte del texto refuta la cautela.

## Comparar ejecuciones

«Hoy» y «ayer» como periodos de publicación se calculan desde el reloj de la
solicitud en `publication_timezone`, una zona IANA. Si el usuario no especifica
zona, el controlador sólo la deriva del país resuelto cuando `zone.tab` contiene
una única zona para ese país; no utiliza el país por defecto del buscador. La tabla
se lee de los datos del sistema o del paquete `tzdata` ya disponible. Varias zonas
o datos ausentes requieren aclaración. Los límites se convierten a UTC; hoy termina
en el instante de la solicitud, sin incluir horas futuras. Las fechas explícitas
no relativas y la ventana móvil de siete días conservan sus contratos.

Esta política utiliza la [base IANA](https://www.iana.org/time-zones/theory) y
[`zoneinfo` de Python](https://docs.python.org/3/library/zoneinfo.html); no mantiene
un mapa propio de países ni reglas particulares por empresa o plataforma.

Antes de evaluar recuperación, comprobar que el esquema de generación y el consumidor coincidan: restricciones de longitud, fuentes admitidas, normalización de consultas y condiciones entre campos. Las regresiones de planificación comprueban el esquema HTTP realmente enviado y los límites de `purpose` de 500/501 caracteres; un plan inválido no puede iniciar capturas. Una salida que falla antes de buscar no permite calificar los resultados del buscador.

Para evaluar el criterio de búsqueda, usar casos del corpus con entidades distintas y evidencia revisada. Comprobar la selección de fuentes y la pertinencia de los términos; distinguir descubrimiento, documentos leídos, fecha publicada, atribución y cobertura de las condiciones solicitadas. Comparar alternativas o alias manteniendo constantes modelo, reloj, presupuesto y conjunto de candidatos cuando se aísla la selección. En fuentes abiertas no se conoce todo el universo de publicaciones: declarar la cobertura observada, sin inventar un porcentaje de exhaustividad global.

[LangChain documenta validación y manejo de errores de salida estructurada](https://docs.langchain.com/oss/python/langchain/structured-output). Sus reintentos son un patrón posible, no una obligación de añadirlos aquí: cualquier reparación futura debe tener límite, quedar registrada como otra llamada y corregir un error confirmado. Un timeout o resultado incierto exige conciliación; no se trunca contenido ni se amplían límites para hacer pasar un plan.

Congelar pregunta, modelo, candidato, evidencia y reloj cuando se busca aislar variabilidad de interpretación y síntesis. Conservar cada corrida, incluida una que falle. Comparar temas y afirmaciones centrales, contradicciones, omisiones, decisiones y tiempos; no usar similitud textual como prueba factual.

Un replay de captura no mide disponibilidad de fuentes nuevas. Cinco corridas de una pregunta no establecen precisión general. Comparar rendimiento exige controlar condiciones; una reducción de segundos entre versiones no demuestra por sí sola que el paralelismo sea su causa.

## Recuperación social

Las regresiones recorren la proyección real de campos del dataset y la clasificación de errores, además del normalizador aislado. Cubren milisegundos de Facebook, publicaciones mixtas con errores de la ruta de Instagram solicitada, comentarios con padre admitido y fechas propias, y rechazo de padres ajenos o errores desconocidos. La composición temática debe mantener sus límites de publicaciones y comentarios, conservar publicaciones útiles cuando falla la lectura de comentarios y reutilizar los efectos completados durante un replay. Una operación pendiente no autoriza iniciar otra captura.

La validación funcional distingue publicaciones de comentarios y revisa pertinencia, fecha, atribución y respaldo de las afirmaciones. Un comentario no prueba por sí solo una práctica comercial de la marca. Una única pregunta real comprueba el candidato y los conectores en ese caso; no acredita calibración general, cobertura exhaustiva de las redes ni estabilidad entre ejecuciones.

La prueba de disponibilidad de un lector no acredita cobertura temporal ni calidad del análisis. Una publicación antigua, de otra entidad o emitida por la propia marca no sustituye opiniones recientes de terceros.

## Calibración del evaluador

El plugin evalúa respuestas guardadas con una rúbrica y hechos de referencia vinculados por hashes. Calibrar con positivos respaldados y negativos que cambien una propiedad material: entidad, atribución, moneda o alcance. Los pares contrastivos comparten contexto; un negativo con otro defecto no aísla la propiedad que se quería medir.

Conservar etiquetas y resultados originales. Un desacuerdo requiere revisar las fuentes y el contrato, no relabelar para obtener aprobación. Las citas que no sean substrings literales fallan el contrato aunque su paráfrasis parezca correcta; no normalizarlas silenciosamente ni contarlas como un veredicto semántico válido.

Separar fallos del proveedor, salidas fuera de esquema, referencias inválidas y errores factuales. Un control no ejecutado no es un rechazo del contenido. La preparación focal de un evaluador permite medir ese alcance; no certifica su precisión general ni sustituye calibración con revisores independientes.

No existe aquí un umbral universal del 70 u 80 por ciento. Los criterios de aceptación deben estar definidos y contrastados para el producto; varios textos pueden coincidir en el mismo error.

## Método y límites

La comprobación por afirmaciones adapta la separación entre unidades factuales y soporte descrita en [FActScore](https://arxiv.org/abs/2305.14251). La evaluación de respuesta, trayectoria y decisión sigue las [guías de evaluación de agentes de LangSmith](https://docs.langchain.com/langsmith/evaluation-approaches). La calibración de jueces debe conservar fallos y desacuerdos, conforme al enfoque de [evaluaciones de agentes de Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

Estas fuentes aportan métodos, no certifican este agente ni fijan un porcentaje de aprobación. La exactitud factual y la consistencia general requieren evidencia del dominio. Las pruebas mecánicas y los jueces basados en modelos pueden equivocarse.

## Continuación web

Las regresiones cubren una primera lectura insuficiente, selección posterior vacía y una única consulta diferente con recuperación útil, sin volver a leer destinos ni ampliar el presupuesto. El contexto del análisis debe cambiar cuando la última recuperación no aporta evidencia aunque conserve páginas anteriores, para que un resultado de análisis guardado no oculte el estado nuevo. Comprobar también límites de calendario entre años, consulta efectiva, identidad del recibo y navegación sin filtros temporales. Una búsqueda con más resultados no aprueba por sí sola: revisar pertinencia, fechas y lectura de los destinos seleccionados.
