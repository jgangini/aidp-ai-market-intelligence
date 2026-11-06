# Compatibilidad con Terraform 1.5.7

El contrato de despliegue admite Terraform 1.5.7, CLI de la línea 1.5.x soportada por [OCI Resource Manager](https://docs.oracle.com/en-us/iaas/Content/ResourceManager/Reference/terraformversions.htm). Las pruebas con `mock_provider` utilizan 1.15.6; no forman parte del runtime de despliegue.

## Comprobaciones locales

- Binario Windows amd64 1.5.7 de releases.hashicorp.com, ZIP verificado con SHA256 oficial `0cf9f41ba11b46f1fb6251952df98bf9c37ec294f26be7091b4909850659b592`.
- Reproducción del rechazo inicial por `required_version >= 1.7.0` con ese binario.
- Tras ajustar únicamente el mínimo a `>= 1.5.7`: init con backend deshabilitado, validate y formato de la configuración completa aprobados. Se usó un mirror local de OCI 8.28.0/time 0.14.2, sin registro remoto ni descargas de proveedores.
- Diez planes offline con 1.5.7: declaraciones de variables, recurso `terraform_data.memory_inputs` y precondiciones del output AIDP copiados literalmente de las fuentes. La fixture usa solo el proveedor builtin; no representa un plan completo de recursos OCI.

Los casos aceptan destinos nuevos y existentes y rechazan ADMIN ausente o inválido, memoria nueva durante mantenimiento, referencias existentes obsoletas, workspace ausente, versión de base incompatible y ECPU fraccional. No se modificaron las validaciones de variables, precondiciones o postcondiciones.

La CI ejecuta init/formato/validate con 1.5.7 y 1.15.6 y los once casos con mocks en 1.15.6. Las comprobaciones locales y sus fixtures quedan en `.local/terraform157-compatibility-evidence.json`, fuera de Git.

El postflight local aprobó 1382 pruebas Python, ocho del protocolo y once Terraform con mocks. Terminó con el gate Sentrux heredado fallido, sin reglas locales: calidad 0.6621, coupling 0.73 y once funciones complejas, sin regresión frente al estado previo (calidad 0.6620); cero ciclos y god files. El preflight se detuvo para conservar la baseline original y no se reemplazó. La corrección Terraform conserva las validaciones. El ajuste asociado del formulario resuelve el selector de bucket en el hook y añade sus regresiones; se actualizó el grafo Graphify existente, sin parser HCL y sin instalar dependencias.

No se ejecutaron backend remoto, proveedor OCI en plan, apply, Browser ni Docker. La asociación de memoria existente mantiene sus comprobaciones y límites; este ajuste no acredita aceptación OCI ni incorpora una alternativa de recibos compartidos.
