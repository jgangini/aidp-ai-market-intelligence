# AIDP AI Market Intelligence

Paquete para instalar y mantener un agente de investigación de mercado en **Oracle AI Data Platform Workbench**. Deploy Studio configura AIDP, identidad, almacenamiento de artefactos, modelo OCI y memoria Autonomous; los hooks instalan el agente y su AI Compute en TEST.

El instalador deja la investigación **pausada**. El runtime contiene conectores web/sociales, fuentes financieras, respuestas con evidencias y recuperación por solicitud; su activación requiere configuración completa y aceptación nativa. Instalar el paquete no inicia inferencia, actores Apify ni trabajos programados.

## Componentes

| Componente | Responsabilidad |
| --- | --- |
| `terraform/` | Crear o referenciar AIDP, bucket de artefactos y Autonomous AI Lakehouse 26ai DW. |
| `terraform/hooks/` y `src/ai_market_intelligence/` | Validar identidad/destino, enlazar memoria e instalar, reanudar, actualizar o recuperar el agente. |
| `aidp/` y `package/lab.json` | Fuentes canónicas del agente, descriptores, dependencias y hashes del paquete. |
| `aidp-market` | Comprobación local del paquete y operación de una instalación existente. |

El producto se integra en Deploy Studio y Workbench. No incorpora portal propio, servidor HTTP ni VM administrativa.

## Requisitos

- Cuenta OCI con permisos para los recursos elegidos, acceso regional a AIDP y al modelo OCI seleccionado.
- Identity Domain activo y administrador elegible. El operador firma las solicitudes; seleccionar otro usuario no lo suplanta.
- Autonomous AI Lakehouse **26ai DW**, nuevo o existente, para la memoria nativa de Agent Flow.
- Token Apify para install/update/redeploy. Su comprobación es de lectura; no demuestra permiso para ejecutar actores.
- Deploy Studio con los selectores AIDP/dominio/administrador y el contrato de memoria sin wallet descritos en la [guía de despliegue](docs/deployment.md).

## Instalación

Selecciona **AIDP AI Market Intelligence** y una release en Deploy Studio. Elige crear servicios o reutilizar recursos existentes, completa los grupos del formulario y ejecuta preflight. Revisa el plan y la propiedad de los recursos antes de autorizar apply. Post-apply comprueba la asociación de memoria y publica el candidato TEST pausado.

Para una plataforma compartida con Governance, conserva su AIDP, workspace, bucket y la base de memoria ya asociada. La [guía de despliegue](docs/deployment.md) explica los parámetros, secretos y comprobaciones de ambos modos.

## Desarrollo local

El entorno local validado utiliza Python 3.11 y `uv`; Node ejecuta las pruebas del protocolo de streaming. La infraestructura admite Terraform 1.5.7 de Resource Manager; las pruebas con mocks usan 1.15.6. Terraform requiere los proveedores del lock inicializados.

```powershell
uv sync --locked
uv run aidp-market package-check
```

`package-check` verifica y compila fuentes locales sin usar credenciales. Para la validación integrada, consulta [operación y comprobaciones](docs/operations.md). Los comandos de instalación y mantenimiento sí necesitan un destino OCI autorizado.

## Guías

- [Despliegue: requisitos, parámetros y secretos](docs/deployment.md)
- [Operación: estado, Resume, actualización y recuperación](docs/operations.md)
- [Distribución y verificación de releases](docs/release-handoff.md)

El [repositorio es público](https://github.com/jgangini/aidp-ai_market-intelligence). Credenciales, Terraform state y recibos de operaciones permanecen fuera de Git. La publicación PROD, delete y transferencia de state no están implementadas. La asociación real de memoria, continuidad de checkpoints, permisos y entorno nativo requieren aceptación OCI; las pruebas con mocks no la sustituyen.
