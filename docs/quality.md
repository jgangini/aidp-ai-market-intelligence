# Evaluación de calidad

## Aceptación de texto incremental

Comprobar varios fragmentos públicos antes del cierre real del proveedor, no sólo particiones de una respuesta ya terminada. El ensayo HTTP local mantiene la conexión abierta con una barrera y exige callbacks antes de liberarla. Las regresiones cubren UTF-8 y escapes divididos, razonamiento/campos privados excluidos, refusals, herramientas, identidad de completado, finalización truncada, uso registrado y retirada de un provisional rechazado.

El control de grafo exige fragmentos anteriores a la revisión, reemplazo por el bloque durable, cierre sin intervalo vacío, retirada explícita ante rechazo, presupuesto acotado y ausencia de tokens en snapshots. El consumidor comprueba intercalación, deduplicación, orden, revisiones, replay y prevalencia del resultado final. El contrato limita exposición a prosa pública provisional: no presenta como verificados los hechos antes de la revisión ni permite que ese texto dirija herramientas. La aceptación conjunta requiere observación nativa y varios fragmentos visibles en el cliente antes del final, con el mismo candidato, sesión y solicitud.

La aceptación nativa y visual está verificada en TEST para el candidato
`5152f7a63c97d7a86d78e62babcd3ecda81f33f53a5febbc0e58a5de99c39642`
(10 de noviembre de 2026). La consulta acotada a YouTube sobre D1 terminó en
88,324 segundos, con siete llamadas al modelo, una captura Apify y tres referencias.
El recibo confirma respuesta `complete`, sesión liberada y cero efectos pendientes,
sin replay ni recuperación manual. Se conservaron las identidades del agente, compute
y sesión TEST; PROD no se modificó.

En 392 observaciones del Browser se comprobó texto por fuente dentro del proceso abierto
y crecimiento real de la síntesis de 20 a 1.297 caracteres con la consulta todavía activa
y el proceso plegado. No hubo estados vacíos desde el inicio de la síntesis ni alertas;
el scroll se mantuvo en el final del área visible. El transporte recibió 93 frames y 80
paquetes de texto público. Los recibos privados están en
`.local/aidp-development/public-text-terminal-handoff-retry/` y la verificación de revisión
servida en `.local/aidp-development/public-text-terminal-handoff/verification.json`.
Esos archivos permanecen en almacenamiento privado ignorado por Git.

Este cierre acredita el streaming y su reemplazo canónico en Agent Desk contra TEST
para ese caso. No demuestra cobertura de todas las fuentes, calibración general del
evaluador, rendimiento representativo ni aceptación de esta revisión en Agent Hub o PROD.
La aceptación funcional se comprueba por separado del gate de arquitectura. La extracción
HCL de Graphify continúa sin su parser opcional.

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

La colección tiene aceptación nativa en TEST para el candidato
`00a64d5e6749e40d4d6f2cb192105a24bb7a21d2d04f44cb5d4933dc4ca12e5f`
(10 de noviembre de 2026). Un único caso real sobre D1 en Colombia, acotado a YouTube
durante siete días, entregó dos fichas revisadas y dos referencias. La solicitud terminó
`completed` con respuesta `partial` por su alcance: ocho llamadas al modelo, una operación
Apify, cero efectos pendientes de esa solicitud y sesión liberada. El navegador recibió
101 paquetes de texto; se observaron 51 estados crecientes del proceso y 41 de la respuesta
final. El carrusel cambió entre resúmenes propios y la recarga conservó colección y final,
sin avisos ni errores. El scroll tuvo cuatro desajustes transitorios de 122 a 415 ms,
hasta 73 px, y terminó con separación cero del final.

Los recibos están en `.local/aidp-development/content-items-native-20261110/` y permanecen
fuera de Git. Este caso valida títulos y descripciones de YouTube, no análisis audiovisual
ni comentarios sociales nativos; estos últimos conservan su validación local. La facturación
real sigue sin verificar. Los intentos históricos inciertos se preservaron sin reintentarlos;
PROD no cambió. El postflight conserva 1.992 pruebas Python, 25 de protocolo y 17 de Terraform
aprobadas. El gate Sentrux aprueba con la referencia histórica intacta, sin reglas adicionales,
ciclos ni god files. La validación del paquete instalado y la política de errores del probe
pertenecen a comprobaciones independientes del inicio y polling de mantenimiento; esta
separación mantiene las guardas de identidad y rollback y reduce su complejidad. Un gate
sin regresión no significa que toda la deuda estructural histórica haya desaparecido.

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
