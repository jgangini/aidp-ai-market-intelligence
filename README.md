# AIDP AI Market Intelligence

Agente de investigación de mercado para **Oracle AI Data Platform (AIDP)**, construido con LangGraph. Interpreta preguntas de negocio, consulta fuentes pertinentes y entrega hallazgos con referencias y límites de cobertura. Puede investigar empresas, marcas, productos o sectores; no contiene reglas específicas para un cliente.

## Qué hace

- Investiga conversación pública en web, X, Facebook, Instagram y YouTube según las fuentes habilitadas y las restricciones del encargo.
- Utiliza documentos financieros, datos de mercado y World Bank para preguntas financieras o económicas pertinentes.
- Ejecuta ramas por fuente en paralelo, con límites compartidos para capturas y llamadas al modelo.
- Publica progreso por fuente, cápsulas verificadas y una respuesta final mediante streaming.
- Conserva evidencias, solicitudes y efectos externos para diagnóstico, recuperación y seguimiento de una conversación.

Ejemplos: «¿Qué se dice de D1 en Colombia?», «¿Y en Instagram?», «Compara los ingresos reportados de estas empresas». Una fuente habilitada no garantiza resultados: el acceso, el contenido disponible y la calidad de la evidencia determinan la cobertura.

## Cómo funciona

El coordinador normaliza entidad, objetivo, periodo y fuentes. Cada rama captura, analiza y revisa su evidencia. Después, el coordinador consolida los hallazgos admitidos y construye las referencias y la nota de alcance. Una respuesta puede ser parcial cuando faltan fuentes o hallazgos publicables; tener referencias no demuestra exhaustividad ni corrección por sí solo.

El proyecto incluye el agente, su paquete de distribución, una CLI administrativa y hooks de Deploy Studio. Utiliza los clientes de AIDP y Agent Hub; no incorpora portal propio, servidor HTTP ni VM administrativa.

## Componentes

| Ruta | Responsabilidad |
| --- | --- |
| `aidp/workspaces/default/files/agents/market_intelligence.py` | Entrada AIDP del agente. |
| `aidp/workspaces/default/files/agents/core/` | Grafo, decisiones y prompts, conectores, evidencias, memoria y protocolo. |
| `src/ai_market_intelligence/` | Instalación, configuración, mantenimiento y recuperación. |
| `terraform/` | Recursos y formulario de Deploy Studio, con modos de creación y reutilización. |
| `package/lab.json` | Inventario y hashes del paquete canónico. |
| `scripts/` y `tests/` | Desarrollo local, validación y ejemplos de consumo. |

## Despliegue y acceso

Deploy Studio prepara o reutiliza AIDP, almacenamiento y memoria Autonomous, y sus hooks instalan el agente en TEST. La investigación comienza pausada; su activación requiere el binding completo de servicios. Para utilizarlo desde Agent Hub también se publica el endpoint y se verifica su registro en AI Registry. Estas operaciones se describen en [despliegue](docs/deployment.md) y [operación](docs/operations.md).

## Desarrollo

El entorno usa Python 3.11 y `uv`; Node ejecuta las pruebas del protocolo. Terraform se valida por separado.

```powershell
uv sync --locked
uv run aidp-market package-check
```

`package-check` comprueba fuentes e inventario sin utilizar credenciales. Las consultas reales y las operaciones AIDP requieren configuración privada y acceso OCI autorizado.

## Documentación

- [Arquitectura y fuentes](docs/architecture.md)
- [Despliegue con Deploy Studio](docs/deployment.md)
- [Operación, publicación y recuperación](docs/operations.md)
- [Desarrollo y validación](docs/development.md)
- [Evaluación de calidad](docs/quality.md)
- [Distribución del paquete](docs/distribution.md)

Credenciales, estados Terraform, configuraciones del tenant y recibos reales permanecen fuera de Git. La investigación puede consumir servicios de modelo y actores Apify de pago. El agente puede omitir información o equivocarse; las decisiones de negocio requieren revisar sus fuentes y cobertura.
