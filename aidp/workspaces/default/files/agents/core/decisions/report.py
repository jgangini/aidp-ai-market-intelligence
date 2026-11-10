"""Report decisions: instructions, contracts and normalization."""

from ..research_contracts import MarketPublicBlock
from ..research_contracts import MarketRepairBundle

NARRATIVE_SCOPE = """Alcance narrativo
Responde sobre lo que dicen las fuentes del tema solicitado. published_at es fecha de
publicación; observed_at es fecha de captura: no intercambies sus funciones. Si el encargo
no pide trazabilidad de la investigación, omite fechas de captura u observación y relatos
sobre las operaciones realizadas. Conserva las fechas de publicación que aporten al tema
y los años, ejercicios y periodos propios de los hechos comerciales o financieros.
El servidor presenta el periodo y alcance desde el estado validado. No reconstruyas una
nota global de recuperación a partir de los documentos seleccionados: al citar sus fechas,
di publicaciones citadas, sin atribuir ese intervalo a todo el inventario recuperado.
Una cautela explica qué permite inferir este resumen; no demuestra una ausencia en el
mundo. No conviertas relatos repetidos en una afirmación de que sus autores son dependientes
o no son testimonios separados. Puedes abstenerte de inferir corroboración independiente
sin afirmar que no existe. Conserva sólo las precisiones pertinentes al encargo.

"""

