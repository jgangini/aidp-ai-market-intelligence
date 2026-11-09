# Operación y recuperación

## Configuración e identidad

Deploy Studio administra Terraform y llama a los hooks del paquete seleccionado. El formulario instala o reanuda; la CLI administra una instalación existente. El operador firma las solicitudes OCI. `admin_user_ocid` identifica al propietario autorizado y no sustituye la identidad firmante.

Los settings privados incluyen destino AIDP/workspace, región, bucket/namespace, dominio, administrador y modelo. No se publican configuraciones reales, `.oci`, claves, tokens, state ni recibos. El token Apify se entrega por archivo protegido o Credential Store, nunca como argumento literal.

```powershell
uv run aidp-market --help
uv run aidp-market status --settings .local/market-settings.json
```

`status` observa y revalida permisos; no instala ni activa investigación. La CLI presupone que la infraestructura y la asociación de memoria ya existen.

## Instalación y estado

El recibo individual está en `/Workspace/.artifact/ai_market_intelligence/.control/installer.json`. El código está en la carpeta hermana `agents/`, con entrada y dependencias directas y módulos en `core/`. Los respaldos de mantenimiento quedan fuera del subtree ejecutable.

La instalación recorre verificación, credenciales, compute, archivos, agente, permisos y TEST. Resume conserva operación, sesión y recursos confirmados. TEST ACTIVE e investigación pausada son estados compatibles: el probe pausado comprueba identidad, revisión y sesión sin inferencia ni actores; no demuestra calidad de investigación ni continuidad de checkpoints.

## Mantenimiento

| Acción | Comportamiento |
| --- | --- |
| `update` | Aplica el paquete o configuración seleccionados con respaldo y verificación. |
| `redeploy` | Redespliega exactamente el paquete y la configuración instalados; otro candidato requiere `update`. |
| `rollback` | Restaura el snapshot respaldado y verifica TEST. |
| `activate-test` | Activa investigación mediante un binding completo sobre el mantenimiento existente. |

Las operaciones de escritura requieren `--apply` y un UUID canónico `--operation-id`. Mantener el mismo UUID al reanudar. Install/update/redeploy reciben `--token-file`; rollback no necesita token ni disponibilidad de Apify. El binding activo se conserva en actualizaciones posteriores.

El journal congela configuración y hashes, respalda bytes antes de escribir y admite sólo bytes conocidos. El refresco se registra antes de enviarse. Una operación pendiente no se anuncia como completada: hay que observar o reanudar su journal. Un update que termina restaurado sigue siendo un update fallido; un rollback solicitado puede completar correctamente.

## Actualizar TEST conservando PROD

El mantenimiento bloquea por defecto una instalación con PROD. La ruta explícita utiliza `--preserve-prod --plugin-scripts <directorio-del-plugin>` y el adaptador `PluginTestDeployment`. Mantiene el lock propietario y usa realmente `native sync` y `native test-refresh` del plugin; no crea otro ejecutor de despliegue.

La operación fija el snapshot publicado y verifica agente, compute, endpoint y configuración durante actualización y commit. No modifica ni elimina PROD. Una publicación es un snapshot separado del código de desarrollo: [Oracle Agent Flow deployment](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/agent-flow-deployment.html).

Los recibos del plugin y del instalador conservan su procedencia. Una respuesta perdida se reconcilia contra el intent existente antes de repetir un POST. Los efectos inciertos del modelo guardan el tipo de excepción y, si el proveedor lo expone, el código HTTP de error; excluyen mensajes, prompts y credenciales. El estado incierto no prueba por sí solo un fallo HTTP: también puede proceder del procesamiento de la salida estructurada. La confirmación exige TEST ACTIVE, revisión servida y probe correctos. Si falla de forma confirmada, el rollback restaura el respaldo y refresca TEST.

## Activar investigación

`activate-test` recibe `--research-binding`, settings privados, UUID y `--apply`. Reutiliza respaldo, lock, journal, refresco y rollback. No crea compute, credenciales o sesiones ni exige exportar el token ya instalado.

El binding debe conservar identidad, Credential Store, modelo, región/compartimento, almacenamiento y generación. Fuentes, idioma, guardrails y perfil observado del runtime son explícitos. `candidate_revision` selecciona el hash del paquete canónico. Un binding distinto no puede reanudar la misma operación; una respuesta incierta no declara activación.

Después, `inspect_runtime` comprueba código y modo, y `check_services` comprueba servicios sin iniciar investigación. Una consulta real sigue siendo necesaria para comprobar el flujo funcional.

## Publicar y registrar para Agent Hub

La publicación se realiza con la CLI AIDP Agent Flow LangGraph instalada. Comprobar su ayuda y el proyecto privado antes de ejecutar; no forma parte del comando `aidp-market install` ni de una actualización TEST implícita.

1. Verificar layout exacto y aceptar el candidato servido en TEST bajo sus criterios declarados.
2. Planificar y aplicar `native publish` con autorización de publicación, manteniendo el lock propietario.
3. Observar y reconciliar el intent hasta confirmar la revisión publicada y el endpoint.
4. Inspeccionar la tarjeta y registrar o adoptar la identidad exacta en AI Registry.
5. Comprobar `registry readiness` y abrir Agent Hub mediante el enlace verificado.
6. Enviar una consulta real en el cliente y observar progreso, bloques y resultado final.

`registry portal-link` sólo resuelve navegación; `registry readiness` exige publicación y registro ACTIVE. Ninguno acredita que el chat haya atendido una pregunta. Un agente reemplazado no hereda el registro del anterior. Tras republicar hay que comprobar la versión y el contrato registrados.

La disponibilidad del endpoint, el registro, el streaming del cliente y la calidad factual son verificaciones distintas. Una entrega parcial o fallida en el chat no se presenta como investigación completada por el hecho de que el despliegue esté activo.

## Locks y recuperación de incertidumbre

El lock Object Storage es por plataforma/workspace y bucket: creación condicional y liberación con ETag exacto, sin expiración ni takeover automático. Configuraciones que usan buckets distintos no comparten exclusión; conservar el binding y serializar operaciones del workspace.

`release-lock` requiere ETag observado, `--confirm-no-running-hook` y `--apply`. Nunca liberar por nombre ni borrar recibos para desbloquear. Cuando una escritura queda incierta, conservar bloqueo e intent y observar su resultado; no iniciar otra operación para sustituirla.

La habilitación de memoria utiliza un intent en el bucket y comprueba READY con la base exacta. Un rechazo confirmado permite corregir y reanudar; timeout o resultado ambiguo conservan el intent enviado hasta reconciliación. No cambiar la base asociada para recuperar un agente. El mantenimiento conserva memoria y checkpoints.

## Cambios de estructura

`native inspect-binding` observa por separado ubicación CODE y archivos ejecutables. `native inspect-layout` verifica inventario completo. Cambiar entry/dependencies no mueve los metadatos ni el recibo. Los comandos `migrate` y `rebuild-development` son operaciones administrativas explícitas con contratos propios; no sustituyen una actualización ordinaria.

Las rutas e inventarios de recuperación deben coordinarse con el instalador antes de retirar archivos. El registro permanece por artefacto, fuera de `agents/`. Un árbol ejecutable sobrante impide declarar el layout cerrado aunque TEST responda.

## Límites operativos

La CLI del producto no ofrece delete, transferencia de state ni publicación PROD directa; la publicación del agente corresponde al plugin. La asociación READY de memoria no demuestra continuidad o aislamiento de checkpoints. Una prueba local no acredita IAM, red, dependencias ni streaming nativos. Las incertidumbres deben mantenerse visibles hasta su reconciliación.
