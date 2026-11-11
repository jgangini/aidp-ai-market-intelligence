# Desarrollo y validación

## Entorno

Utilizar Python 3.11 y las dependencias fijadas en `uv.lock`:

```powershell
uv sync --locked
uv run aidp-market package-check
```

No editar candidatos extraídos como si fueran otra fuente canónica. Modificar `aidp/workspaces/default/files/agents/` y mantener los hashes del paquete coherentes. Los bytes y finales de línea forman parte de su identidad.

El entorno de hooks y el AI Compute son entornos distintos. Comprobar el perfil completo observado del destino antes de declarar compatibilidad; no forzar una versión de LangGraph basándose sólo en la fecha de una guía ni instalar dependencias durante una invocación.

## Iteración con el plugin

AIDP Agent Flow LangGraph aporta las operaciones `doctor`, `runtime`, `test`, `quality`, `campaign`, `native` y `registry`. El plugin se instala por separado; no está vendoreado ni se descarga implícitamente al instalar el producto. Usar su ayuda y guías para la revisión instalada.

El factory de `scripts/market_intake.py` permite recorrer interpretación y planificación. `tests/runtime/market_fixture_factory.py` aporta el factory de captura sellada y `scripts/market_frozen.py` prepara y ejecuta esas pruebas con el grafo del proyecto. La configuración local `aidp-agent.toml`, la campaña y los resultados `.aidp-local/` están excluidos de Git.

Mantener el ejecutor, modelo, captura y reloj fijos durante una medición. Registrar hipótesis y efectos, conservar fallos y conciliar intentos inciertos antes de repetir. Una nueva revisión invalida sólo la evidencia que depende de sus cambios; no convierte ensayos anteriores en resultados del candidato nuevo.

## Conexión OpenAI

El binding selecciona `oci_iam` (esquema 1 existente) u `openai` (esquema 2).
OpenAI usa exclusivamente `https://api.openai.com/v1/chat/completions`, con una
API key del servicio OpenAI; una clave de OCI Generative AI no sirve para esa conexión.
El runtime nativo y el harness local comparten `OpenAIModel`.