DRAFT_ID = "market_draft"
DRAFT_PROMPT = NARRATIVE_SCOPE + """Tarea
Redacta una respuesta útil al encargo con los hallazgos respaldados.
content_items contiene las síntesis individuales ya admitidas de esta fuente. Úsalas para
organizar temas, conservando la evidencia original como soporte; no repitas el párrafo de
fuente dentro de cada ítem ni atribuyas comentarios al autor de la publicación. Una ficha
basada en título y descripción no acredita haber visto el video. No infieras cobertura
completa a partir de las fichas seleccionadas y mantén las limitaciones pertinentes.

Selección y presentación
En conversación abierta, organiza por temas y empieza por el hallazgo principal. Cada frase
debe aportar un tema o una diferencia que cambie su interpretación. Agrupa las repeticiones
por su contenido compartido; usa la atribución individual para los detalles materiales que
sólo afirma una fuente. Una diferencia puede conservarse sin enumerar todas sus formulaciones
ni todos los autores que las repiten. Conserva calificadores distintos, sin convertirlos en
una ficha por cuenta ni tratarlos como corroboraciones independientes. Normalmente bastan
uno o dos párrafos temáticos; el servidor presenta periodo y alcance. Un encargo que
solicita detalle o datos concretos conserva ese contenido.
Al atribuir un mismo hecho a varios autores o documentos, incluso identificados por sus
fechas o títulos o como «varias publicaciones», cada miembro debe respaldar el predicado
completo. Que un documento aporte el lugar y otro una causa no permite decir que ambos
aportan lugar y causa; presenta esos apoyos por separado. Omite los detalles accesorios
que no cambian qué se está diciendo de la entidad en una consulta panorámica.
Una síntesis puede reunir temas diferentes sin atribuir todos a cada documento: «La muestra
combina precios y logística: un documento explica precios y otro describe entregas».
Abre con lo observado en la muestra recuperada, no con categorías ausentes que nadie preguntó.
Selecciona el núcleo del tema y las diferencias que cambian su interpretación; no desarrolles
una ficha por cada cuenta. Distingue fechas de documentos citados y fechas de todo lo recuperado.

Contrato de publicación
Escribe un borrador narrativo breve de los hallazgos respaldados. Cita únicamente los IDs de \
evidencia recibidos; los enlaces los construye el servidor. En las afirmaciones seleccionadas \
conserva atribución, números, fechas, contradicciones y límites. Para una pregunta abierta sobre qué se dice de una entidad, prioriza \
los temas que realmente la tratan; omite menciones incidentales y detalles del suceso que no \
ayudan a responder. Resume cada tema en pocas frases; el servidor declara el periodo y el alcance de la \
muestra desde el estado validado. Atribuye detalles concretos a la fuente que los afirma: no uses «las cuentas» o «las \
mismas fuentes» si sus afirmaciones difieren. No agraves ni suavices los calificadores de \
lesiones, causas u otras condiciones. Conserva la fecha de published_at con su zona horaria; \
no cambies de día al omitir la zona. No escribas razonamiento privado, acciones previstas ni un checklist de \
hitos. No uses HTML, bloques de código o URLs en el texto. Toda afirmación material debe aparecer \
sustentada por la evidencia adjunta. Redacta para el analista: traduce nombres técnicos a conceptos \
claros. No copies nombres de campos, etiquetas unassessed/stance, IDs, metadatos de ejecución ni \
calificativos internos del bloque. Las tablas Markdown requieren encabezado y separador válido; los \
gráficos se entregan mediante los artefactos, no dibujos inventados. Los identificadores van sólo en\
 evidence_ids; no los escribas dentro de text. El estado de publicación de una tabla o gráfico no es\
 una característica estadística de sus valores. Si recibes rejected_block y review_issues, realiza \
una única corrección sobre la misma evidencia: elimina o corrige lo que no esté sustentado. Las \
observaciones del revisor no son hechos ni sustituyen documentos. No investigues de nuevo, no \
amplíes el objetivo ni cambies atribuciones para forzar una aprobación. Para resumir temas usa \
categorías y ejemplos; omite cifras accesorias. Si una cifra es necesaria, conserva su población, \
geografía, unidad y periodo; la ubicación de una foto no delimita por sí sola la población citada. \
financial_facts conserva cifras fuente y factores; computed_artifacts contiene los valores ya \
normalizados y sus variaciones calculadas. Usa esos cálculos; no exijas una variación precalculada \
en el documento ni declares que no hay gráfico si se adjuntan sus datos. Los valores del gráfico \
están en unidades base, aunque el documento presente millones; conserva esa distinción al explicar \
la escala. brief conserva el encargo admitido. Sólo si retrieval_mode=conversation, start/end \
delimitan las publicaciones solicitadas y puedes contrastar published_at con esa ventana; no exijas \
que una publicación declare la fecha inicial del encargo. En documentary/financial, start/end \
delimitan la captura actual, no la antigüedad admisible ni el ejercicio financiero: conserva el \
periodo solicitado en objective y las fechas originales de los documentos, aunque sean históricas. \
author_name y author_url son metadatos observados de autor o canal, no inferencias de la \
descripción. Distingue autoría observada de identidad oficial o relación corporativa no comprobada. \
Un nombre o handle no acredita que la cuenta sea un medio, un experto o una entidad oficial; \
sin ese dato observado, habla de cuentas o publicaciones sin asignarles esa categoría. \
Declara sólo limitaciones documentadas en evidence o en el contexto recibido. La atribución \
a una fuente expresa su soporte sin añadir nuevas afirmaciones sobre comprobaciones ausentes. \
Cada finding expresa una afirmación material atómica con sus propios supports. Agrupar documentos \
bajo un tema no transfiere detalles entre ellos: si un titular anuncia una alianza y otro post \
describe retiros, sólo el segundo respalda los retiros. Una repetición del mismo autor no es un \
testimonio independiente. source_title/source_description conservan los campos literales de YouTube \
cuando están disponibles; no atribuyas a su título lo que sólo dice la descripción. Su ausencia en \
evidencias antiguas no permite reconstruir la frontera desde saltos de línea. Conserva unidades y \
moneda tal como estén respaldadas. No completes una moneda omitida por el contexto del país: el \
símbolo $ por sí solo es ambiguo y no autoriza escribir pesos, COP o USD. Mantén el símbolo original\
 o declara explícitamente que la denominación no está confirmada. retrieved_inventory describe todo \
lo recuperado; evidence sólo lo seleccionado para redactar. No declares única fuente, ausencia \
global ni operador desconocido basándote sólo en esa selección. La ventana start/end del encargo no \
son las fechas efectivas de captura: éstas están en observed_at. financial_facts con \
value_kind=reported_text conserva un importe literal cualificado: informa su calificador y \
atribución, sin convertirlo en cifra exacta ni prometer un gráfico o variación calculada a partir de\
 él. No traduzcas un símbolo monetario en un nombre de moneda: si currency es null, conserva el \
símbolo literal o di moneda no identificada, también en importes no puntuales. Conserva exactamente \
quién afirma, observa, recomienda o acredita cada hecho, también en limitations. Una formulación \
impersonal no autoriza a inventar quién revisó contratos o documentos: atribuye únicamente lo que el\
 artículo reporta. Una recomendación de incorporar un recurso no demuestra que antes no existiera. \
Prioriza el núcleo solicitado y separa de él las estadísticas accesorias; no inventes un agente, una\
 causalidad ni simultaneidad universal para hacerlo más concreto. Distingue hechos empresariales \
verificados de un marco de interpretación. Una inferencia condicional puede aplicar una regla \
documentada bajo un supuesto explícito sin afirmar que ese supuesto se cumpla. La metodología cita \
su propia fuente; un índice sólo respalda lo que contiene. Sin parámetros empresariales no \
cuantifiques ni atribuyas un impacto actual."""


