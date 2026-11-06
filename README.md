# AI Market Intelligence

Agente de investigación con evidencia para Oracle AIDP, extraído de Starter Kits `3c82617fd298933995e954955841007253cc0985`. Producto independiente: paquete Python CLI/hooks, fuentes AIDP y Terraform. Se instala desde Deploy Studio; no contiene portal, API HTTP, frontend ni VM administrativa.

El candidato instala **TEST con investigación pausada**. Update/redeploy conservan identidades y respaldan el candidato previo para recuperación. No inicia inferencia, capturas Apify ni horarios. Instalación, conectores y aceptación semántica son comprobaciones diferentes; no hay publicación PROD implementada.

```powershell
uv sync --locked
uv run aidp-market package-check
./scripts/arch-postflight.ps1
```

Python 3.11; Node solo para pruebas del consumidor de streaming de referencia. Terraform requiere proveedores inicializados (`terraform -chdir=terraform init -backend=false`). El postflight no descarga proveedores ni ejecuta apply. `.oci/`, estado y recibos privados permanecen fuera de Git.

- [Despliegue y contrato Deploy Studio](docs/deployment.md)
- [Operación, límites y recuperación](docs/operations.md)
- [Estado del candidato y evidencia](docs/tasks/current.md)
- [Diagnóstico y procedencia del corte](docs/migration-assessment.md)

El repositorio privado entrega un candidato local; integración real de Deploy Studio y aceptación OCI siguen coordinadas antes de una release.
