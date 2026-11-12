# Contratos de informes de mercado

Estos esquemas describen extensiones opcionales del contrato genérico
`response.report/1.0` de Agent Desk. El productor implementa esa entrega cuando
recibe `delivery.mode="report"`; sin esa opción mantiene la respuesta habitual.
La implementación y sus regresiones locales no acreditan por sí solas que una
instalación AIDP esté sirviendo el mismo candidato ni su calibración general.

El núcleo del informe pertenece a Agent Desk:
`contracts/report-v1.schema.json` en su repositorio. No se copia aquí ni se amplía
con campos de mercado obligatorios. La compatibilidad con ese núcleo y su parser
requiere una aceptación conjunta; estos checks no la sustituyen.

## Ubicación y versiones

| Clave en `response.report.extensions` | Esquema del valor |
| --- | --- |
| `market.platform_metrics` | [market-platform-metrics-v1.schema.json](extensions/market-platform-metrics-v1.schema.json) |
| `market.sentiment` | [market-sentiment-v1.schema.json](extensions/market-sentiment-v1.schema.json) |

Cada valor declara `schema_version: "1.0"`. El contenedor puede incluir otras
extensiones de otros dominios: validar sólo la clave cuyo esquema se soporte. Las
desconocidas permanecen opacas y no se presentan como datos validados. Ninguna de
estas capacidades se deduce del nombre del agente ni de `content_items/1.0`.

Los esquemas son Draft 2020-12, autocontenidos, sin referencias que requieran
descargar recursos externos. Las versiones desconocidas y los campos adicionales
no son válidos dentro de una extensión reconocida. Un cliente sin soporte conserva
el Markdown, referencias, estado y limitaciones de la respuesta.

## Métricas: qué se cuenta

`query_operations` cuenta intentos registrados de búsqueda/consulta en el ledger:
`started` incluye los rechazados antes de crear un run; `completed` cuenta consultas
con resultado final verificado, incluido un vacío válido; `failed` cuenta rechazos,
ejecuciones fallidas o resultados no recuperables. Son la misma población, por lo
que `completed + failed <= started`; no es un contador de runs creados. No cuenta
sondeos, lecturas del dataset ni llamadas al modelo. Un inicio
o resultado incierto conserva `null` donde no se pueda verificar la contabilidad.
Los contadores proceden del ledger, no de una estimación del modelo. Para futuras
fuentes sin una operación de búsqueda diferenciada, sus contadores serán `null`
con una limitación, sin reinterpretar otra operación para llenar los campos.

Cada observación identifica `unit` (`post`, `comment`, `page`) y población
`retained_evidence`: unidades únicas retenidas después de filtrar entidad/periodo.
No es el número bruto de filas del proveedor, enlaces citados, personas, volumen
total de una plataforma ni corroboraciones independientes. Se distinguen
`retained_count` y `analyzed_count`, con sus IDs explícitos; sólo una unidad con
análisis admitido forma parte de `analyzed_evidence_ids`.

`empty` es una observación válida con cero unidades; `not_collected` conserva
contadores desconocidos y listas vacías. Fallos, pendientes y cobertura parcial
son estados distintos. Una publicación recuperada sin comentarios no demuestra
que no existan comentarios. La trazabilidad y deduplicación deben conservar el
padre original de cada comentario.

## Sentimiento: criterio y denominador

Cada grupo fija plataforma, base (`post_text` o `comment_text`), objetivo y aspecto.
Conserva unidades elegibles, clasificadas y no clasificadas y las anotaciones que
permiten recomputar las cuatro categorías: positiva, negativa, neutral y mixta.
`unclassified` representa abstención/ambigüedad, no neutralidad; exige una razón.
Una etiqueta clasificada exige un fragmento original que la respalde.

El método adapta la separación de objetivo/aspecto y polaridad descrita por
[SemEval-2014 ABSA](https://alt.qcri.org/semeval2014/task4/). Esa referencia no
certifica este agente ni calibra redes en español. Esta propuesta sólo admite
`calibration_status: "not_calibrated"`: antes de admitir otra declaración habrá
que definir su alcance, evidencia de calibración y controles. Los ejemplos no
demuestran precisión del modelo.

No se transmite una confianza autodeclarada ni un porcentaje de aprobación. Una
proporción futura debe declarar su denominador y conservar los no clasificados;
con cero clasificados es indefinida, nunca cero por defecto. No sumar aspectos
distintos como si fuesen publicaciones distintas, ni posts y comentarios como
personas. Los resultados describen la muestra, no a la población del mercado.

## Límites de validación y fixtures

Las cinco muestras de [fixtures/reports](fixtures/reports) son **sintéticas** y
contienen fragmentos de extensiones y evidencia de prueba, no informes reales ni
el núcleo completo de Desk:

- `market-empty`: búsqueda vacía y comentarios no recopilados.
- `market-posts`: una publicación analizada, sin comentarios recopilados.
- `market-posts-comments`: misma identidad/versionado de plantilla; posteriormente
  hay comentarios y sus grupos se mantienen separados de la publicación.
- `market-partial`: una fuente aporta texto y otra falla, sin convertir el fallo
  en cero resultados.
- `market-ambiguous`: una unidad retenida sin polaridad determinable.

`template_version` en estos fixtures es un entero y se corresponde con
`report.template.version` del núcleo de Desk. Una muestra vacía no redefine la
plantilla ni sus reglas de visibilidad y no cancela la programación.

Ejecutar:

```powershell
uv run --locked pytest tests/test_report_extension_contracts.py
```

JSON Schema valida forma, tipos, rangos, versiones y algunas contradicciones de
estado. **No valida** por sí solo sumas entre campos, unicidad por propiedad,
pertenencia a la solicitud, relación padre/comentario, literalidad ni corrección
semántica. `core/report_analysis.py` deriva los conteos de evidencia propia,
valida IDs, padres, objetivo y citas literales de propuestas, y sólo publica
etiquetas admitidas por una revisión independiente del modelo. Una etiqueta o
justificación rechazada se sustituye por abstención segura. Revisar con el mismo
modelo no certifica precisión ni reemplaza calibración. Los tests de fixtures
auditan sus datos sintéticos y tampoco constituyen una evaluación del modelo.

El núcleo de Desk admite `source_request_id` opcional para identificar la
investigación transformada y `period.bounds` opcional con `[start,end)` o
`[start,end]`; omitir `bounds` significa semiabierto. El filtro nativo actual es
cerrado en ambos extremos: el adaptador declara `[start,end]`, sin cambiar
timestamps ni sumar segundos. El estado se conserva en `response.status`, y la
procedencia en los metadatos; el Markdown presenta hallazgos, periodo de publicaciones
y límites materiales sin contadores ni estados de ejecución. Las referencias de
las extensiones conservan su función de auditoría y no se asignan automáticamente
al soporte del resumen. `report.limitations` describe el contenido publicado;
`response.limitations` conserva también observaciones operativas. Una
transformación utiliza el brief y las fechas originales, no una nueva ventana.

Se conservan las fronteras de arquitectura, investigación, streaming y aceptación
descritas en [arquitectura](../docs/architecture.md), [operaciones](../docs/operations.md)
y [calidad](../docs/quality.md). El catálogo A2UI, la ejecución de tareas y la
presentación del núcleo pertenecen a Desk. Incorporar estos ejemplos no acredita
sentimiento calibrado, integración A2UI ni publicación en AIDP. La aceptación
requiere además el resultado del consumidor y el candidato servido verificado.