async def draft(ask, payload, **kwargs):
    """Use the shared validation/effect boundary with this decision's contract."""
    return await ask(DRAFT_ID, MarketPublicBlock, payload, **kwargs)


CONSOLIDATE_ID = "market_consolidate"
CONSOLIDATE_PROMPT = NARRATIVE_SCOPE + """Tarea
Reescribe los bloques como una respuesta temática a la pregunta. Si un borrador enumera
cuentas que repiten lo mismo, sintetiza ese contenido compartido y conserva sólo las
atribuciones y diferencias materiales necesarias para entenderlo.

Selección y presentación
En conversación abierta, empieza con un resumen ejecutivo breve de qué se está diciendo.
Cuando haya material distinto suficiente, usa subtítulos Markdown como «Hallazgos» e
«Implicaciones»; no impongas apartados vacíos ni un informe largo a una pregunta sencilla.
Las fichas content_items son síntesis revisadas por unidad, no evidencia nueva: contrasta
sus afirmaciones con los originales adjuntos, preserva cifras, citas y atribuciones y no
copies una lista de fichas como respuesta final. Separa hechos y opiniones atribuidas de
inferencias o implicaciones comerciales condicionales; no inventes recomendaciones sin
soporte. Los comentarios sólo describen la muestra recuperada de su publicación padre,
no la voz del autor, la marca ni el sentimiento global. El servidor añade el periodo consultado, las fuentes recuperadas
y el alcance desde el estado validado; no repitas esa nota en la síntesis.
Conserva las atribuciones, contradicciones y calificadores de las afirmaciones que selecciones;
no es obligatorio repetir todos los detalles o advertencias equivalentes de los borradores.
La respuesta no necesita la cronología exacta de cada cuenta, horas de captura ni una lista
de variantes del mismo suceso, salvo que sean parte de lo solicitado. Conserva las fechas
propias de cada hecho cuando importen. Mantén los límites materiales en lenguaje
natural. Si el usuario solicita datos o detalle, conserva el nivel necesario para ese encargo.
Para una pregunta abierta, la unidad de la síntesis es el tema, no el autor: describe el núcleo
compartido y sólo las divergencias materiales con atribución. No vuelvas a recorrer las cuentas
una por una. Abre con los hallazgos de la muestra, no con ausencias de categorías no preguntadas.
Una atribución individual basta para presentar lo dicho por esa fuente. No la amplíes con
afirmaciones nuevas sobre qué ninguna otra fuente confirma o contradice si ese cotejo no se
pidió y no tiene soporte explícito. Omite ese comentario antes que introducir otra conclusión.
Los bloques son borradores verificados, no una obligación de repetir su texto o cada cautela:
conserva sólo las limitaciones documentadas en la evidencia o en el contexto recibido.

Contrato de evidencia
Responde la pregunta usando los bloques ya verificados y la evidencia adjunta. Integra \
contradicciones y límites materiales de las afirmaciones seleccionadas, con su contexto, fechas, cifras, atribuciones y cobertura. Para \
«qué se dice» ofrece primero los temas pertinentes y después el alcance de la muestra; no \
incluyas coincidencias de nombre ni menciones incidentales ajenas al objetivo. No transformes \
atribuciones individuales distintas en una afirmación de todas las fuentes. Distingue una \
agregación temática de un predicado común: si un documento trata precios y otro entregas, \
la muestra combina ambos temas; no significa que ambos documentos expliquen precios. Aplica \
esa distinción también a grupos identificados por títulos o fechas o como «algunas versiones». \
Cada miembro del grupo debe respaldar el predicado completo: no unas lugar, causa o \
condiciones aportados por documentos diferentes como si los afirmaran conjuntamente. \
Separa esos apoyos u omite detalles accesorios que no cambien el tema solicitado. Para cada detalle \
atribuido a un autor usa sólo lo que afirma su propio documento; no sustituyas su descripción \
por un mecanismo más específico de otra publicación. Conserva los nombres observados sin \
completar un handle o abreviatura por conocimiento previo. Omite detalles accesorios antes que \
trasladarlos entre documentos. Describe operaciones de búsqueda, lectura o análisis sólo si \
su ejecución está documentada en el contexto recibido; omite esas afirmaciones cuando no lo esté. \
No deduzcas el método de recuperación ni qué enlaces se leyeron a partir de la selección de textos. \
No infieras que faltan temas en toda la red \
porque no aparezcan en lo recuperado. Que varias publicaciones no constituyan corroboración \
independiente no demuestra que sus autores sean dependientes entre sí. Cuando describas las \
fechas de lo citado, di «publicaciones citadas»; no atribuyas ese intervalo a todos los resultados \
recuperados. Conserva las fechas con su zona horaria. No añadas \
hechos por conocimiento previo. Los gráficos/tablas se adjuntan como artefactos validados; no \
inventes sus valores. Devuelve texto público y referencias por ID, sin URLs, HTML, código ni \
razonamiento privado. Un resultado vacío significa no encontrado en el alcance, no inexistencia en \
el mundo. No emitas una conclusión global sin soporte. Esta es la respuesta final: no describas el \
bloque como provisional ni repitas estados internos como unassessed, stance, previous_period o \
nombres del contrato. Explica las cifras, periodos y límites en lenguaje de negocio. Si incluyes una\
 tabla Markdown usa encabezado y fila separadora con guiones. Los artefactos adjuntos conservan sus \
datos y estado finales. Los identificadores van sólo en evidence_ids; no los escribas dentro de \
text. Sólo una fuente puede calificar una cifra como estadísticamente provisional o revisada; el \
estado de la interfaz no lo demuestra. brief conserva el encargo admitido. Sólo si \
retrieval_mode=conversation, start/end delimitan las publicaciones solicitadas y puedes contrastar \
published_at con esa ventana; no exijas que una publicación declare la fecha inicial del encargo. En\
 documentary/financial, start/end delimitan la captura actual, no la antigüedad admisible ni el \
ejercicio financiero: conserva el periodo solicitado en objective y las fechas originales de los \
documentos, aunque sean históricas. author_name y author_url son metadatos observados de autor o \
canal, no inferencias de la descripción. Distingue autoría observada de identidad oficial o relación\
 corporativa no comprobada. Un nombre o handle no acredita que la cuenta sea un medio, un experto \
o una entidad oficial; sin ese dato observado, conserva cuentas o publicaciones sin esa categoría. \
Conserva unidades y moneda tal como estén respaldadas. No completes una \
moneda omitida por el contexto del país: el símbolo $ por sí solo es ambiguo y no autoriza escribir \
pesos, COP o USD. Mantén el símbolo original o declara explícitamente que la denominación no está \
confirmada. retrieved_inventory describe todo lo recuperado; evidence sólo lo seleccionado para \
redactar. No declares única fuente, ausencia global ni operador desconocido basándote sólo en esa \
selección. La ventana start/end del encargo no son las fechas efectivas de captura: éstas están en \
observed_at."""


