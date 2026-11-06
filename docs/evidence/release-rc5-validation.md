# Registro de publicación y validación de rc.5

Verificado el 2026-11-06 mediante la API pública de GitHub y descarga de los cinco assets de [v0.1.0-rc.5](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.5). La publicación fue realizada por el coordinador; este cierre verificó su resultado y actualizó documentación, sin volver a publicar.

| Dato | Valor verificado |
| --- | --- |
| Repositorio | `jgangini/aidp-ai_market-intelligence`, público |
| Release ID | `436600090` |
| Tag | `v0.1.0-rc.5` |
| Commit del tag y assets | `ce5ae002995b9406e509d427603b8716b5cca63f` |
| Estado GitHub | `draft=false`, `prerelease=true`, `immutable=true` |
| Distribución Python / paquete AIDP | `0.1.0` / `2.0.1` |
| Schema del formulario Deploy Studio | `2` |
| Superficie nativa | TEST, investigación pausada |

## Assets comprobados

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `aidp-ai-market-intelligence-release.json` | 10220 | `3218faeb932ee83a35827792a5cda7d92b9fe25de30d729181282b6c45b7764b` |
| `aidp-ai-market-intelligence-source.zip` | 500787 | `19b9808ec8fde876a6f501a061d82c7647d876b235ed179c945eb75c35df9611` |
| `aidp_ai_market_intelligence-0.1.0-py3-none-any.whl` | 186855 | `10ac028d2ca0bd8ef46e245fa16f8f16c628aa2637339405ea4e834e7e3da21b` |
| `aidp_ai_market_intelligence-0.1.0.tar.gz` | 180476 | `dd18a5f335d06cceb217133412546e7d34269a82f528f4fe27e04f7446a092f8` |
| `SHA256SUMS` | 436 | `bc79b39a042d27cce5625dcb00ef539f83dfcc9d82ae2571cee75dc5885201a8` |

Los digests y tamaños públicos coinciden con los archivos preparados localmente. La descarga posterior es idéntica byte por byte para los cinco assets; se verificaron las cuatro entradas de `SHA256SUMS`. La release y su tag permanecen fijados al SHA anterior. Los cambios documentales posteriores del PR no cambian la fuente ni los assets inmutables de rc.5.

El manifiesto de release usa schema 1 para describir distribución y evidencia; el manifiesto del formulario usa schema 2. Son contratos independientes, al igual que el contexto de hooks, que conserva su versión 1.

## Comprobaciones del candidato publicado

[CI del PR](https://github.com/jgangini/aidp-ai_market-intelligence/actions/runs/42224569177) y [CI push](https://github.com/jgangini/aidp-ai_market-intelligence/actions/runs/42224561805) aprobadas para el SHA exacto: Python, protocolo y Terraform 1.5.7/1.15.6. Los mocks se ejecutan con 1.15.6.

El postflight previo de ese candidato aprobó 1384 pruebas Python, ocho de protocolo y once Terraform mocks; terminó con error por el gate Sentrux heredado. No se repitieron las suites en este cierre documental. Sentrux no tiene reglas configuradas; su baseline vacía original sigue preservada. Calidad 0.6621, coupling 0.73 y once funciones complejas, con cero ciclos/god files; sin variación frente al candidato previo. Esto no es una aprobación arquitectónica.

La validación previa del ZIP Git exacto comprobó RECORD, 40 assets canónicos intactos, CLI aislada y 313 payloads con patrones de credenciales. El parser/extractor real de CTN aceptó 74 archivos y 890574 bytes. Schema 2 fue aceptado y schema 1 rechazado. Graphify existente se actualizó, sin incluir sus salidas en Git ni assets; faltaba el parser HCL local. Estos resultados se conservan en el manifiesto publicado y la descarga idéntica vincula la evidencia con los archivos públicos.

## Contrato y límites

`form.groups` define grupos e items ordenados, con credenciales primero y todos los campos/archivos referenciados una vez. La región ocupa una fila completa antes de los controles de compartment. ADMIN declara generador solo cuando `autonomous_database_mode=new`; Apify no declara generador. Se requiere CTN con schema 2 y `generate_password_when`; los formularios anteriores no tienen fallback. rc.1–rc.4 siguen inmutables y conservan su contrato anterior.

Terraform mínimo >=1.5.7 y los selectores workspace/bucket/Autonomous forman parte de esta candidata. Las definiciones de instalación, recursos Terraform, memoria y assets AIDP no cambiaron durante la migración del formulario respecto del candidato revisado.

El [PR borrador #1](https://github.com/jgangini/aidp-ai_market-intelligence/pull/1) conserva los cambios para revisión; la publicación no implica merge a main. No se ejecutaron OCI, apply, inferencia, Apify real, Browser ni Docker aquí. El coordinador publica el motor CTN antes de las pruebas OCI. Siguen pendientes aceptación nativa de permisos/compute, `vectorDbId` y continuidad/aislamiento de checkpoints, además de revisión Browser. Ver [despliegue](../deployment.md), [operaciones](../operations.md) y [distribución](../release-handoff.md).
