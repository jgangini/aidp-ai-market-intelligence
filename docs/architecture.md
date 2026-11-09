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

## Límites

Los verificadores pueden rechazar contenido respaldado o aceptar una atribución demasiado amplia. El modelo puede confundir metadatos, combinar detalles de fuentes distintas o emitir citas no literales. Las validaciones estructurales, la revisión factual y la evaluación comparativa son controles complementarios; ninguno garantiza precisión universal.