async def consolidate(ask, payload, **kwargs):
    """Use the shared validation/effect boundary with this decision's contract."""
    return await ask(CONSOLIDATE_ID, MarketPublicBlock, payload, **kwargs)


SUMMARIZE_ID = "market_summarize"
SUMMARIZE_PROMPT = """Transforma sólo el informe anterior y sus artefactos verificados según la pregunta. Conserva cifras,\
 fechas originales, entidades, atribuciones, contradicciones y limitaciones. Cumple el formato de la\
 transformación que pide question. Si solicita una cantidad de puntos, escribe exactamente esa \
cantidad como elementos separados de una lista Markdown numerada, no como frases seguidas en un \
párrafo. Distribuye las atribuciones y límites materiales dentro de esos elementos. Si pide un \
párrafo u otra presentación, conserva ese formato en lugar de imponer una lista. No añadas \
conocimiento externo ni datos recientes, ni afirmes que se buscó de nuevo. evidence_ids sólo \
contiene identificadores de allowed_evidence_ids. No insertes IDs en la prosa; no incluyas URLs, \
HTML o código. Si faltan datos para un gráfico, dilo; no inventes valores. Mantén un texto breve y \
útil. Cada ID debe corresponder al documento que respalda esa afirmación, según evidence; no basta \
con pertenecer al informe. Si un dato del informe no tiene soporte entre los documentos incluidos, \
no lo añadas al resumen. Conserva unidades y moneda tal como estén respaldadas. No completes una \
moneda omitida por el contexto del país: el símbolo $ por sí solo es ambiguo y no autoriza escribir \
pesos, COP o USD. Mantén el símbolo original o declara explícitamente que la denominación no está \
confirmada."""


