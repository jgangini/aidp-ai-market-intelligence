# Candidato de separación de Market

Fecha: 2026-11-05. Repositorio privado solicitado: `jgangini/aidp-ai_market-intelligence`.
Fuente de extracción: `3c82617fd298933995e954955841007253cc0985` (Starter Kits v4.0.11).
Requisitos: plan común coordinado y `docs/migration-assessment.md`, corregidos a producto sin portal.

## Implementado

Paquete Python CLI/hooks independiente, 40 assets AIDP/metadata idénticos al corte, ensamblado TEST pausado (SHA-256 `3777e05b9f1536e012bb075de4f07ca83b60b2b92a3d50af70418ddc983816d1`), instalador durable v2, cierre explícito de 36 métodos OCI del origen sin dependencias a otros productos, autorización por dominio/roles efectivos y lock condicional Object Storage. Preflight Apify solo de lectura, 13 perfiles con uno suspendido y ejecución/entitlement no comprobados. Terraform new/existing para AIDP y artefactos, sin VM, tablas ni workflows ficticios.

## Validación integrada del candidato

- Preflight: `scripts/arch-preflight.ps1`, baseline Sentrux del destino antes de código, aprobado; sin reglas locales.
- `scripts/arch-postflight.ps1`: 1230 pruebas Python aprobadas, una advertencia de deprecación pendiente de LangChain; package-check verifica 36 archivos runtime y fuente pausada compilable. Incluye runtime/instalador, identidad/grupos/herencia, revocación al Resume, locks, Apify y contratos reales del runner.
- Protocolo JavaScript: 8 aprobadas. Terraform fmt/validate y cinco pruebas nativas con mocks aprobadas, sin apply. Gate de límites/secrets sobre fuente staged aprobado; `.oci` no se leyó ni incluyó.
- Parser Pydantic real de `D:/dev/codex-cloudtechnext/app/backend/app/services/deployment_source.py`: manifiesto exacto aprobado. Su `_extract_selected` aceptó el ZIP de fuente staged (71 archivos seleccionados), entrypoints e include_paths existentes y cero `.oci`. Esta verificación es read-only, no modifica CloudTechNext ni acredita ejecución del runner contra OCI.
- Wheel/sdist construidas; RECORD, 40 assets y CLI desde wheel fuera del checkout verificados. Ajuste de empaquetado ZIP estándar evita MAX_PATH de Windows; no cambia fuentes del agente.
- Graphify: no configurado en el destino; no inicializado.
- Sentrux: sin reglas locales; gate final FALLÓ frente al repositorio vacío inicial. Calidad 0.8125 → 0.6633, coupling 0 → 0.73 y funciones complejas 0 → 6; ciclos 0 → 0 y god files 0 → 0. El postflight termina con error en esa puerta. Se conserva la baseline original; no se ha reseteado ni aceptado una nueva para aparentar aprobación. La importación de un runtime completo frente a un destino vacío explica la comparación, pero no constituye un gate aprobado: el coordinador debe revisar el tradeoff de extracción y la aceptación de arquitectura antes de release.

## Límites y siguiente frontera

No se ejecutó OCI, inferencia ni actores reales. El selector `oci_aidp_administrators` fue integrado por el coordinador y el manifiesto pasó su parser real; la ejecución nativa y el entorno de dependencias de hooks siguen sin aceptar. No hay update de paquete diferente, rollback automático nativo, PROD, delete ni transferencia de state implementados; se rechazan sustituciones en lugar de simularlas. El lock solo excluye procesos con el mismo bucket: configuraciones con buckets distintos sobre un workspace requieren serialización global del coordinador, aún sin binding CAS nativo entre buckets. Ver `docs/operations.md` para recuperación de locks abandonados y alcance de Resume. No se publica release final.

Correcciones verificadas contra el consumidor real: el preflight recibe un ID transitorio y compartment string; no los trata como UUID durable ni objeto. El operation_id se deriva con UUID5 del ID completo `dep_<16hex>` de post-apply. Terraform recibe solo el suffix ya soportado y produce un SHA256 `deployment_binding` de tenancy/región/compartment/suffix/dominio/admin; no inventa un deployment UUID desde el preflight. Los clientes SDK usan la región seleccionada. La identidad firmante sigue siendo la del operador; la selección no la suplanta.

Codex: GPT-6.1 Sol High -- integración de contratos y recuperación. [Referencia](https://learn.chatgpt.com/docs/models)

El commit candidato se registra al cerrar validación; su SHA no puede incluirse dentro del mismo commit. Consultar `git rev-parse HEAD` y la entrega del chat. El origen y otros destinos permanecen de solo lectura para este trabajo.
