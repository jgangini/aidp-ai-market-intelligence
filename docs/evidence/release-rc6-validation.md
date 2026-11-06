# Registro de publicación y validación de rc.6

Verificado el 2026-11-06 mediante API pública de GitHub y descarga de los cinco assets de [v0.1.0-rc.6](https://github.com/jgangini/aidp-ai_market-intelligence/releases/tag/v0.1.0-rc.6). El coordinador publicó esta candidata tras aprobación humana explícita; este cierre solo documenta y verifica, sin volver a publicar.

| Dato | Valor verificado |
| --- | --- |
| Repositorio | `jgangini/aidp-ai_market-intelligence`, público |
| Release ID | `436769686` |
| Tag | `v0.1.0-rc.6` |
| Commit del tag y assets | `93102dfb76da2d0025143a36ecdcf274f36e6e63` |
| Estado GitHub | `draft=false`, `prerelease=true`, `immutable=true` |
| Distribución Python / paquete AIDP | `0.1.0` / `2.0.1` |
| Schema del formulario | `2`, sin fallback |
| Superficie | TEST, investigación pausada |

## Assets comprobados

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `aidp-ai-market-intelligence-release.json` | 11834 | `e6cb8326780b58868eaabdb43187f083742a345860c9177fd1c253d3427ffe5f` |
| `aidp-ai-market-intelligence-source.zip` | 505415 | `6e4a30bb924f242e1f0f90d644fa1286a38495b06b90ffde5043f5b09fe34a40` |
| `aidp_ai_market_intelligence-0.1.0-py3-none-any.whl` | 186855 | `96436f5c104be180af35a6bee1c469a3ac0e8a2e8fbe029d06bf43c446a28e23` |
| `aidp_ai_market_intelligence-0.1.0.tar.gz` | 180930 | `520c195b34b8775c1b9fe1967410c1e161e738a4d2d6b29da8e0935a80f16bea` |
| `SHA256SUMS` | 436 | `8b3785d1d9603f8cd01cfb879acc8146a89aa6371dc6160773e8f60c0795ff69` |

Los cinco archivos descargados son idénticos byte por byte a los preparados. Tamaños/digests públicos y las cuatro entradas de `SHA256SUMS` coinciden. Tag y assets permanecen fijados al SHA anterior; los cambios documentales posteriores del PR no los modifican. rc.1–rc.5 conservan sus tags/assets inmutables.

## Formulario publicado

Los mismos grupos, orden, campos visibles, etiquetas y defaults que Governance, salvo el nombre propio de Market y el token Apify junto a Select Model. AIDP administrator usa la etiqueta breve; New AIDP name comienza en `aidp_market_intelligence` y llega al `display_name` real del recurso Terraform. Workspace e identidad conservan su binding estable por OCID/clave nativa. Modelo preferido `xai.grok-4.7`, artifacts inicialmente existing y perfil Oracle AI Database 26ai · AI Lakehouse (DW).

Sesión TEST y ECPU dejan de ser campos del formulario; permanecen como parámetros avanzados con defaults vacío/4. La ausencia de UUID entrega None al instalador; Resume conserva la sesión registrada. ADMIN mantiene generador solo para base nueva. La descripción inglesa de 434 caracteres explica Apify y la comprobación de cuenta/visibilidad de Actors/builds sin ejecutar runs. El token se obtiene en Settings → API & Integrations ([fuente oficial](https://docs.apify.com/integrations/api)); esa comprobación no acredita ejecución ni acceso de pago.

## Validación y límites

[CI PR](https://github.com/jgangini/aidp-ai_market-intelligence/actions/runs/42257371768) y [CI push](https://github.com/jgangini/aidp-ai_market-intelligence/actions/runs/42257361625) aprobadas para el SHA exacto. Postflight previo: 1385 Python, ocho protocolo y quince Terraform mocks aprobados. Terraform 1.5.7 real: init/validate/fmt del módulo y catorce planes offline de controles; estos últimos no incluyen recursos OCI. Parser CTN real: schema 2, paridad visible con Governance, new/existing y rechazo de parámetros avanzados comprobados. ZIP Git exacto: 74 archivos/892333 bytes seleccionados; RECORD, CLI aislada y 40 assets canónicos verificados; 314 payloads escaneados. Revisión independiente acotada sin hallazgos accionables.

El postflight termina con error por Sentrux. No hay reglas y se conserva la baseline vacía original. Además del fallo heredado, la calidad baja 0.6621 → 0.6618 frente a rc.5. Coupling 0.73, once funciones complejas y cero ciclos/god files no cambian. El runtime Python permanece intacto; la candidata añade regresiones de contrato y conserva/deja explícita la pequeña caída de la métrica. No es una aprobación arquitectónica. Graphify existente actualizado y excluido de Git/assets; parser HCL local ausente. No se repitieron estas suites ni checks de arquitectura para este cierre documental.

El coordinador informó que Browser aún mostraba rc.5 por TTL de caché al publicar rc.6; la revisión visual específica de rc.6 sigue pendiente hasta actualizar esa referencia. Este chat no ejecutó Browser ni capturas, OCI, apply, inferencia, Apify real ni Docker. La publicación no implica merge a main ni aceptación nativa. Permisos/compute, vectorDbId y continuidad/aislamiento de checkpoints siguen pendientes. Ver [distribución](../release-handoff.md), [despliegue](../deployment.md) y [operación](../operations.md).