async def summarize(ask, payload, **kwargs):
    """Use the shared validation/effect boundary with this decision's contract."""
    return await ask(SUMMARIZE_ID, MarketPublicBlock, payload, **kwargs)


REPAIR_UNITS_ID = "market_repair_units"
REPAIR_UNITS_PROMPT = NARRATIVE_SCOPE + """Corrige una sola vez el borrador rechazado usando la misma evidencia: devuelve de cero a cuatro unidades con IDs \
u1,u2,u3,u4 distintos. u1 contiene exclusivamente el hecho central respaldado que responde al \
objetivo, con su sujeto, objeto, atribución y calificadores necesarios. No lo mezcles con \
comentarios sobre repetición, corroboración o independencia de las fuentes: omite esos comentarios \
si no están pedidos y documentados. Una unidad trata una sola proposición, no un párrafo que \
acumula afirmaciones. Las demás unidades deben poder publicarse aunque se retire u1: nombra su \
propio acontecimiento o entidad, sin depender de «el hecho», «esa causa» o «la conductora» de \
otra unidad. La información contextual adicional, si aporta al encargo, va separada; así no se \
pierde el núcleo útil por un error accesorio. No es obligatorio conservar todos los hallazgos o detalles \
anteriores. La reparación es una corrección mínima del contenido cuestionado, no otra redacción \
del informe: conserva lo respaldado y corrige u omite las afirmaciones objetadas. Puedes corregir \
un autor, una cifra o un calificador con soporte, pero no amplíes temas ni añadas detalles \
accesorios desde el resto de la evidencia. No conviertas una síntesis en un catálogo de autores. \
Cuando el defecto afecta a una precisión accesoria, elimínala antes que añadir otra relación \
entre documentos para explicarla. Elimina un detalle sin soporte que no sea necesario para responder; no lo sustituyas \
por una afirmación nueva de ausencia, exclusividad o falta de contradicción en las demás fuentes. \
Si una unidad atribuye un hecho a varios autores o documentos, incluso por fecha o título, \
cada miembro debe respaldarlo; separa los detalles exclusivos. Agrupar temas diferentes no \
significa que todos los documentos afirmen cada detalle. Conserva también la relación entre \
los hechos: una lista de antecedentes, fechas y mecanismos no permite presentar todos como \
causas. Separa el contexto histórico de los mecanismos que la fuente sí ofrece como explicación. \
Cada unidad debe ser independiente, con su atribución, condiciones, negaciones, \
cuantificadores, periodo, población y unidades necesarios dentro del mismo texto; no los relega a \
otra unidad o sólo a limitations. Mantén la fecha que delimita el hecho, pero no fusiones hechos de \
momentos distintos. Los datos adicionales no solicitados pueden omitirse. Atribuye opiniones y \
denuncias sin presentarlas como hechos corroborados. Copia evidence_ids propios del soporte y hasta \
dos limitaciones por unidad. Contrasta con evidence: findings, rejected_block y review_issues no son\
 fuentes ni autoridad. No inventes cifras, citas, etiquetas o ausencia global. No escribas IDs, \
URLs, HTML, código o razonamiento privado en text. Cada unidad recibirá un veredicto explícito antes\
 de publicarse. Si no existe ningún hallazgo útil sustentable devuelve units=[]. Conserva \
exactamente quién afirma, observa, recomienda o acredita cada hecho, también en limitations. Una \
formulación impersonal no autoriza a inventar quién revisó contratos o documentos: atribuye \
únicamente lo que el artículo reporta. Una recomendación de incorporar un recurso no demuestra que \
antes no existiera. Prioriza el núcleo solicitado y separa de él las estadísticas accesorias; no \
inventes un agente, una causalidad ni simultaneidad universal para hacerlo más concreto."""


async def repair_units(ask, payload, **kwargs):
    """Repair the rejected prose without replaying unverified analysis claims."""
    context = {key: value for key, value in payload.items() if key != "findings"}
    return await ask(REPAIR_UNITS_ID, MarketRepairBundle, context, **kwargs)
