# Candidato de separación de Market

Fecha: 2026-11-05. Repositorio privado solicitado: `jgangini/aidp-ai_market-intelligence`.
Fuente de extracción: `3c82617fd298933995e954955841007253cc0985` (Starter Kits v4.0.11).
Requisitos: plan común coordinado y `docs/migration-assessment.md`, corregidos a producto sin portal.

Candidato intermedio publicado en `main`: `31815471c6f7b247ca9d51178ebe9fce0b45fb10`. [CI GitHub aprobada](https://github.com/jgangini/aidp-ai_market-intelligence/actions/runs/42140400696). El remoto fue verificado PRIVATE. Este SHA cierra la extracción inicial, no todo el alcance solicitado.

Candidato base con lifecycle completo: `4f5133f8e918e7335da4a356735490fc72e1c55c`, publicado con [CI aprobada](https://github.com/jgangini/aidp-ai_market-intelligence/actions/runs/42142255290). Código final con guard de infraestructura de mantenimiento: `9755a54d8d018f5c42bba253d01f94059066683e`; las 1325 pruebas y la wheel final de abajo corresponden a ese árbol de código. Este cierre documental tiene un commit posterior sin cambios de código; la entrega del chat identifica HEAD y CI finales. No se ha publicado release final.

Continuación implementada: update/redeploy y recuperación del candidato nativo anterior mediante journal durable y respaldos exactos, preservando identidades y sesiones. CLI y Deploy Studio exponen las acciones mediante el mismo paquete Python. La validación integrada se detalla abajo; aceptación OCI y gate de arquitectura siguen pendientes.

## Implementado

Paquete Python CLI/hooks independiente, 40 assets AIDP/metadata idénticos al corte, ensamblado TEST pausado (SHA-256 `3777e05b9f1536e012bb075de4f07ca83b60b2b92a3d50af70418ddc983816d1`), instalador durable v2, cierre explícito de 36 métodos OCI del origen sin dependencias a otros productos, autorización por dominio/roles efectivos y lock condicional Object Storage. Preflight Apify solo de lectura, 13 perfiles con uno suspendido y ejecución/entitlement no comprobados. Terraform new/existing para AIDP y artefactos, sin VM, tablas ni workflows ficticios.

Mantenimiento: backups exactos inmutables de archivos/dependencias/descriptor antes de sobrescribir; journal validado como dato no confiable; mismo agente/compute/credenciales/UUID de instalación. La operación de mantenimiento tiene UUID y hashes propios. Se reconcilian escrituras parciales y refresh incierto sin duplicar envíos; compute STOPPED usa clave y retry scope registrados. Drift de propietario, recursos, sesión o revisión impide el commit. Un fallo confirmado restaura y verifica el anterior; `rolled_back` conserva código de fallo para el update solicitado. Rollback explícito funciona sin Apify/token ni metadata del modelo y sigue exigiendo identidad/propiedad. No se borran datos/checkpoints ni se crean/resetan sesiones. Las 43 pruebas de la máquina y 45 de integración forman parte de la suite completa.

## Validación integrada del candidato

- Preflight: `scripts/arch-preflight.ps1`, baseline Sentrux del destino antes de código, aprobado; sin reglas locales.
- `scripts/arch-postflight.ps1`: 1325 pruebas Python aprobadas, una advertencia de deprecación pendiente de LangChain; package-check verifica 36 archivos runtime y fuente pausada compilable. Incluye runtime/instalador, identidad/grupos/herencia, revocación al Resume, locks, Apify, mantenimiento y contratos reales del runner. Log privado de esta ejecución: `.local/postflight-final-guards.log`.
- Protocolo JavaScript: 8 aprobadas. Terraform fmt/validate y cinco pruebas nativas con mocks aprobadas, sin apply. Gate de límites/secrets sobre fuente staged aprobado; `.oci` no se leyó ni incluyó.
- Parser Pydantic real de `D:/dev/codex-cloudtechnext/app/backend/app/services/deployment_source.py`: manifiesto exacto aprobado. Su `_extract_selected` aceptó el ZIP de fuente staged (72 archivos seleccionados), acciones de mantenimiento, entrypoints e include_paths existentes y cero `.oci`. Esta verificación es read-only, no modifica CloudTechNext ni acredita ejecución del runner contra OCI.
- Wheel/sdist finales construidas; RECORD completo, 40 assets, import de mantenimiento y CLI desde wheel fuera del checkout verificados. SHA-256 de wheel local final: `7f060284d36a3d152bed2c1adf80459cd5e1dc61e8bf4d34e3109af26d03d5ea`. Ajuste de empaquetado ZIP estándar evita MAX_PATH de Windows; no cambia fuentes del agente. No se publicó una release ni un paquete en registro.
- Graphify: no configurado en el destino; no inicializado.
- Sentrux: sin reglas locales; gate final FALLÓ frente al repositorio vacío inicial. Calidad 0.8125 → 0.6619, coupling 0 → 0.74 y funciones complejas 0 → 11; ciclos 0 → 0 y god files 0 → 0. El postflight termina con error en esa puerta. Se conserva la baseline original; no se ha reseteado ni aceptado una nueva para aparentar aprobación. La importación de un runtime completo frente a un destino vacío explica la comparación, pero no constituye un gate aprobado: el coordinador debe revisar el tradeoff de extracción y la aceptación de arquitectura antes de release.

## Límites y siguiente frontera

No se ejecutó OCI, inferencia ni actores reales. El selector `oci_aidp_administrators` fue integrado por el coordinador y el manifiesto pasó su parser real; la ejecución nativa y el entorno de dependencias de hooks siguen sin aceptar. Update/redeploy/rollback están implementados y probados con servicios simulados, sin atribuirles aceptación nativa. PROD, delete y transferencia de state quedan fuera del candidato. El lock solo excluye procesos con el mismo bucket: configuraciones con buckets distintos sobre un workspace requieren serialización global del coordinador, aún sin binding CAS nativo entre buckets. Ver `docs/operations.md` para recuperación de locks abandonados y alcance de Resume. No se publica release final.

Correcciones verificadas contra el consumidor real: el preflight recibe un ID transitorio y compartment string; no los trata como UUID durable ni objeto. El operation_id se deriva con UUID5 del ID completo `dep_<16hex>` de post-apply. Terraform recibe solo el suffix ya soportado y produce un SHA256 `deployment_binding` de tenancy/región/compartment/suffix/dominio/admin; no inventa un deployment UUID desde el preflight. Los clientes SDK usan la región seleccionada. La identidad firmante sigue siendo la del operador; la selección no la suplanta.

Guard de coste/propiedad: una nueva solicitud de mantenimiento en el runner actual debe referenciar AIDP y bucket existentes y un recibo ready del administrador seleccionado. Se rechazan new/new y combinaciones que creen infraestructura antes de leer credenciales o llamar a OCI. El stack original conserva propiedad, mientras la nueva solicitud solo referencia recursos. Reutilizar directamente un stack previo sin cambiar su propiedad requiere el flujo genérico coordinado; el hook acepta sus outputs verificados y no cambia identidades.

Codex: GPT-6.1 Sol High -- integración de contratos y recuperación. [Referencia](https://learn.chatgpt.com/docs/models)

El SHA de código y sus comprobaciones quedan registrados arriba; consultar `git rev-parse HEAD` para el cierre documental posterior y la entrega del chat para el estado final de CI. El origen y otros destinos permanecen de solo lectura para este trabajo.
