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

Sentrux usa una baseline conservada e inventarios de entrada con hashes. Si no hay reglas, informar sólo el gate de regresión; no inventar reglas ni redefinir la baseline para obtener un pase. Los scripts incluyen fuentes nuevas mediante un índice Git temporal y comprueban que el índice compartido no cambie.

## Navegación del código

Cuando exista `graphify-out/`, consultar `GRAPH_REPORT.md` y `wiki/index.md` antes de recorrer relaciones. Usar `rg` para símbolos exactos. Para actualizar el grafo y su wiki existentes:

```powershell
graphify update .
graphify export wiki
```

El índice es local y excluye credenciales, estado privado y dependencias. No crear índices globales. El soporte Terraform depende de un extractor HCL instalado; si falta, consultar esas fuentes directamente. Graphify orienta relaciones y Sentrux detecta regresiones estructurales; ninguno reemplaza pruebas funcionales o evaluación factual.
