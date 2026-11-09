# Evaluación de calidad

## Qué se evalúa

El objetivo es una respuesta útil al encargo, con hechos respaldados, alcance fiel y cobertura declarada. Repetir las mismas palabras no es un requisito. Una entrega con referencias o un estado operativo `completed` no equivalen a calidad aprobada; `partial` expresa límites del resultado, no una puntuación universal.

Separar tres niveles:

- **Decisiones:** entidad, periodo, fuentes e intención interpretada; justificación de refinamientos.
- **Trayectoria:** herramientas y datos realmente utilizados, errores, reparaciones, tiempos y efectos.
- **Respuesta:** respaldo de afirmaciones, atribución, calificadores, cobertura, claridad y pertinencia.

## Criterios

Una respuesta fiel no introduce restricciones que el usuario no pidió. Las afirmaciones materiales deben estar sustentadas por la evidencia citada. Atribuir un predicado a varios documentos requiere soporte en cada miembro nombrado; detalles repartidos entre documentos no forman una afirmación compartida.

Una repetición no prueba corroboración independiente, pero tampoco autoriza afirmar que sus autores sean dependientes. Distinguir una limitación de inferencia de una ausencia empírica. Las publicaciones citadas y el inventario total tienen alcances distintos.

Las cifras conservan empresa, población, geografía, periodo, unidad, moneda y calificador. `$` por sí solo no identifica COP o USD. Una cautela de moneda puede respaldarse comprobando todo el texto suministrado; un fragmento no demuestra que el documento completo omita esa información. Una moneda explícita en otra parte del texto refuta la cautela.

## Comparar ejecuciones

Congelar pregunta, modelo, candidato, evidencia y reloj cuando se busca aislar variabilidad de interpretación y síntesis. Conservar cada corrida, incluida una que falle. Comparar temas y afirmaciones centrales, contradicciones, omisiones, decisiones y tiempos; no usar similitud textual como prueba factual.

Un replay de captura no mide disponibilidad de fuentes nuevas. Cinco corridas de una pregunta no establecen precisión general. Comparar rendimiento exige controlar condiciones; una reducción de segundos entre versiones no demuestra por sí sola que el paralelismo sea su causa.

## Calibración del evaluador

El plugin evalúa respuestas guardadas con una rúbrica y hechos de referencia vinculados por hashes. Calibrar con positivos respaldados y negativos que cambien una propiedad material: entidad, atribución, moneda o alcance. Los pares contrastivos comparten contexto; un negativo con otro defecto no aísla la propiedad que se quería medir.

Conservar etiquetas y resultados originales. Un desacuerdo requiere revisar las fuentes y el contrato, no relabelar para obtener aprobación. Las citas que no sean substrings literales fallan el contrato aunque su paráfrasis parezca correcta; no normalizarlas silenciosamente ni contarlas como un veredicto semántico válido.

Separar fallos del proveedor, salidas fuera de esquema, referencias inválidas y errores factuales. Un control no ejecutado no es un rechazo del contenido. La preparación focal de un evaluador permite medir ese alcance; no certifica su precisión general ni sustituye calibración con revisores independientes.

No existe aquí un umbral universal del 70 u 80 por ciento. Los criterios de aceptación deben estar definidos y contrastados para el producto; varios textos pueden coincidir en el mismo error.

## Método y límites

La comprobación por afirmaciones adapta la separación entre unidades factuales y soporte descrita en [FActScore](https://arxiv.org/abs/2305.14251). La evaluación de respuesta, trayectoria y decisión sigue las [guías de evaluación de agentes de LangSmith](https://docs.langchain.com/langsmith/evaluation-approaches). La calibración de jueces debe conservar fallos y desacuerdos, conforme al enfoque de [evaluaciones de agentes de Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

Estas fuentes aportan métodos, no certifican este agente ni fijan un porcentaje de aprobación. La exactitud factual y la consistencia general requieren evidencia del dominio. Las pruebas mecánicas y los jueces basados en modelos pueden equivocarse.