Antes de inferencia, ambas factories locales verifican que TOML, binding congelado y
`core/runtime.json` del candidato declaren la misma selección de proveedor/modelo,
endpoint y referencia de credencial, junto con región y compartimento. Una diferencia
se rechaza antes de crear el adaptador; una medición congelada no adopta otro modelo
silenciosamente. Véase [configuración e identidad](operations.md#configuración-e-identidad).

En AIDP, la clave se resuelve desde Credential Store: `AiMarketIntelligenceOpenAI`,
clave `api_key`. En pruebas locales se inyecta sólo en el entorno del proceso como
`MARKET_LLM_API_KEY`, desde el gestor de secretos del operador. Nunca incluirla en
TOML, bindings, fixtures, comandos literales o archivos versionados. La identidad
OCI sigue proporcionando acceso a AIDP y al almacenamiento.

Las decisiones usan JSON Schema estricto, `max_completion_tokens` y `store=false`.
Se omite temperatura para admitir modelos de razonamiento sin enviar un parámetro
incompatible. La adaptación del esquema hace obligatorios todos los campos,
convierte constantes en enumeraciones y deja las restricciones de longitud de texto
y unicidad a los validadores originales del workflow. No modifica el contrato de
dominio ni sus decisiones. Rechaza respuestas truncadas, negativas, llamadas a
herramientas inesperadas y cuerpos superiores a 1 MiB. No añade retries de transporte
ni sigue redirecciones; conserva la admisión y observaciones por intento.
El streaming público de progreso y hallazgos sigue perteneciendo al workflow.

OpenAI sólo admite inicialmente `guardrails_config={"policies":[]}`; rechaza
políticas OCI configuradas cuya equivalencia no está implementada. No transfiere
`reasoning_effort` de OCI. Listar modelos verifica el catálogo visible a la clave,
pero no acredita cuota de inferencia ni calidad. Antes de publicar se requiere una
llamada focal y aceptación en TEST con el modelo seleccionado.

Referencias: [modelos de OpenAI](https://developers.openai.com/api/reference/resources/models/methods/list),
[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
y [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## Throttling local

`core/model_bridge.py` permite hasta tres intentos para un 429 inequívoco después del envío HTTP y con recorder local. Cada intento utiliza la admisión normal, un UUID y una observación propios. El SDK mantiene sus retries desactivados. El backoff es cancelable, con jitter y esperas de 1–2 y 2–3 segundos.

Timeout, 5xx, formato inválido, cancelación, errores previos al envío y estados ambiguos no activan ese retry. Un 429 observado no demuestra coste cero. Si se consume el presupuesto durante la espera, la siguiente solicitud sigue sujeta a admisión. Esta política es del harness local; el runtime AIDP conserva su journal y tratamiento de incertidumbre y no hereda estos reintentos.

## Validación del repositorio

```powershell
./scripts/arch-preflight.ps1
./scripts/arch-postflight.ps1
```

El postflight ejecuta pruebas Python, package-check, protocolo Node, formato/validate/tests Terraform y comprobaciones de límites y secretos, seguido del gate Sentrux. Las pruebas automáticas usan fixtures sin efectos externos. Las consultas reales se ejecutan por separado, con autorización y contabilidad.

Terraform se valida con el contrato 1.5.7 de Resource Manager; los mocks necesitan la versión moderna indicada en CI. Los proveedores deben estar inicializados. La CI también construye la distribución Python.

Sentrux usa una baseline conservada e inventarios de entrada con hashes. Los scripts incluyen fuentes nuevas mediante un índice Git temporal y comprueban que el índice compartido no cambie. `arch-scan.ps1` ejecuta tanto el gate de regresión como `sentrux check .`; un fallo en cualquiera bloquea el postflight. No reemplazar la baseline para obtener un pase.

Antes de escanear, el flujo exige la política y valida `.sentrux/rules.toml` contra `.sentrux/rules.schema.json` mediante `scripts/check_repository.py --rules-only`. Rechaza claves desconocidas, tipos incompatibles con TOML/Rust, números no finitos y políticas sin reglas activas. Los contadores requieren enteros TOML: `0.0` no equivale a `0` para este contrato. Las puntuaciones normalizadas usan el intervalo 0–1. Así se evita que Sentrux ignore silenciosamente una regla mal escrita. El mismo validador se usa en el comprobador de CI, y el inventario conserva los hashes de la política y su esquema junto al de la baseline.

Las seis reglas de `.sentrux/rules.toml`, verificadas con Sentrux 0.5.7, exigen:

- Cero ciclos de dependencias y ningún archivo con fan-out superior a 15, según la métrica del motor.
- El agente portable no puede importar la CLI administrativa, los hooks de Deploy Studio ni scripts de desarrollo.
- El paquete administrativo `src/` no puede importar `scripts/`.

La frontera completa entre el agente y `src/` se comprueba también en `scripts/check_repository.py` mediante el AST de Python, incluidos imports dentro de funciones. Ese control utiliza nombres de módulos completos: rechaza `ai_market_intelligence`, `src` y `scripts` desde el agente, y permite el módulo externo `oracle_memory_clients.client`. No ejecuta los imports ni necesita que sus dependencias estén instaladas. El mismo control ya forma parte de CI.

Sentrux opera sobre imports resueltos y excluye pruebas y almacenamiento privado mediante `.sentruxignore`. Su resolución puede confundir el módulo externo `oracle_memory_clients.client` con un archivo administrativo local de igual nombre. Las fronteras explícitas y la comprobación AST separan ambos namespaces sin excluir código del escaneo. Ninguno de los dos mecanismos acredita dependencias cargadas mediante imports dinámicos.

No se configuran capas: el [motor de Sentrux 0.5.7](https://github.com/sentrux/sentrux/blob/f36da08a53e7f06c5b6aec33eb05816b371000ea/sentrux-core/src/metrics/rules/mod.rs) aplica su comparación de órdenes en sentido contrario al descrito en la [guía oficial](https://sentrux.dev/docs/rules-engine/). Se usan fronteras explícitas probadas con el binario instalado. La complejidad y el acoplamiento existentes siguen bajo el gate de regresión; estas reglas no certifican que esa deuda haya desaparecido ni añaden umbrales elegidos sólo para aprobar.

`tests/test_sentrux_rules.py` ejecuta casos permitidos y violaciones deliberadas de las fronteras, ciclos y fan-out en repositorios temporales aislados. Se omite si no está instalada la CLI; el postflight local exige Sentrux. La CI actual ejecuta el comprobador de imports Python, pero no instala ni ejecuta el motor Sentrux. Las pruebas funcionales y de calidad del agente siguen siendo comprobaciones independientes.

El diagnóstico de complejidad puede ejecutarse sobre una copia aislada de las fuentes con hashes, usando el umbral de complejidad alta del motor. Esa configuración diagnóstica no sustituye las reglas del proyecto ni constituye una nueva baseline. Un resultado señala funciones que conviene revisar; no demuestra por sí solo un defecto funcional ni justifica alterar contratos de recuperación para reducir una métrica.

## Navegación del código

Cuando exista `graphify-out/`, consultar `GRAPH_REPORT.md` y `wiki/index.md` antes de recorrer relaciones. Usar `rg` para símbolos exactos. Para actualizar el grafo y su wiki existentes:

```powershell
graphify update .
graphify export wiki
```

El índice es local y excluye credenciales, estado privado y dependencias. No crear índices globales. El soporte Terraform depende de un extractor HCL instalado; si falta, consultar esas fuentes directamente. Graphify orienta relaciones y Sentrux detecta regresiones estructurales; ninguno reemplaza pruebas funcionales o evaluación factual.
